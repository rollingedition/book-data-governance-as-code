"""Governing the data the platform's course assistant learns from. The corpus is the review_corpus pipeline's SQL, run
through the enforcement layer (lib/enforce.py) for the one purpose the access policy knows, "ai-training", so only
reviews whose author consented come back, with contact details already removed. Then every name the detector finds is
replaced by a keyed pseudonym, exact and near-duplicate texts are removed, every record keeps where it came from, and
a Croissant dataset card is generated from the run. The settings are in governance/ai_data.toml, and so are the answers
the code needs to legal questions, which the platform's legal team gives."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import tomllib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path

import mlcroissant as mlc
import pyarrow as pa
import pyarrow.parquet as pq
import sqlglot
from datasketch import MinHash, MinHashLSH
from openlineage.client.uuid import generate_static_uuid
from presidio_analyzer import AnalyzerEngine, PatternRecognizer
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig
from pyiceberg.catalog import Catalog

from lib import lineage, metadata, pii, pipelines
from lib.enforce import Enforcer
from lib.platform import load, scratch
from lib.warehouse import connect

SETTINGS = tomllib.loads((Path(__file__).resolve().parent.parent / "governance" / "ai_data.toml").read_text(encoding="utf-8"))
BUILD, NAMES, DUPLICATES, CARD = SETTINGS["build"], SETTINGS["names"], SETTINGS["duplicates"], SETTINGS["card"]
LEGAL = SETTINGS["legal"]               # each answer a placeholder until the platform's legal team gives its own
SOURCE = "learning.reviews"            # the table every record comes from; a corpus that mixes sources says so per record
OUTPUT = "ai.review_corpus"
CORPUS_FILE = "review_corpus.parquet"
# The book's key for the corpus's pseudonyms, fixed so outputs never change. It isn't the enforcement layer's key, and
# the construction isn't the layer's either. A real one is read from a secret manager.
CORPUS_KEY = b"corpus-example-key-not-a-secret"
CROISSANT = "http://mlcommons.org/croissant/1.1"
RAI = "http://mlcommons.org/croissant/RAI/1.0"
PROV = "http://www.w3.org/ns/prov#"


class CorpusError(Exception):
    """A build the corpus job refuses: one whose rows the legal team's consent answer didn't decide."""


# ---------------------------------------------------------------- consent: read through the enforcement layer


def query() -> str:
    """The SELECT of pipelines/review_corpus.sql, in review order: the same SQL the plain pipeline runs."""
    select = sqlglot.parse_one(pipelines.sql(BUILD["job"]), read="duckdb").expression
    return select.order_by("r.review_id").sql("duckdb")


def read(enforcer: Enforcer, principal: str = BUILD["principal"], purpose: str | None = BUILD["purpose"]) -> list[dict]:
    """The rows the job gets: the pipeline's SQL, run through the enforcement layer as the job's principal, for its
    purpose."""
    relation = enforcer.sql(query(), principal, purpose)
    return [dict(zip(relation.columns, row)) for row in relation.fetchall()]


# ---------------------------------------------------------------- personal data in the text


def known_names(table: str = NAMES["table"]) -> list[str]:
    """Every word of every name in a table's `full_name`: by default the instructors', the people reviews talk about.
    Generated from the table, never typed. This is the governance team's job (NAMES["built_by"]), and it reads the
    table with the platform's own access: analyst-1, whom the corpus job runs as, may not read crm.instructors. The
    list is personal data itself, so the corpus job keeps it inside the run, never in the corpus or its card."""
    return sorted({word for name in load(table).column(NAMES["column"]).to_pylist() for word in name.split()})


def detector(names: list[str] | None = None, match_case: bool = False) -> AnalyzerEngine:
    """The platform's detector (lib.pii.platform_detector), plus a recognizer for the names the platform knows, as a
    Presidio deny list: whole words, in any case, as Presidio's default flags match them, or only as written."""
    flags = re.DOTALL | re.MULTILINE | (0 if match_case else re.IGNORECASE)
    engine = pii.platform_detector()
    engine.registry.add_recognizer(PatternRecognizer(supported_entity="PERSON", name="KnownNamesRecognizer",
                                                     deny_list=known_names() if names is None else names,
                                                     global_regex_flags=flags))
    return engine


