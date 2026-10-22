"""Retention and erasure for the platform's Iceberg tables: the rules in governance/retention.toml applied against a
given day, an erasure planned from lineage, and the clean-up PyIceberg leaves to its users. At the version this book
pins, PyIceberg's snapshot expiry ignores the table's own settings and deletes no file, and nothing in it removes the
files no snapshot references. So this module does both, and then reads every file that's left to check what it holds."""
from __future__ import annotations

import hashlib
import hmac
import json
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from pyarrow.fs import FileSelector, FileSystem, FileType
from pyiceberg.avro.file import AvroFile
from pyiceberg.catalog import Catalog
from pyiceberg.expressions import AlwaysFalse, AlwaysTrue, And, BooleanExpression, EqualTo, In, LessThan
from pyiceberg.table import Table
from pyiceberg.table.snapshots import ancestors_of
from pyiceberg.typedef import Record

from lib import lineage, metadata, pipelines
from lib.catalog import open_catalog
from lib.lineage import Graph, downstream, table_of
from lib.platform import TABLES, load, scratch

RULES = Path(__file__).resolve().parent.parent / "governance" / "retention.toml"
METRICS = "write.metadata.metrics.column."
PREFIX = 16                     # the characters of a string a manifest keeps by default, as truncate(16)
SEARCHED = PREFIX - 1           # what a largest value keeps unchanged: its 16th character is raised by one
WITHDRAWN = (3590,)             # the book's case: learners who withdrew marketing consent weeks before asking for erasure
# The book's key for the erasure register, fixed so outputs never change. A real one is the erasure job's secret, kept
# like the enforcement layer's pseudonymization key in a secret manager, and a different one: whoever holds it can hash
# every learner ID and so find out whom the register names.
REGISTER_KEY = "example-erasure-key-not-a-secret"


class RetentionError(RuntimeError):
    """A clean-up that a table's own settings forbid."""


class ErasureRefused(RetentionError):
    """An erasure refused before it changed anything, because some tables in its plan would still hold the person
    afterwards. It carries the plan and the traces, so the scan can still run, and the tables, each with its reason."""

    def __init__(self, steps: list[Step], found: Traces, tables: dict[str, str]):
        self.steps, self.found, self.tables = steps, found, tables
        super().__init__("nothing erased: " + "; ".join(f"{name}: {why}" for name, why in tables.items()))


def rules(path: Path = RULES) -> dict:
    """The platform's retention rules (governance/retention.toml)."""
    return tomllib.loads(path.read_text(encoding="utf-8"))


def min_file_age(settings: dict | None = None) -> timedelta:
    """How old an unreferenced file must be before a sweep in production removes it ([files] in the rules)."""
    return timedelta(hours=(settings or rules())["files"]["min_age_hours"])


def tables(catalog: Catalog) -> list[str]:
    """Every table in the catalog, as "namespace.table", sorted."""
    return sorted(f"{namespace}.{name}" for (namespace,) in catalog.list_namespaces()
                  for _, name in catalog.list_tables(namespace))


def count(table: Table, rows: BooleanExpression = AlwaysTrue(), snapshot_id: int | None = None) -> int:
    """How many rows of a table match a filter, in its current snapshot or in an earlier one."""
    return table.scan(row_filter=rows, snapshot_id=snapshot_id).to_arrow().num_rows


# ---------------------------------------------------------------- retention rules


def cutoff(rule: dict, today: date) -> date:
    """The first day a rule still keeps on `today`."""
    return today - timedelta(days=rule["keep_days"])


def due(rule: dict, today: date) -> BooleanExpression:
    """The rows a rule no longer keeps on `today`: those whose date is before its cutoff, and in the state it names."""
    start = datetime.combine(cutoff(rule, today), time(), tzinfo=timezone.utc)
    rows: BooleanExpression = LessThan(rule["counted_from"], start.isoformat())
    if "only_when" in rule:
        rows = And(EqualTo(rule["only_when"]["column"], rule["only_when"]["equals"]), rows)
    return rows


