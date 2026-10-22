"""Finding personal data on the platform: rules on column names and types, seeded samples of a column's values,
Presidio's pattern recognizers run with no NLP model, and measurement of any detector's spans against the labelled
truth in data/platform/pii_spans.parquet. The rules, the sampling and the recognizers' thresholds are settings in
governance/pii.toml, reviewed like the rest of the platform's governance."""
from __future__ import annotations

import json
import re
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import cache

import duckdb
import phonenumbers
import pyarrow.parquet as pq
import tldextract
from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NoOpNlpEngine
from presidio_analyzer.predefined_recognizers import PhoneRecognizer
from pyiceberg.catalog import Catalog
from pyiceberg.types import StringType

from lib.metadata import stricter
from lib.platform import DATA, load


def suffix_list_urls() -> tuple[str, ...]:
    """The URLs tldextract, and so Presidio's email recognizer, fetches the Public Suffix List from on first use."""
    return tuple(tldextract.tldextract.TLD_EXTRACTOR.suffix_list_urls)


# lib/__init__.py empties that list before any lib module loads, and replaces tldextract's shared extractor when
# something imported tldextract first. If neither held, the email recognizer would reach the network: fail here instead.
if suffix_list_urls():
    raise RuntimeError("tldextract would fetch the Public Suffix List: the setting in lib/__init__.py didn't hold")

RECORDED = DATA.parent / "recorded"
SETTINGS = tomllib.loads((DATA.parent.parent / "governance" / "pii.toml").read_text(encoding="utf-8"))

# ---------------------------------------------------------------- columns

NAME_RULES = [(rule["pattern"], rule["entity"]) for rule in SETTINGS["columns"]["name_rules"]]   # first match wins
CLASSIFICATION = SETTINGS["classification"]      # entity -> what finding it means for a column's classification
FLOOR = SETTINGS["columns"]["floor"]             # proposed for a column with no evidence; only a person sets `public`
ROWS, SEED = SETTINGS["columns"]["sample_rows"], SETTINGS["columns"]["sample_seed"]


def by_name(column: str) -> str | None:
    """The entity a column's name suggests, or None."""
    return next((entity for pattern, entity in NAME_RULES if re.search(pattern, column.lower())), None)


def sample(con: duckdb.DuckDBPyConnection, table: str, column: str, rows: int = ROWS, seed: int = SEED) -> list[str]:
    """Up to `rows` non-null values of a column, drawn by a seeded reservoir sample. DuckDB promises the same sample
    for the same seed only on one thread, so this sets the connection to one thread."""
    con.execute("set threads = 1")
    return [value for (value,) in con.sql(
        f"select {column} from (select {column} from {table} where {column} is not null) "
        f"using sample reservoir({rows} rows) repeatable ({seed})").fetchall()]


@dataclass(frozen=True)
class Finding:
    """What the rules and the sample say about one column."""
    table: str
    column: str
    field_id: int
    named: str | None                                   # the entity its name suggests
    sampled: int                                        # values sampled; 0 for a column whose type isn't text
    found: Counter = field(default_factory=Counter)     # entity -> sampled values it was found in

    @property
    def classification(self) -> str:
        proposed = FLOOR
        for entity in ([self.named] if self.named else []) + list(self.found):
            proposed = stricter(proposed, CLASSIFICATION.get(entity, FLOOR))
        return proposed


def classify_field(con: duckdb.DuckDBPyConnection, detector: AnalyzerEngine, table: str, name: str, field_id: int,
                   text: bool, rows: int = ROWS) -> Finding:
    """One column's finding: its name always, and a sample of its values when its type is text."""
    values = sample(con, table, name, rows) if text else []
    found = Counter(entity for value in values for entity in {e for _, _, e, _ in detect(detector, value)})
    return Finding(table, name, field_id, by_name(name), len(values), found)


def classify(catalog: Catalog, con: duckdb.DuckDBPyConnection, detector: AnalyzerEngine, rows: int = ROWS,
             only: dict[str, set[int]] | None = None) -> list[Finding]:
    """A finding for every column of every table in the catalog, or only for the field IDs given per table."""
    findings = []
    for (namespace,) in sorted(catalog.list_namespaces()):
        for _, name in sorted(catalog.list_tables(namespace)):
            table = f"{namespace}.{name}"
            for f in catalog.load_table(table).schema().fields:
                if only is None or f.field_id in only.get(table, set()):
                    findings.append(classify_field(con, detector, table, f.name, f.field_id,
                                                   isinstance(f.field_type, StringType), rows))
    return findings