def pseudonym(entity: str, value: str, key: bytes = CORPUS_KEY) -> str:
    """A keyed pseudonym, HMAC-SHA256 of the entity type and the value: the same value, in any case, always gets the
    same pseudonym, and nobody without the key can compute one from a guessed name."""
    digest = hmac.new(key, f"{entity}:{value.casefold()}".encode(), hashlib.sha256).hexdigest()
    return f"<{entity}_{digest[:SETTINGS['pseudonyms']['hex_digits']]}>"


@cache
def anonymizer() -> AnonymizerEngine:
    """Presidio's anonymizer, made once and shared: it holds no state between calls."""
    return AnonymizerEngine()


def pseudonymize(engine: AnalyzerEngine, text: str, key: bytes = CORPUS_KEY) -> tuple[str, list[str]]:
    """The text with everything the detector finds replaced by its keyed pseudonym, by Presidio's anonymizer with a
    custom operator, and the entity types it replaced."""
    found = engine.analyze(text, language="en")
    operators = {r.entity_type: OperatorConfig("custom", {"lambda": lambda value, e=r.entity_type: pseudonym(e, value, key)})
                 for r in found}
    result = anonymizer().anonymize(text, found, operators)
    return result.text, sorted(item.entity_type for item in result.items)


def labelled(source: str = "reviews") -> dict[int, set[str]]:
    """The labelled personal data values of each row of a source, from data/platform/pii_spans.parquet."""
    values: dict[int, set[str]] = {}
    for span in pii.truth():
        if span.source == source:
            values.setdefault(span.row_id, set()).add(pii.text_of(span))
    return values


def unlabelled(engine: AnalyzerEngine, texts: dict[int, str], source: str = "reviews") -> Counter:
    """What a detector finds in these texts that isn't a labelled value of its row, case folded: its false positives."""
    truth = labelled(source)
    return Counter(text[r.start:r.end].casefold() for row_id, text in texts.items()
                   for r in engine.analyze(text, language="en") if text[r.start:r.end] not in truth.get(row_id, set()))


def read_pseudonymized(enforcer: Enforcer, names: list[str] | None = None) -> tuple[list[dict], Counter]:
    """The rows the job reads, with each review's text pseudonymized by the corpus's detector, given the governance
    team's list of names, and how many values of each entity type it replaced."""
    rows, engine, replaced = read(enforcer), detector(names), Counter()
    for row in rows:
        row["body"], entities = pseudonymize(engine, row["body"])
        replaced.update(entities)
    return rows, replaced


def left(texts: dict[int, str], source: str = "reviews") -> dict[str, tuple[int, int]]:
    """For each entity type in the labelled truth (data/platform/pii_spans.parquet): how many labelled values these
    rows of the source held, and how many of them are still in the texts given, by row ID."""
    counts: dict[str, tuple[int, int]] = {}
    for span in pii.truth():
        if span.source == source and span.row_id in texts:
            placed, still = counts.get(span.entity, (0, 0))
            counts[span.entity] = (placed + 1, still + (pii.text_of(span) in texts[span.row_id]))
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------- duplicates


def normalized(text: str) -> str:
    """The text as duplicates are compared: case folded, runs of white space made one space."""
    return " ".join(text.casefold().split())


def shingles(text: str, size: int = DUPLICATES["shingle_characters"]) -> set[str]:
    """Every run of `size` characters in the normalized text."""
    text = normalized(text)
    return {text[i:i + size] for i in range(max(len(text) - size + 1, 1))}


PSEUDONYM = re.compile(rf"<([A-Z_]+?)_[0-9a-f]{{{SETTINGS['pseudonyms']['hex_digits']}}}>")


def placeholders(text: str) -> str:
    """The text with each keyed pseudonym made a plain placeholder: <PERSON_1a2b3c4d> becomes <PERSON>. Two distinct
    texts that are the same this way differ only in which names they hold."""
    return PSEUDONYM.sub(r"<\1>", text)


