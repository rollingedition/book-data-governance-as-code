"""The platform's metadata, as code: governance/metadata.toml says who owns each table, what it holds and how each
column is classified. Columns are keyed by Iceberg field ID, which survives a rename."""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq
from pyiceberg.catalog import Catalog
from pyiceberg.schema import Schema

METADATA = Path(__file__).resolve().parent.parent / "governance" / "metadata.toml"
SUGGESTIONS = Path(__file__).resolve().parent.parent / "data" / "recorded" / "metadata-suggestions" / "suggestions.parquet"
# The same model and columns under an earlier prompt, whose examples of personal and sensitive were the platform's columns.
SUGGESTIONS_WITH_PLATFORM_EXAMPLES = SUGGESTIONS.with_name("suggestions-platform-examples.parquet")
LEVELS = ["public", "internal", "personal", "sensitive"]     # from least to most protected


@dataclass(frozen=True)
class Column:
    field_id: int
    name: str
    classification: str
    description: str
    deprecated: bool = False    # `deprecated = true` in its entry: the column is on its way out (lib.ci's removals)


@dataclass(frozen=True)
class Table:
    name: str
    owner: str
    domain: str
    description: str
    columns: dict[int, Column]

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns.values() if c.name == name), None)


def load(path: Path = METADATA) -> dict[str, Table]:
    """Every table the metadata file describes, by full name. A column's `deprecated` key is optional, false when absent."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))["tables"]
    return {name: Table(name, t["owner"], t["domain"], t["description"],
                        {int(fid): Column(int(fid), c["name"], c["classification"], c["description"],
                                          c.get("deprecated", False) is True) for fid, c in t["columns"].items()})
            for name, t in raw.items()}


def stricter(a: str, b: str) -> str:
    """The more protected of two classifications."""
    return max(a, b, key=LEVELS.index)


def apply(catalog: Catalog, metadata: dict[str, Table]) -> list[str]:
    """Write each described table's column descriptions into Iceberg's column docs, and its owner and domain into table
    properties. Returns the tables changed."""
    changed = []
    for name, described in metadata.items():
        if not catalog.table_exists(name):
            continue
        table = catalog.load_table(name)
        fields = {f.field_id: f for f in table.schema().fields}
        stale = [c for c in described.columns.values() if c.field_id in fields and fields[c.field_id].doc != c.description]
        if stale:
            with table.update_schema() as update:
                for column in stale:
                    update.update_column(fields[column.field_id].name, doc=column.description)
        relabel = table.properties.get("owner") != described.owner or table.properties.get("domain") != described.domain
        if relabel:
            with table.transaction() as transaction:
                transaction.set_properties(owner=described.owner, domain=described.domain)
        if stale or relabel:
            changed.append(name)
    return changed


def problems(catalog: Catalog, metadata: dict[str, Table]) -> list[str]:
    """What the metadata misses about the catalog: tables with no entry or no owner, field IDs with no classification,
    classifications that aren't one of the four, and columns renamed since they were reviewed. A deprecated column is
    checked like any other, since it's still in the table."""
    found = []
    for namespace in sorted(n for (n,) in catalog.list_namespaces()):
        for _, name in sorted(catalog.list_tables(namespace)):
            full = f"{namespace}.{name}"
            described = metadata.get(full)
            if described is None:
                found.append(f"{full}: no metadata")
                continue
            if not described.owner:
                found.append(f"{full}: no owner")
            for field in catalog.load_table(full).schema().fields:
                column = described.columns.get(field.field_id)
                if column is None:
                    found.append(f"{full}.{field.name}: not classified")
                elif column.name != field.name:
                    found.append(f"{full}.{field.name}: renamed from {column.name} since it was reviewed")
                elif column.classification not in LEVELS:
                    found.append(f"{full}.{field.name}: unknown classification {column.classification!r}")
    return found


def dropped(catalog: Catalog, metadata: dict[str, Table]) -> list[str]:
    """What problems() can't see: columns the metadata describes that their table no longer has, so their entries are
    stale. Tables not in the catalog are skipped, as apply() skips them."""
    found = []
    for name, described in sorted(metadata.items()):
        if catalog.table_exists(name):
            fields = {f.field_id for f in catalog.load_table(name).schema().fields}
            found += [f"{name}.{c.name}: field {c.field_id} is described but no longer in the table"
                      for c in described.columns.values() if c.field_id not in fields]
    return found


def shape(schema: Schema) -> list[tuple[int, str, str, bool]]:
    """What a review of a schema change should compare: each column's field ID, name, type and whether it's required.
    Editing a doc leaves the shape as it was, although PyIceberg gives the schema a new ID."""
    return [(f.field_id, f.name, str(f.field_type), f.required) for f in schema.fields]


@dataclass(frozen=True)
class Suggestion:
    table: str
    field_id: int
    column: str
    type: str
    samples: list[str]
    response: str               # the model's answer, word for word
    description: str | None     # None when the answer isn't the JSON object the prompt asked for
    classification: str | None  # as the model wrote it, which may not be one of LEVELS


def parse(response: str) -> tuple[str | None, str | None]:
    """The description and classification in a model's answer: a JSON object, alone or in a ```json fence."""
    text = response.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        answer = json.loads(text)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(answer, dict) or not all(isinstance(answer.get(k), str) for k in ("description", "classification")):
        return None, None
    return answer["description"], answer["classification"]


def suggestions(path: Path = SUGGESTIONS) -> list[Suggestion]:
    """The recorded suggestions (recordings/metadata_suggestions.py): a local open model's description and
    classification for every column of the platform's eight source tables, from its name, type and five values. The
    default is the book's prompt; SUGGESTIONS_WITH_PLATFORM_EXAMPLES holds the earlier prompt's answers."""
    return [Suggestion(r["table"], r["field_id"], r["column"], r["type"], r["samples"], r["response"], *parse(r["response"]))
            for r in pq.read_table(path).to_pylist()]