# ---------------------------------------------------------------- Presidio

THRESHOLDS = SETTINGS["recognizers"]             # the recognizers the platform keeps, each with its own threshold


def countries() -> list[str]:
    """The countries the platform's learners live in, from crm.learners."""
    return sorted(set(load("crm.learners").column("country").to_pylist()))


def detector(thresholds: dict[str, float] | None = None, phone_regions: list[str] | None = None,
             default_threshold: float | None = None) -> AnalyzerEngine:
    """Presidio's analyzer with no NLP model, so nothing is downloaded. Without `thresholds` it runs every predefined
    recognizer, as Presidio's defaults do; with them, only the recognizers named, each with its own threshold.
    `phone_regions` replaces the phone recognizer's default regions, and `default_threshold` Presidio's own default."""
    engine = NoOpNlpEngine(models=[{"lang_code": "en", "model_name": "no_op"}])
    registry = RecognizerRegistry()
    registry.load_predefined_recognizers(nlp_engine=engine)
    if phone_regions:
        registry.remove_recognizer("PhoneRecognizer")
        registry.add_recognizer(PhoneRecognizer(supported_regions=tuple(phone_regions)))
    if thresholds is not None:
        for recognizer in list(registry.recognizers):
            if recognizer.name not in thresholds:
                registry.remove_recognizer(recognizer.name)
            else:
                recognizer.score_thresholds = {"default": thresholds[recognizer.name]}
    if default_threshold is None:
        return AnalyzerEngine(registry=registry, nlp_engine=engine)
    return AnalyzerEngine(registry=registry, nlp_engine=engine, default_score_threshold=default_threshold)


def platform_detector() -> AnalyzerEngine:
    """The detector the platform runs: the recognizers and thresholds in governance/pii.toml, and phone numbers read
    in every country the learners live in."""
    return detector(THRESHOLDS, countries())


def valid_phone(number: str, country: str) -> bool:
    """Whether python-phonenumbers' metadata calls a number valid, read as it would be written in the country. The
    phone recognizer accepts only valid numbers."""
    try:
        return phonenumbers.is_valid_number(phonenumbers.parse(number, country))
    except phonenumbers.NumberParseException:
        return False


def detect(detector: AnalyzerEngine, text: str) -> list[tuple[int, int, str, float]]:
    """(start, end, entity, score) for everything the detector finds in one text."""
    return [(r.start, r.end, r.entity_type, r.score) for r in detector.analyze(text, language="en")]


# ---------------------------------------------------------------- free text, and measuring against the truth

SOURCES = {"tickets": "ticket_id", "reviews": "review_id"}      # the free text: support tickets and course reviews


@dataclass(frozen=True)
class Span:
    """A stretch of one ticket or review, by character offsets, with the entity type it holds or a detector's label."""
    source: str          # tickets or reviews
    row_id: int
    start: int
    end: int
    entity: str          # an entity type, or a model's label


@cache
def texts(source: str) -> dict[int, str]:
    """Every text of one source, by row ID. Read once, then shared: don't change it."""
    rows = load(source).select([SOURCES[source], "body"]).to_pylist()
    return {row[SOURCES[source]]: row["body"] for row in rows}


def truth() -> list[Span]:
    """Every piece of personal data placed in the tickets and reviews, from pii_spans.parquet."""
    return [Span(r["source"], r["row_id"], r["start"], r["end"], r["entity"])
            for r in pq.read_table(DATA / "pii_spans.parquet").to_pylist()]


def find(detector: AnalyzerEngine) -> list[Span]:
    """What the detector finds in every ticket and review."""
    return [Span(source, row_id, start, end, entity)
            for source in SOURCES for row_id, text in texts(source).items()
            for start, end, entity, _ in detect(detector, text)]


def recorded(name: str = "pii-entities") -> tuple[list[Span], dict]:
    """A recording's spans, labelled as the model labels them, and its manifest. It refuses a recording made on other
    text: each span keeps the text the model found, which must be the text at its offsets in the data now."""
    folder = RECORDED / name
    rows = pq.read_table(folder / "entities.parquet").to_pylist()
    for r in rows:
        if texts(r["source"])[r["row_id"]][r["start"]:r["end"]] != r["text"]:
            raise ValueError(f"the recording {name} was made on other text than {r['source']} {r['row_id']} holds "
                             f"now: record it again")
    return ([Span(r["source"], r["row_id"], r["start"], r["end"], r["label"]) for r in rows],
            json.loads((folder / "manifest.json").read_text(encoding="utf-8")))