def jaccard(a: str, b: str) -> float:
    """The exact Jaccard similarity of two texts' shingles: shared shingles over all shingles."""
    first, second = shingles(a), shingles(b)
    return len(first & second) / len(first | second)


def minhash(text: str, scheme: str = DUPLICATES["scheme"]) -> MinHash:
    """A seeded MinHash of the text's shingles, with the permutation scheme written out."""
    sketch = MinHash(num_perm=DUPLICATES["num_perm"], seed=DUPLICATES["seed"], scheme=scheme)
    sketch.update_batch([shingle.encode() for shingle in sorted(shingles(text))])
    return sketch


@dataclass
class NearDuplicates:
    """What near_duplicates found: the pairs LSH returned, the pairs confirmed, and the bands LSH chose."""
    candidates: set[tuple[int, int]]     # pairs of texts LSH returned, by position
    confirmed: set[tuple[int, int]]      # of those, the pairs whose exact Jaccard similarity reaches the threshold
    bands: int                           # LSH's b and r: b bands of r rows of each signature
    rows: int


def near_duplicates(texts: list[str], threshold: float = DUPLICATES["threshold"],
                    candidate_threshold: float = DUPLICATES["candidate_threshold"],
                    scheme: str = DUPLICATES["scheme"]) -> NearDuplicates:
    """Candidate pairs from MinHash LSH tuned for `candidate_threshold`, then the ones whose exact Jaccard similarity
    reaches `threshold`. LSH is approximate both ways: it returns pairs under its threshold, and misses some above it."""
    index = MinHashLSH(threshold=candidate_threshold, num_perm=DUPLICATES["num_perm"])
    sketches = [minhash(text, scheme) for text in texts]
    for position, sketch in enumerate(sketches):
        index.insert(position, sketch)
    candidates = {(min(i, j), max(i, j)) for i, sketch in enumerate(sketches) for j in index.query(sketch) if j != i}
    confirmed = {(i, j) for i, j in candidates if jaccard(texts[i], texts[j]) >= threshold}
    return NearDuplicates(candidates, confirmed, index.b, index.r)


def exact_distinct(rows: list[dict], text: str = "body") -> list[dict]:
    """The first row, by review ID, of each group of rows whose normalized text has the same SHA-256."""
    first: dict[str, dict] = {}
    for row in sorted(rows, key=lambda row: row["review_id"]):
        first.setdefault(hashlib.sha256(normalized(row[text]).encode()).hexdigest(), row)
    return list(first.values())


def duplicate_groups(rows: list[dict], text: str = "body") -> tuple[list[list[dict]], dict[str, int]]:
    """The rows in groups of duplicates, each group in review order, so its first row is the one kept. Exact duplicates
    first, by SHA-256 of the normalized text; then near-duplicates among the distinct texts, grouped through the pairs
    `near_duplicates` confirms, leaving out the pairs that differ only in a name unless the settings count those as
    duplicates. Returns the groups and what each step found and removed."""
    exact: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda row: row["review_id"]):
        exact.setdefault(hashlib.sha256(normalized(row[text]).encode()).hexdigest(), []).append(row)
    distinct = list(exact.values())
    texts = [members[0][text] for members in distinct]
    near = near_duplicates(texts)
    name_only = {(i, j) for i, j in near.confirmed if placeholders(texts[i]) == placeholders(texts[j])}
    merged = near.confirmed if DUPLICATES["name_only_pairs_are_duplicates"] else near.confirmed - name_only
    group = list(range(len(distinct)))           # each text's group, named by its first member
    def root(i: int) -> int:
        while group[i] != i:
            i = group[i]
        return i
    for i, j in sorted(merged):
        a, b = root(i), root(j)
        group[max(a, b)] = min(a, b)
    groups: dict[int, list[dict]] = {}
    for i, members in enumerate(distinct):
        groups.setdefault(root(i), []).extend(members)
    found = [sorted(members, key=lambda row: row["review_id"]) for members in groups.values()]
    return found, {"exact duplicates removed": len(rows) - len(distinct), "near-duplicate pairs from LSH": len(near.candidates),
                   "near-duplicate pairs confirmed": len(near.confirmed),
                   "of them, differing only in a name": len(name_only),
                   "near-duplicates removed": len(distinct) - len(found)}