def apply(catalog: Catalog, today: date, settings: dict | None = None) -> dict[str, int]:
    """Delete every row the rules no longer keep on `today`, table by table. A commit records the rule's table and the
    day in its snapshot summary; a table with nothing due gets no commit. Returns the rows deleted from each table."""
    settings = settings or rules()
    deleted = {}
    for name, rule in sorted(settings["tables"].items()):
        table = catalog.load_table(name)
        deleted[name] = count(table, due(rule, today))
        if deleted[name]:
            why = {"retention-rule": name, "retention-day": today.isoformat()}
            table.delete(due(rule, today), snapshot_properties=why)
    return deleted


# ---------------------------------------------------------------- table properties, generated


def properties(catalog: Catalog, described: dict[str, metadata.Table] | None = None,
               settings: dict | None = None) -> dict[str, dict[str, str]]:
    """The properties each table in the catalog should carry, generated from the retention rules and the classifications
    in governance/metadata.toml: how long its snapshots are kept, and no statistics for a column whose classification
    the rules list, or that nobody has classified. Iceberg names the column in the property, so a rename needs them
    generated again."""
    settings = settings or rules()
    described = metadata.load() if described is None else described
    snapshots, statistics = settings["snapshots"], settings["statistics"]
    found = {}
    for name in tables(catalog):
        columns = described[name].columns if name in described else {}
        found[name] = {"history.expire.max-snapshot-age-ms": str(snapshots["max_age_days"] * 86_400_000),
                       "history.expire.min-snapshots-to-keep": str(snapshots["min_to_keep"])}
        for field in catalog.load_table(name).schema().fields:
            classification = columns[field.field_id].classification if field.field_id in columns else "sensitive"
            if classification in statistics["classifications"]:
                found[name][METRICS + field.name] = statistics["mode"]
    return found


def missing_properties(catalog: Catalog) -> list[str]:
    """The tables whose properties differ from those generated for them, such as a table a pipeline created again."""
    return [name for name, wanted in properties(catalog).items()
            if any(catalog.load_table(name).properties.get(key) != value for key, value in wanted.items())]


def apply_properties(catalog: Catalog, described: dict[str, metadata.Table] | None = None) -> list[str]:
    """Give every table the properties generated for it, and rewrite its data once, so that every file its current
    snapshot references was written under them. The files from before stay until their snapshots are expired and the
    files swept. Returns the tables changed."""
    changed = []
    for name, wanted in properties(catalog, described).items():
        table = catalog.load_table(name)
        if all(table.properties.get(key) == value for key, value in wanted.items()):
            continue
        with table.transaction() as transaction:
            transaction.set_properties(wanted)
        table.overwrite(table.scan().to_arrow(), snapshot_properties={"rewritten-for": "table properties"})
        changed.append(name)
    return changed


# ---------------------------------------------------------------- erasure, planned from lineage


@dataclass(frozen=True)
class Step:
    """One table an erasure visits: delete the rows whose `column` holds one of `values`, the column being a copy of
    `via` when lineage found it; or, with no column, run the job that writes the table again, once the tables it reads
    are erased. When an exception keeps the table's rows, `kept` is the reason the legal team gave, and the rows stay,
    with the columns in `cleared` erased."""
    table: str
    column: str | None
    values: tuple = ()
    via: str = ""
    kept: str = ""
    cleared: tuple[str, ...] = ()


def exceptions(settings: dict | None = None) -> dict[str, dict]:
    """The tables an erasure keeps, by name, as the platform's legal team recorded them in governance/retention.toml:
    each with its reason and the columns to erase from the kept rows. The code decides none of them. It refuses an
    exception with no reason, or one for a table that no retention rule ends, since its rows would then stay for good."""
    settings = settings or rules()
    found = settings["erasure"].get("exceptions", {})
    for name, exception in found.items():
        if not exception.get("reason"):
            raise RetentionError(f"the erasure exception for {name} gives no reason")
        if name not in settings["tables"]:
            raise RetentionError(f"the erasure exception for {name} has no retention rule to end it")
    return found