@dataclass
class Scores:
    """What score measured: counts per entity type and per label, and the texts that masking would leave clean."""
    recall: dict[str, list[int]]       # entity type -> [labelled, found exactly, found overlapping]
    precision: dict[str, list[int]]    # detector label -> [found, matching a label exactly, overlapping one]
    texts: int                         # texts holding personal data
    masked: int                        # of those, texts where every labelled character is inside some detection


def score(found: list[Span], labelled: list[Span], matches: dict[str, set[str]] | None = None) -> Scores:
    """Span-level recall per entity type and precision per detector label, by exact boundaries and by overlap.
    `matches` says which entity types a detector label may match; by default a label matches only itself."""
    allowed = lambda label: (matches or {}).get(label, {label})
    by_text, truth_by_text = defaultdict(list), defaultdict(list)
    for span in found:
        by_text[span.source, span.row_id].append(span)
    for span in labelled:
        truth_by_text[span.source, span.row_id].append(span)
    exact = lambda d, t: (d.start, d.end) == (t.start, t.end) and t.entity in allowed(d.entity)
    overlap = lambda d, t: d.start < t.end and t.start < d.end and t.entity in allowed(d.entity)
    recall, precision = defaultdict(lambda: [0, 0, 0]), defaultdict(lambda: [0, 0, 0])
    for t in labelled:
        candidates = by_text.get((t.source, t.row_id), [])
        counts = recall[t.entity]
        counts[0] += 1
        counts[1] += any(exact(d, t) for d in candidates)
        counts[2] += any(overlap(d, t) for d in candidates)
    for d in found:
        candidates = truth_by_text.get((d.source, d.row_id), [])
        counts = precision[d.entity]
        counts[0] += 1
        counts[1] += any(exact(d, t) for t in candidates)
        counts[2] += any(overlap(d, t) for t in candidates)
    masked = 0
    for key, spans in truth_by_text.items():
        covered = {i for d in by_text.get(key, []) for i in range(d.start, d.end)}
        masked += all(set(range(t.start, t.end)) <= covered for t in spans)
    return Scores(dict(sorted(recall.items())), dict(sorted(precision.items())), len(truth_by_text), masked)


def coverage(found: list[Span], labelled: list[Span]) -> dict[str, tuple[int, int]]:
    """Per entity type in the truth: (labelled spans, of them those whose every character is inside some detection,
    whatever its label). It's the span-level measure masking needs. `score` counts a span only when a detection has a
    label of its type, so a card number found as a phone number is covered here and missed there."""
    chars = defaultdict(set)
    for d in found:
        chars[d.source, d.row_id].update(range(d.start, d.end))
    counts = defaultdict(lambda: [0, 0])
    for t in labelled:
        counts[t.entity][0] += 1
        counts[t.entity][1] += set(range(t.start, t.end)) <= chars[t.source, t.row_id]
    return {entity: (n, covered) for entity, (n, covered) in sorted(counts.items())}


def labels_on(spans: list[Span], found: list[Span]) -> list[set[str]]:
    """For each span, the labels of the detections that overlap it, by character."""
    by_text = defaultdict(list)
    for d in found:
        by_text[d.source, d.row_id].append(d)
    return [{d.entity for d in by_text[s.source, s.row_id] if d.start < s.end and s.start < d.end} for s in spans]


def uncovered(span: Span, found: list[Span]) -> str:
    """The characters of a span that no detection covers, whatever its label, in order."""
    chars = {i for d in found if (d.source, d.row_id) == (span.source, span.row_id) for i in range(d.start, d.end)}
    text = texts(span.source)[span.row_id]
    return "".join(text[i] for i in range(span.start, span.end) if i not in chars)


def precision_overall(scores: Scores) -> float:
    """The share of all detections that match a labelled span exactly."""
    found = sum(counts[0] for counts in scores.precision.values())
    return sum(counts[1] for counts in scores.precision.values()) / found if found else 0.0


# The hard cases the tickets plant on purpose (data/platform/README.md), recognized from the labelled spans. First names
# that are also words (Grace, Will) appear in the tickets as "My manager Grace approved the team licence."
HARD_CASES = {
    "names in lowercase": lambda span, text: span.entity == "PERSON" and text.islower(),
    "initial and surname": lambda span, text: span.entity == "PERSON" and re.fullmatch(r"\w\. \S+", text) is not None,
    "first names that are words": lambda span, text: span.entity == "PERSON" and span.source == "tickets"
                                                     and span.start >= 11 and _before(span, 11) == "My manager ",
    "instructors named in reviews": lambda span, text: span.entity == "PERSON" and span.source == "reviews",
    "phones typed as digits only": lambda span, text: span.entity == "PHONE_NUMBER" and text.isdigit(),
}
# Numbers and dates in the tickets that only look like personal data.
LOOKALIKES = {"invoice numbers": r"The invoice number is (\d+)", "order numbers": r"Order (\d{4} \d{4})",
              "dates of a problem": r"It happened on (\d\d/\d\d)"}