def deduplicate(rows: list[dict], text: str = "body") -> tuple[list[dict], dict[str, int]]:
    """One row for each group of duplicates (`duplicate_groups`), the one with the lowest review ID. Returns the rows
    kept and what each step removed."""
    groups, removed = duplicate_groups(rows, text)
    return [members[0] for members in groups], removed


# ---------------------------------------------------------------- the build, with provenance on every record


SCHEMA = pa.schema([("review_id", pa.int64()), ("course_id", pa.int64()), ("course_title", pa.string()),
                    ("rating", pa.int32()), ("body", pa.string()),
                    ("source_table", pa.string()), ("job", pa.string()), ("run_id", pa.string())])


@dataclass
class Build:
    records: pa.Table                   # the corpus: one row per text kept, with its provenance
    counts: dict[str, int]              # what each step read, replaced and removed
    left: dict[str, tuple[int, int]]    # labelled values in the rows read, and how many are still in their text
    run_id: str                         # the OpenLineage run that wrote it
    at: datetime                        # the run's nominal time
    statement: str                      # the query as the enforcement layer ran it, as the job's CREATE TABLE
    derived_from: list[str]             # the tables the corpus's values come from: those the pipeline's own SQL reads
    filtered_by: list[str]              # the columns whose filters decided which rows it holds
    folder: Path                        # the corpus file, the lineage events and the card
    influenced_by: list[str]            # the tables the layer's rewrite adds, which only decide which rows it holds
    names: int                          # words in the governance team's list of names that the detector used


def run_id(at: datetime = lineage.NIGHTLY, job: str = BUILD["job"]) -> str:
    """A run's ID, derived as lib.lineage derives it: from the run's nominal time and the job's name."""
    return str(generate_static_uuid(at, job.encode()))


def build(catalog: Catalog, folder: Path | None = None, at: datetime = lineage.NIGHTLY,
          names: list[str] | None = None) -> Build:
    """Build the corpus as the nightly run at `at`, write it to the catalog as ai.review_corpus and to a Parquet file,
    and send its lineage: the query as the enforcement layer ran it, so the consent filter is among its inputs. The
    build refuses to run unless that filter is on the column the legal team's consent answer names. `names` is the
    governance team's list of names; by default its job (`known_names`) builds it."""
    folder = Path(folder) if folder else scratch("corpus-")     # removed when the process exits
    enforcer = Enforcer(catalog)
    statement = f"create table {OUTPUT} as {enforcer.rewrite(query(), BUILD['principal'], BUILD['purpose'])}"
    _, rows_shaped_by = lineage.column_lineage(statement, lineage.schema(catalog))
    filters = sorted({i.source for i in rows_shaped_by if i.subtype == "FILTER"})
    if LEGAL["consent"]["answer"] not in filters:
        raise CorpusError(f"the enforcement layer doesn't filter on {LEGAL['consent']['answer']}, the consent answer "
                          f"in governance/ai_data.toml: it filters on {', '.join(filters) or 'nothing'}")
    names = known_names() if names is None else names
    run, done = run_id(at), {}

    def work() -> None:
        rows, replaced = read_pseudonymized(enforcer, names)
        done["left"] = left({row["review_id"]: row["body"] for row in rows})
        kept, removed = deduplicate(rows)
        stored = enforcer.connection.sql(f"select count(*) from {SOURCE}").fetchall()[0][0]
        done["counts"] = {f"reviews in {SOURCE}": stored, "reviews read": len(rows),
                          **{f"{entity} values replaced": n for entity, n in sorted(replaced.items())},
                          **removed, "records kept": len(kept)}
        provenance = {"source_table": SOURCE, "job": f"{lineage.JOBS}/{BUILD['job']}", "run_id": run}
        done["records"] = pa.Table.from_pylist([row | provenance for row in kept], schema=SCHEMA)
        pq.write_table(done["records"], folder / CORPUS_FILE)
        catalog.create_namespace_if_not_exists(OUTPUT.split(".")[0])
        if catalog.table_exists(OUTPUT):
            catalog.drop_table(OUTPUT)
        catalog.create_table(OUTPUT, schema=SCHEMA).append(done["records"])

    lineage.record(lineage.client(folder / "events.jsonl"), BUILD["job"], statement, catalog, work, at)
    # The layer's rewrite adds row filters and masks, never a source of values: the values come from the tables the
    # pipeline's own SQL reads, and every table the rewrite adds only decides which rows exist.
    written = lineage.tables_read(pipelines.sql(BUILD["job"]))
    added = sorted(set(lineage.tables_read(statement)) - set(written))
    return Build(done["records"], done["counts"], done["left"], run, at, statement, written, filters, folder, added,
                 len(names))