def plan(catalog: Catalog, graph: Graph, learner_id: int) -> list[Step]:
    """Where one learner's data is, table by table. First every table no job writes that has a `learner_id` column.
    Then every table lineage says those tables' rows reach, in the order jobs wrote them: by a column copied unchanged
    (IDENTITY) from the learner's ID or from the ID of one of the learner's rows, or, when the learner's rows only
    shaped it, by running its job again. A source table with an exception in governance/retention.toml is kept, with
    the exception's reason."""
    steps, keys = [], {}         # keys: "table.column" -> the values in it that are the learner's
    excepted = exceptions()
    for name in tables(catalog):
        table = catalog.load_table(name)
        fields = [field.name for field in table.schema().fields]
        if name in graph.columns or "learner_id" not in fields:
            continue
        exception = excepted.get(name, {})
        steps.append(Step(name, "learner_id", (learner_id,), kept=exception.get("reason", ""),
                          cleared=tuple(exception.get("erase_columns", ()))))
        ids = table.scan(row_filter=EqualTo("learner_id", learner_id), selected_fields=(fields[0],)).to_arrow()
        keys[f"{name}.learner_id"] = (learner_id,)
        keys[f"{name}.{fields[0]}"] = tuple(sorted(ids.column(0).to_pylist()))
    reached = {table_of(edge.target) for step in list(steps) for edge in downstream(graph, step.table)}
    for name in [table for table in graph.columns if table in reached]:
        copies = sorted((not edge.source.endswith(".learner_id"), edge.target, edge.source) for edge in graph.edges
                        if edge.subtype == "IDENTITY" and edge.source in keys and table_of(edge.target) == name
                        and edge.target != name)
        if not copies:
            steps.append(Step(name, None))
            continue
        _, target, source = copies[0]
        steps.append(Step(name, target.rpartition(".")[2], keys[source], via=source))
        keys[target] = keys[source]
    return steps


def rows(step: Step) -> BooleanExpression:
    """The rows a step deletes, as an Iceberg filter."""
    if not step.values:
        return AlwaysFalse()
    return EqualTo(step.column, step.values[0]) if len(step.values) == 1 else In(step.column, step.values)


def rows_of(learners: list[int]) -> BooleanExpression:
    """The rows of some learners, as an Iceberg filter."""
    return rows(Step("", "learner_id", tuple(learners)))


def job_for(table: str) -> str:
    """The pipeline that writes a table."""
    return next(path.stem for path in sorted(pipelines.PIPELINES.glob("*.sql"))
                if pipelines.target(path.read_text(encoding="utf-8")) == table)


def erase(catalog: Catalog, steps: list[Step], request: str) -> dict[str, int | str]:
    """Carry out an erasure plan, in its order. Each delete records the request's ID in its snapshot summary, and
    nothing about the learner, because the summary stays in the table's metadata. A table an exception keeps deletes
    nothing: its rows are written again with the exception's `cleared` columns empty. Returns the rows deleted from each
    table, or "rebuilt"."""
    done: dict[str, int | str] = {}
    why = {"erasure-request": request}
    for step in steps:
        if step.column is None:
            pipelines.run(catalog, job_for(step.table))
            done[step.table] = "rebuilt"
            continue
        table = catalog.load_table(step.table)
        if step.kept:
            done[step.table] = 0
            kept = table.scan(row_filter=rows(step)).to_arrow()
            if step.cleared and kept.num_rows:
                for column in step.cleared:
                    field = kept.schema.field(column)
                    kept = kept.set_column(kept.schema.get_field_index(column), field, pa.nulls(kept.num_rows, field.type))
                table.overwrite(kept, overwrite_filter=rows(step), snapshot_properties=why)
            continue
        done[step.table] = count(table, rows(step))
        if done[step.table]:
            table.delete(rows(step), snapshot_properties=why)
    return done


# ---------------------------------------------------------------- snapshots and files