def text_of(span: Span) -> str:
    """The text a span covers."""
    return texts(span.source)[span.row_id][span.start:span.end]


_GREETING = re.compile(r"^(?:Hi|Hello|Hi team|Good morning), (\w)")      # the generator's openings, before a sentence


def templates() -> set[str]:
    """The sentence frames the tickets and reviews are put together from, read back from the text: in each text, every
    labelled span becomes its entity type, every course title {course} and every invoice number, the one unlabelled run
    of 11 digits, {invoice}; a greeting is dropped; and what's left is split into sentences."""
    spans = defaultdict(list)
    for span in truth():
        spans[span.source, span.row_id].append(span)
    titles = sorted(load("learning.courses")["title"].to_pylist(), key=len, reverse=True)
    frames = set()
    for source in SOURCES:
        for row_id, text in texts(source).items():
            for span in sorted(spans[source, row_id], key=lambda span: span.start, reverse=True):
                text = f"{text[:span.start]}{{{span.entity}}}{text[span.end:]}"
            for title in titles:
                text = text.replace(title, "{course}")
            text = _GREETING.sub(lambda match: match.group(1).upper(), re.sub(r"\b\d{11}\b", "{invoice}", text))
            frames.update(re.split(r"(?<=[.?])\s+", text))
    return frames


# Phone numbers, card numbers and IBANs in the platform's data are random in a valid format, so any one may be in
# service (data/platform/README.md). An example that prints text holding one prints it through masked().
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_NUMBER = re.compile(r"\+?\(?\d[\d ().-]*\d(?: ?x\d+)?")     # digits with spaces, dashes, dots or brackets, an extension
_SHOWN = re.compile(r"\d{4}-\d\d-\d\d|\d{1,3}(?:\.\d{1,3}){3}")     # dates and IP addresses, which may be shown


def masked(text: str) -> str:
    """The text with every IBAN, and every number of seven digits or more, masked but for its last four digits, so its
    shape still shows. An extension after a phone number is masked whole. Dates and IP addresses stay as written."""
    def number(match: re.Match) -> str:
        main, x, extension = match.group().partition("x")
        if sum(c.isdigit() for c in main) < 7 or _SHOWN.fullmatch(match.group()):
            return match.group()
        return _last_four(main, str.isdigit) + x + "•" * len(extension)
    return _NUMBER.sub(number, _IBAN.sub(lambda match: _last_four(match.group(), str.isalnum), text))


def _last_four(value: str, counted) -> str:
    """The value with every character `counted` accepts replaced by a bullet, except the last four."""
    kept, out = 0, []
    for c in reversed(value):
        kept += counted(c)
        out.append("•" if counted(c) and kept > 4 else c)
    return "".join(reversed(out))


def _before(span: Span, length: int) -> str:
    return texts(span.source)[span.row_id][span.start - length:span.start]


def hard_cases(labelled: list[Span]) -> dict[str, list[Span]]:
    """The labelled spans of each hard case."""
    return {case: [s for s in labelled if test(s, text_of(s))] for case, test in HARD_CASES.items()}


def lookalikes() -> dict[str, list[Span]]:
    """Where each kind of look-alike sits in the tickets."""
    tickets = texts("tickets")
    return {kind: [Span("tickets", row_id, m.start(1), m.end(1), kind) for row_id, text in tickets.items()
                   for m in re.finditer(pattern, text)] for kind, pattern in LOOKALIKES.items()}


def missed(spans: list[Span], found: list[Span], labels: set[str] | None = None) -> list[Span]:
    """The spans that no detection overlaps (no detection with one of the labels given, if any)."""
    by_text = defaultdict(list)
    for d in found:
        if labels is None or d.entity in labels:
            by_text[d.source, d.row_id].append(d)
    return [s for s in spans if not any(d.start < s.end and s.start < d.end for d in by_text.get((s.source, s.row_id), []))]


def caught(spans: list[Span], found: list[Span], labels: set[str] | None = None) -> int:
    """How many of the spans some detection overlaps (a detection with one of the labels given, if any)."""
    return len(spans) - len(missed(spans, found, labels))