def consenting(catalog: Catalog, review_ids: list[int]) -> int:
    """How many of these reviews' authors consent today: each review joined through learning.reviews to its author,
    with the platform's own access, and the column the legal team's consent answer names tested IS TRUE, so an
    unknown answer is no. A training run checks its build this way before it starts, and refuses a build with any
    record that doesn't pass."""
    table, column = LEGAL["consent"]["answer"].rsplit(".", 1)
    return connect(catalog).execute(f"""select count(*) from learning.reviews r join {table} a using (learner_id)
                                        where r.review_id in (select unnest($ids)) and a.{column} is true""",
                                    {"ids": review_ids}).fetchall()[0][0]


# ---------------------------------------------------------------- the dataset card


TYPES = {pa.int64(): mlc.DataType.INTEGER, pa.int32(): mlc.DataType.INTEGER, pa.string(): mlc.DataType.TEXT}
# What a Croissant 1.1 dataset must declare, and what the platform requires of its cards besides.
REQUIRED = ["@context", "@type", "conformsTo", "description", "license", "name", "url", "creator", "datePublished"]
PLATFORM_REQUIRED = ["version", "rai:dataCollection", "rai:personalSensitiveInformation", "rai:dataPreprocessingProtocol",
                     "rai:dataLimitations", "rai:dataUseCases", "prov:wasDerivedFrom", "prov:wasGeneratedBy"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def card(built: Build, described: dict[str, metadata.Table] | None = None,
         conforms_to: list[str] | None = None) -> tuple[dict, list[str]]:
    """The corpus's Croissant card, generated from the run: its counts, its file's SHA-256, its schema and its lineage,
    with the creator taken from the output table's owner in governance/metadata.toml and the licence from the legal
    team's answer. `conforms_to` defaults to Croissant 1.1 and the RAI vocabulary 1.0, written out. The version is the
    build's date as its major number: Croissant's versioning counts removed records as a major change, and any nightly
    build may remove some. Returns the JSON-LD and mlcroissant's own warnings."""
    described = metadata.load() if described is None else described
    owner = described.get(OUTPUT)
    file = mlc.FileObject(id=CORPUS_FILE, name=CORPUS_FILE, content_url=CORPUS_FILE,
                          encoding_formats=["application/x-parquet"], sha256=sha256(built.folder / CORPUS_FILE))
    fields = [mlc.Field(id=f"reviews/{f.name}", name=f.name, data_types=[TYPES[f.type]],
                        source=mlc.Source(file_object=CORPUS_FILE, extract=mlc.Extract(column=f.name)))
              for f in built.records.schema]
    n = built.counts
    placed, still = map(sum, zip(*built.left.values())) if built.left else (0, 0)
    found = ", ".join(f"{k.split(' values')[0]} {v:,}" for k, v in n.items() if k.endswith("values replaced"))
    apart = ("" if DUPLICATES["name_only_pairs_are_duplicates"] else
             f"; {n['of them, differing only in a name']:,} pairs that differ only in a name kept apart")
    provenance = {           # Croissant 1.1 recommends PROV-O, written through mlcroissant's extra_properties
        "prov:wasDerivedFrom": [{"@id": f"{lineage.NAMESPACE}/{table}"} for table in built.derived_from],
        "prov:wasInfluencedBy": [{"@id": f"{lineage.NAMESPACE}/{table}"} for table in built.influenced_by],
        "prov:wasGeneratedBy": {"@type": "prov:Activity", "@id": f"urn:uuid:{built.run_id}",
                                "prov:label": f"{lineage.JOBS}/{BUILD['job']}"}}
    meta = mlc.Metadata(
        name=CARD["name"], description=CARD["description"], url=CARD["url"], license=[LEGAL["licence"]["answer"]],
        creators=[mlc.Organization(name=owner.owner)] if owner else [], date_published=built.at,
        version=f"{built.at:%Y%m%d}.0.0", extra_properties=provenance,
        conforms_to=[CROISSANT, RAI] if conforms_to is None else conforms_to,
        distribution=[file], record_sets=[mlc.RecordSet(id="reviews", name="reviews", fields=fields)],
        data_collection=(f"Course reviews the platform's learners wrote, from {SOURCE}, read through the enforcement layer "
                         f"for the purpose {BUILD['purpose']}: the reviews whose author's {LEGAL['consent']['answer']} "
                         f"is true."),
        personal_sensitive_information=[
            f"Values replaced by keyed pseudonyms: {found}.",
            "Emails and phone numbers replaced by [email] and [phone] by the enforcement layer's redact_contacts mask.",
            f"Names found with a list of {built.names:,} words from {NAMES['table']}, built by the governance team's job "
            f"{NAMES['built_by']}; the list isn't part of the corpus.",
            f"Measured against labelled spans: {still:,} of {placed:,} labelled values left in the text."],
        data_preprocessing_protocol=[
            f"Read {n['reviews read']:,} of {n[f'reviews in {SOURCE}']:,} reviews, filtered on "
            f"{', '.join(built.filtered_by)}.",
            f"Replaced personal data with keyed pseudonyms ({found}).",
            f"Removed {n['exact duplicates removed']:,} exact duplicates and {n['near-duplicates removed']:,} "
            f"near-duplicates (MinHash LSH, Jaccard similarity of {DUPLICATES['threshold']} or more, confirmed "
            f"exactly{apart}).",
            f"Kept {n['records kept']:,} records, each with its source table, review ID, job and run."],
        data_limitations=[
            "Pseudonymized, not anonymized: names and contacts are replaced as detected, and the review ID leads back "
            "to its author.",
            "Reviews by learners never asked about AI training, whose consent is unknown, are left out, so the corpus "
            "holds reviews only by learners who signed up after the platform began asking."],
        data_use_cases=CARD["use_cases"])
    jsonld = meta.to_json()
    jsonld["@context"]["prov"] = PROV          # mlcroissant writes the properties, but adds no prefix for them
    return jsonld, sorted(meta.ctx.issues.warnings)


def write_card(jsonld: dict, folder: Path) -> Path:
    """Write a card beside the files it describes."""
    path = folder / "croissant.json"
    path.write_text(json.dumps(jsonld, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def card_problems(jsonld: dict, folder: Path) -> list[str]:
    """What the card misses: a property Croissant 1.1 requires or the platform requires, a conformsTo other than
    Croissant 1.1, and a file that isn't there or whose SHA-256 isn't the one the card states."""
    found = [f"{name}: missing" for name in REQUIRED + PLATFORM_REQUIRED if not jsonld.get(name)]
    conforms = jsonld.get("conformsTo")
    if conforms and CROISSANT not in ([conforms] if isinstance(conforms, str) else conforms):
        found.append(f"conformsTo: {conforms}, not {CROISSANT}")
    for file in jsonld.get("distribution", []):
        path = folder / file["contentUrl"]
        if not path.exists():
            found.append(f"{file['contentUrl']}: no such file")
        elif sha256(path) != file.get("sha256"):
            found.append(f"{file['contentUrl']}: its SHA-256 isn't the one the card states")
    return found