def expire(table: Table, older_than_ms: int | None = None) -> int:
    """Expire the snapshots committed before `older_than_ms`, or all it may when that is None, as far as the table's
    own settings allow, which PyIceberg's expiry never reads: nothing at all when `gc.enabled` is false, and never the
    newest `history.expire.min-snapshots-to-keep` snapshots of the main branch, nor one a branch or tag points at.
    An erasure passes None. Retention by age passes the clock's time, in milliseconds, less the table's
    `history.expire.max-snapshot-age-ms`: commit times are real times, unlike the dates in rows. Returns how many it
    expired."""
    settings = table.properties
    if settings.get("gc.enabled", "true").lower() == "false":
        name = ".".join(table.name()[-2:])
        raise RetentionError(f"{name} has gc.enabled=false, which turns off garbage collection for the table")
    keep = int(settings.get("history.expire.min-snapshots-to-keep", "1"))
    kept = {s.snapshot_id for s in list(ancestors_of(table.current_snapshot(), table.metadata))[:keep]}
    kept |= {ref.snapshot_id for ref in table.metadata.refs.values()}
    expired = [s.snapshot_id for s in table.snapshots()
               if s.snapshot_id not in kept and (older_than_ms is None or s.timestamp_ms < older_than_ms)]
    if expired:
        table.maintenance.expire_snapshots().by_ids(expired).commit()
    return len(expired)


def expire_by_age(table: Table, now: datetime | None = None) -> int:
    """Retention by age: expire, as `expire` does, the snapshots older than the table's own
    `history.expire.max-snapshot-age-ms`, measured back from `now` on the clock (the rules' [snapshots] age when the
    table sets none). Returns how many it expired."""
    default = rules()["snapshots"]["max_age_days"] * 86_400_000
    age = int(table.properties.get("history.expire.max-snapshot-age-ms", str(default)))
    return expire(table, int((now or datetime.now(timezone.utc)).timestamp() * 1000) - age)


def blocked(catalog: Catalog, steps: list[Step]) -> dict[str, str]:
    """The tables in a plan that an erasure couldn't clean up, each with the reason, from the table's own settings:
    garbage collection off; more than one snapshot to keep, which keeps the snapshot from before the delete; or a
    branch or tag besides main, which keeps the snapshot it points at. Checked before anything is changed."""
    found = {}
    for step in steps:
        table = catalog.load_table(step.table)
        settings, reasons = table.properties, []
        if settings.get("gc.enabled", "true").lower() == "false":
            reasons.append("gc.enabled=false turns off garbage collection for the table")
        keep = int(settings.get("history.expire.min-snapshots-to-keep", "1"))
        if keep > 1:
            reasons.append(f"history.expire.min-snapshots-to-keep={keep} keeps the snapshot from before the delete")
        for name, ref in sorted(table.metadata.refs.items()):
            if name != "main":
                reasons.append(f"the {ref.snapshot_ref_type.value} {name} keeps the snapshot it points at")
        if reasons:
            found[step.table] = "; ".join(reasons)
    return found


def _path(uri: str) -> str:
    return FileSystem.from_uri(uri)[1]


def referenced(table: Table) -> set[str]:
    """Every file the table's metadata references, as file-system paths: its metadata files, current and logged, each
    snapshot's manifest list, every manifest of any snapshot, every data and delete file live in any snapshot, and its
    statistics files. Built from PyIceberg's inspect tables."""
    uris = {table.metadata_location, *table.inspect.metadata_log_entries().column("file").to_pylist()}
    uris |= {snapshot.manifest_list for snapshot in table.snapshots()}
    uris |= set(table.inspect.all_manifests().column("path").to_pylist())
    uris |= set(table.inspect.all_files().column("file_path").to_pylist())
    uris |= {s.statistics_path for s in table.metadata.statistics + table.metadata.partition_statistics}
    return {_path(uri) for uri in uris}


def files_under(location: str) -> list:
    """Every file under a location, as pyarrow file infos, sorted by path."""
    fs, root = FileSystem.from_uri(location)
    found = fs.get_file_info(FileSelector(root, recursive=True, allow_not_found=True))
    return sorted((info for info in found if info.type == FileType.File), key=lambda info: info.path)


def unreferenced(table: Table) -> list[str]:
    """The paths of the files under the table's location that its metadata doesn't reference."""
    keep = referenced(table)
    return [info.path for info in files_under(table.location()) if info.path not in keep]


def sweep(table: Table, min_age: timedelta, now: datetime | None = None) -> int:
    """Delete the files under the table's location that its metadata doesn't reference and that are older than
    `min_age`. A file's age is measured on the clock, unlike a row's, because what it guards against happens in real
    time: a write in progress, whose files exist before its commit references them. A `min_age` of zero skips that
    guard, which is safe only when nothing else can write to the table. Returns how many files it deleted."""
    fs, _ = FileSystem.from_uri(table.location())
    keep = referenced(table)
    limit = (now or datetime.now(timezone.utc)) - min_age
    old = [info.path for info in files_under(table.location())
           if info.path not in keep and (min_age == timedelta(0) or info.mtime < limit)]
    for path in old:
        fs.delete_file(path)
    return len(old)


# ---------------------------------------------------------------- reading every file that's left


@dataclass(frozen=True)
class Traces:
    """What identifies one learner in a file: the IDs of their rows, by the column that holds them, and the first 15
    characters, lowercased, of each value that identifies them on its own. A manifest keeps a string column's smallest
    value cut to 16 characters, and its largest cut to 16 with the last one raised by one, so that it still bounds the
    rest: 15 characters are what both keep unchanged. `cleared` holds the same for the values an exception's columns
    held, such as a card number, and `kept` names the tables an exception keeps, whose rows still hold the IDs."""
    ids: dict[str, tuple]
    texts: tuple[str, ...]
    cleared: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()


def traces(catalog: Catalog, steps: list[Step], learner_id: int, settings: dict | None = None) -> Traces:
    """A learner's traces, read before the erasure: every ID of the rows the plan deletes, by column, the learner's
    identifying values in crm.learners (governance/retention.toml), and the values in the columns an exception erases."""
    columns = (settings or rules())["erasure"]["identifying_columns"]
    learner = catalog.load_table("crm.learners").scan(row_filter=EqualTo("learner_id", learner_id),
                                                        selected_fields=tuple(columns)).to_arrow().to_pylist()[0]
    ids: dict[str, set] = {}
    cleared: set[str] = set()
    for step in (s for s in steps if s.column is not None):
        table = catalog.load_table(step.table)
        if step.kept:
            if step.cleared:
                held = table.scan(row_filter=rows(step), selected_fields=step.cleared).to_arrow()
                cleared |= {str(value)[:SEARCHED].lower() for column in held.columns for value in column.to_pylist()
                            if value is not None}
            continue
        ids.setdefault(step.column, set()).update(step.values)
        first = table.schema().fields[0].name
        if first != step.column:
            ids.setdefault(first, set()).update(table.scan(row_filter=rows(step), selected_fields=(first,))
                                                .to_arrow().column(0).to_pylist())
    texts = {learner[column][:SEARCHED].lower() for column in columns if learner[column]}
    return Traces({column: tuple(sorted(values)) for column, values in sorted(ids.items())}, tuple(sorted(texts)),
                  tuple(sorted(cleared)), tuple(sorted(step.table for step in steps if step.kept)))


def _naming(data: pa.Table, texts: tuple[str, ...]) -> pa.ChunkedArray | None:
    """Which rows hold one of the texts in any string column, lowercased; None when the table has no strings."""
    found = None
    for column in data.columns:
        if pa.types.is_string(column.type) or pa.types.is_large_string(column.type):
            lowered = pc.utf8_lower(column)
            for text in texts:
                hit = pc.fill_null(pc.match_substring(lowered, text), False)
                found = hit if found is None else pc.or_(found, hit)
    return found


def mentions(catalog: Catalog, steps: list[Step], found: Traces) -> dict[str, int]:
    """Rows the plan doesn't delete whose text holds one of the learner's identifying values, by table: another
    learner's ticket, or a note, that names them. It looks for the first 15 characters of each value, lowercased,
    anywhere in a string; chapter 3's detectors find the rest."""
    deleting = {step.table: step for step in steps if step.column is not None}
    named = {}
    for name in tables(catalog):
        data = catalog.load_table(name).scan().to_arrow()
        if name in deleting:
            step = deleting[name]
            data = data.filter(pc.invert(pc.is_in(data[step.column], pa.array(step.values, data[step.column].type))))
        hits = _naming(data, found.texts + found.cleared)
        if hits is not None and pc.sum(hits).as_py():
            named[name] = pc.sum(hits).as_py()
    return named


def _strings(value) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, bytes):
        yield value.decode("utf-8", errors="ignore")
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, Record):
        for position in range(len(value)):
            yield from _strings(value[position])


class _Opened:
    """As much of PyIceberg's InputFile as its Avro reader uses: something it can open."""
    def __init__(self, fs: FileSystem, path: str):
        self.fs, self.path = fs, path

    def open(self, seekable: bool = True):
        return self.fs.open_input_file(self.path)


def _bounds(value) -> Iterator[tuple[int, bytes]]:
    """Every column bound inside a manifest record, as (field ID, bytes): the maps of field ID to bound."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, int) and isinstance(item, bytes):
                yield key, item
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _bounds(item)
    elif isinstance(value, Record):
        for position in range(len(value)):
            yield from _bounds(value[position])


def _id_fields(schema: str | None, ids: dict[str, tuple]) -> dict[int, set]:
    """The learner's IDs by the field ID of the integer column that holds them, from the table schema a manifest
    carries in its header; none for a manifest list, which carries no schema."""
    if not schema:
        return {}
    return {field["id"]: set(ids[field["name"]]) for field in json.loads(schema)["fields"]
            if field["name"] in ids and field["type"] in ("int", "long")}


def _holds(fs: FileSystem, path: str, found: Traces, ids: dict[str, tuple]) -> bool:
    texts = found.texts + found.cleared
    if path.endswith(".parquet"):
        with fs.open_input_file(path) as stream:
            data = pq.read_table(stream)
        for name, column in zip(data.column_names, data.columns):
            if name in ids and pc.any(pc.is_in(column, pa.array(ids[name], column.type))).as_py():
                return True
        hits = _naming(data, texts)
        return hits is not None and bool(pc.any(hits).as_py())
    if path.endswith(".avro"):
        with AvroFile[Record](_Opened(fs, path)) as records:
            fields = _id_fields(records.header.meta.get("schema"), ids)
            for record in records:
                if any(text in string.lower() for string in _strings(record) for text in texts):
                    return True
                if any(int.from_bytes(bound, "little", signed=True) in fields[field]
                       for field, bound in _bounds(record) if field in fields):
                    return True
        return False
    with fs.open_input_file(path) as stream:
        content = stream.read().decode("utf-8", errors="ignore").lower()
    return any(text in content for text in texts)


def holds(fs: FileSystem, path: str, found: Traces) -> bool:
    """Whether one file holds any of the learner's traces. A Parquet file is read as rows: one of their IDs in its
    column, or an identifying value inside any string. An Avro file, a manifest or a manifest list, is read record by
    record with PyIceberg's Avro reader: an identifying value inside any string, column bounds included, or one of their
    IDs as the bound of the column that holds it. Anything else, such as a metadata file, is read as text."""
    return _holds(fs, path, found, found.ids)


def scan(catalog: Catalog, found: Traces) -> dict[str, tuple[int, int]]:
    """Read every file under the catalog's warehouse, and count, by the table whose location holds it, the files and
    the files that hold any of the learner's traces. Files under no table's location count as "(no table)". In a table
    an exception keeps, the learner's IDs are expected, so only their values count there."""
    locations = {_path(catalog.load_table(name).location()) + "/": name for name in tables(catalog)}
    fs, _ = FileSystem.from_uri(catalog.properties["warehouse"])
    report: dict[str, tuple[int, int]] = {}
    for info in files_under(catalog.properties["warehouse"]):
        owner = next((name for root, name in locations.items() if info.path.startswith(root)), "(no table)")
        files, holding = report.get(owner, (0, 0))
        ids = {} if owner in found.kept else found.ids
        report[owner] = (files + 1, holding + _holds(fs, info.path, found, ids))
    return dict(sorted(report.items()))


# ---------------------------------------------------------------- the platform, as an erasure finds it


def crm_export() -> pa.Table:
    """crm.learners as the CRM, the application where learners manage their accounts, exports it each night: the
    platform's data with the book's withdrawals of marketing consent (WITHDRAWN) applied."""
    learners = load("crm.learners")
    withdrawn = pc.is_in(learners["learner_id"], pa.array(WITHDRAWN, learners["learner_id"].type))
    index = learners.schema.get_field_index("marketing_consent")
    return learners.set_column(index, learners.schema.field(index),
                               pc.if_else(withdrawn, pa.scalar(False), learners["marketing_consent"]))


def open_platform(statistics_off: bool = False) -> tuple[Catalog, Graph]:
    """A fresh catalog of the platform's tables as the book's erasure request finds them, weeks after learner 3590
    withdrew marketing consent: crm.learners as the CRM last exported it, and the four pipelines run on it, so
    marketing.audience no longer holds the learner. Returns it with the lineage graph the runs' events describe. With
    `statistics_off`, every table is first given its generated properties and rewritten under them, and the files from
    before are expired and swept, with no age guard, since nothing else writes to this catalog."""
    folder = scratch("platform-")         # removed when the process exits
    catalog = open_catalog([name for name in TABLES if name != "crm.learners"], folder=folder)
    catalog.create_namespace_if_not_exists("crm")
    learners = crm_export()
    catalog.create_table("crm.learners", schema=learners.schema).append(learners)
    events = folder / "lineage.jsonl"
    lineage.run_all(catalog, events)
    if statistics_off:
        apply_properties(catalog)
        for name in tables(catalog):
            table = catalog.load_table(name)
            expire(table)
            sweep(table, timedelta(0))
    return catalog, lineage.graph(lineage.read_events(events))


def erase_everywhere(catalog: Catalog, graph: Graph, learner_id: int, request: str,
                     min_age: timedelta) -> tuple[list[Step], Traces, dict[str, int | str]]:
    """The whole erasure job: plan it from lineage, note the learner's traces, check every table in the plan, erase,
    then expire every snapshot the plan's tables may lose and sweep the files no snapshot references, older than
    `min_age` (`min_file_age()` in production). When a table's own settings would keep the learner, it raises
    ErasureRefused before it changes anything, naming every such table. Returns the plan, the traces and what `erase`
    did."""
    steps = plan(catalog, graph, learner_id)
    found = traces(catalog, steps, learner_id)
    refused = blocked(catalog, steps)
    if refused:
        raise ErasureRefused(steps, found, refused)
    done = erase(catalog, steps, request)
    for step in steps:
        table = catalog.load_table(step.table)
        expire(table)
        sweep(table, min_age)
    return steps, found, done


def returned(catalog: Catalog, erased: list[int]) -> dict[str, int]:
    """Rows of the learners with the given IDs, by table, in every table with a `learner_id` column. The erasure
    register keeps no IDs, so the check after a load uses `erased_rows` instead."""
    found = {}
    for name in tables(catalog):
        table = catalog.load_table(name)
        if erased and "learner_id" in [field.name for field in table.schema().fields]:
            matching = count(table, rows_of(erased))
            if matching:
                found[name] = matching
    return found


# ---------------------------------------------------------------- the erasure register


def registered(learner_id: int, key: str = REGISTER_KEY) -> str:
    """How the erasure register keeps an erased learner: a keyed hash (HMAC-SHA256) of their ID. The same ID always
    gives the same hash, so a load can be checked against the register, and without the key nobody can tell whose
    it is."""
    return hmac.new(key.encode("utf-8"), str(learner_id).encode("utf-8"), hashlib.sha256).hexdigest()


def register_entry(request: str, learner_id: int, key: str = REGISTER_KEY) -> dict[str, str]:
    """One entry in the erasure register: the request's ID, which the audit log's record of the erasure also holds,
    and the learner's keyed hash. Nothing else about them."""
    return {"request": request, "learner": registered(learner_id, key)}


def erased_rows(catalog: Catalog, register: list[dict[str, str]], key: str = REGISTER_KEY) -> dict[str, int]:
    """Rows of erased learners, by table, in every table with a `learner_id` column that no exception keeps: each
    learner ID in the table is hashed with the register's key and looked up among the register's hashes. The check
    to run after every load, restore and rebuild."""
    hashes, kept = {entry["learner"] for entry in register}, set(exceptions())
    found = {}
    for name in tables(catalog):
        table = catalog.load_table(name)
        if name in kept or "learner_id" not in [field.name for field in table.schema().fields]:
            continue
        ids = set(table.scan(selected_fields=("learner_id",)).to_arrow().column(0).to_pylist()) - {None}
        erased = sorted(learner for learner in ids if registered(learner, key) in hashes)
        if erased:
            found[name] = count(table, rows_of(erased))
    return found
