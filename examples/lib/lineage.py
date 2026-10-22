"""Lineage for the platform's pipelines, as OpenLineage models it: a job (one pipeline), its runs, and the datasets each
run reads and writes. Each run sends a START event and then a COMPLETE (or FAIL) event through OpenLineage's file
transport. The COMPLETE event carries the output's column lineage, parsed from the job's SQL by sqlglot, plus the inputs
sqlglot.lineage leaves out: the columns that join, filter, group and sort rows, which OpenLineage calls INDIRECT.

The examples fix every event's time, and derive each run's ID from that time and the job's name, so the same nominal
run always writes the same bytes. The lineage graph is read back from the events alone, as a lineage server would."""
from __future__ import annotations

import json
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import reduce
from pathlib import Path

import sqlglot
from openlineage.client import OpenLineageClient
from openlineage.client.event_v2 import InputDataset, Job, OutputDataset, Run, RunEvent, RunState
from openlineage.client.generated import column_lineage_dataset as lineage_facet
from openlineage.client.generated import schema_dataset
from openlineage.client.transport.file import FileConfig, FileTransport
from openlineage.client.uuid import generate_static_uuid
from pyiceberg.catalog import Catalog
from sqlglot import exp
from sqlglot.lineage import Node
from sqlglot.lineage import lineage as sqlglot_lineage
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from lib import metadata, pipelines
from lib.metadata import stricter
from lib.platform import TABLES

NAMESPACE = "iceberg://platform"     # every dataset's namespace: OpenLineage's naming spec has none for a local catalog
JOBS = "platform"                     # every job's namespace
NIGHTLY = datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc)    # the nightly run's start, on the book's "today"
RULES = Path(__file__).resolve().parent.parent / "governance" / "lineage.toml"
DIALECT = "duckdb"

# The clauses of a SELECT whose columns decide which rows exist and how they're grouped, as OpenLineage subtypes.
ROW_CLAUSES = {"joins": "JOIN", "where": "FILTER", "group": "GROUP_BY", "having": "FILTER", "qualify": "FILTER",
               "order": "SORT"}
HASHES = (exp.MD5, exp.MD5Digest, exp.SHA, exp.SHA2, exp.SHA1Digest, exp.SHA2Digest)
DIRECT_ORDER = ["IDENTITY", "TRANSFORMATION", "AGGREGATION"]


class LineageError(ValueError):
    """A statement whose column lineage can't be trusted: a kind sqlglot misreads, such as a MERGE; an INSERT whose
    values don't match its table's columns; no lineage found; or a source sqlglot couldn't place in the schema."""


@dataclass(frozen=True, order=True)
class Input:
    """One input column of an output column, or of a whole output table, and how OpenLineage would say it's used."""
    table: str            # "crm.learners"
    column: str           # "email"
    type: str             # DIRECT: the value comes from it. INDIRECT: it decides which rows exist, or how they're grouped
    subtype: str          # IDENTITY, TRANSFORMATION or AGGREGATION; or JOIN, FILTER, GROUP_BY, SORT, WINDOW, CONDITIONAL
    masking: bool = False  # a hash obfuscated the value on the way

    @property
    def source(self) -> str:
        return f"{self.table}.{self.column}"


@dataclass(frozen=True, order=True)
class Edge:
    """One edge of the column graph, read back from events."""
    source: str           # an input column: "crm.learners.email"
    target: str           # an output column, "marketing.audience.email", or a table whose rows the input shapes
    type: str
    subtype: str


@dataclass
class Graph:
    """The column graph the events describe: its edges, and each written table's columns, in the order jobs ran."""
    edges: list[Edge]
    columns: dict[str, list[str]]


# ---------------------------------------------------------------- column lineage from SQL


def schema(catalog: Catalog) -> dict:
    """Every table's columns and types, from the catalog's Iceberg metadata, in the shape sqlglot reads a schema."""
    found: dict = {}
    for (namespace,) in catalog.list_namespaces():
        for _, name in catalog.list_tables(namespace):
            fields = catalog.load_table(f"{namespace}.{name}").schema().fields
            found.setdefault(namespace, {})[name] = {f.name: str(f.field_type) for f in fields}
    return found


def sqlglot_sources(statement: str, schema: dict) -> dict[str, list[str]]:
    """What sqlglot.lineage says on its own: each output column, and the source columns its lineage ends in."""
    nodes = sqlglot_lineage(None, statement, schema, dialect=DIALECT)
    return {name: sorted({_leaf_name(n) for n in node.walk() if not n.downstream}) for name, node in nodes.items()}


def column_lineage(statement: str, schema: dict) -> tuple[dict[str, list[Input]], list[Input]]:
    """The column lineage of a statement that writes a table from a query: a CREATE ... AS or an INSERT ... SELECT.
    First, for each column written, the inputs its value comes from: sqlglot.lineage's answer, with each input given an
    OpenLineage type and subtype. Second, the inputs that shape every row: the JOIN, WHERE, GROUP BY, HAVING, QUALIFY
    and ORDER BY columns of every SELECT in the statement, which sqlglot.lineage never returns. An INSERT writes the
    target's columns as the schema has them, matched by position: the ones it lists, or else all of them, never the
    SELECT's names. Raises LineageError for any other statement, MERGE included, whatever its USING names; when sqlglot
    finds no lineage; when an INSERT's SELECT has more or fewer values than the columns it writes; and for a source
    sqlglot can't place in the schema, or a column the schema doesn't have."""
    parsed = sqlglot.parse_one(statement, read=DIALECT)
    query = _query(parsed)
    nodes = sqlglot_lineage(None, parsed, schema, dialect=DIALECT)
    if not nodes:
        raise LineageError(f"{pipelines.target(statement)}: sqlglot found no column lineage in this statement")
    names = _inserted(parsed, schema) if isinstance(parsed, exp.Insert) else list(nodes)
    if len(names) != len(nodes):
        raise LineageError(f"{pipelines.target(statement)}: the INSERT writes {len(names)} columns, and its SELECT "
                           f"has {len(nodes)} values")
    fields = {name: sorted(set(_value_inputs(node, schema))) for name, node in zip(names, nodes.values())}
    return fields, _row_inputs(query, schema)


def tables_read(statement: str) -> list[str]:
    """The tables a statement reads, as "namespace.table". Raises LineageError as column_lineage does for a statement
    that isn't a CREATE ... AS or an INSERT ... SELECT."""
    query = _query(sqlglot.parse_one(statement, read=DIALECT))
    return sorted({f"{source.db}.{source.name}" for scope in traverse_scope(query)
                   for source in scope.sources.values() if isinstance(source, exp.Table)})


def _query(parsed: exp.Expr) -> exp.Query:
    """The query a CREATE ... AS or an INSERT ... SELECT writes its table from. Every other statement is refused. For a
    MERGE, sqlglot.lineage reads the last query it reaches as if that were what the MERGE writes: nothing when USING
    names a table or a CTE and no subquery follows, and otherwise the last subquery's columns, written by the MERGE or
    not."""
    query = parsed.expression if isinstance(parsed, (exp.Create, exp.Insert)) else None
    if not isinstance(query, exp.Query):
        written = parsed.this.this if isinstance(parsed.this, exp.Schema) else parsed.this
        where = f"{written.db}.{written.name}: " if isinstance(written, exp.Table) else ""
        kind = parsed.key.upper() + (" without a query" if isinstance(parsed, (exp.Create, exp.Insert)) else "")
        raise LineageError(f"{where}lineage is read only from CREATE ... AS and INSERT ... SELECT, not from {kind}")
    return query


def _inserted(insert: exp.Insert, schema: dict) -> list[str]:
    """The columns an INSERT writes, in order, as the target's schema has them: the ones it lists, or all of them."""
    table = insert.this.this if isinstance(insert.this, exp.Schema) else insert.this
    columns = list(schema.get(table.db, {}).get(table.name, {}))
    if not columns:
        raise LineageError(f"{table.db}.{table.name}: not in the schema, so the INSERT's columns aren't known")
    listed = [column.name for column in insert.this.expressions] if isinstance(insert.this, exp.Schema) else columns
    for name in listed:
        if name not in columns:
            raise LineageError(f"{table.db}.{table.name} has no column {name}")
    return listed


def _leaf_name(node: Node) -> str:
    if isinstance(node.expression, exp.Table):
        return f"{node.expression.db}.{node.expression.name}.{node.name.rpartition('.')[2]}"
    return f"unknown source of {node.name}"


def _value_inputs(root: Node, schema: dict) -> list[Input]:
    """Walk sqlglot's tree for one output column, classifying each hop by where the column sits in the projection."""
    found = []

    def walk(node: Node, hops: list[tuple[str, str, bool]]) -> None:
        for child in node.downstream:
            for hop in _hops(node.expression, child.name):
                if child.downstream:
                    walk(child, hops + [hop])
                elif (found_input := _input(child, schema, hops + [hop])) is not None:
                    found.append(found_input)

    walk(root, [])
    return found


def _hops(projection: exp.Expr, name: str) -> set[tuple[str, str, bool]]:
    """How each use of one of sqlglot's child nodes feeds the projection. A child with no table is a subquery's output,
    such as the consent check `learner_id IN (SELECT ...)` in chapter 6's column gate: it's typed by where its subquery
    sits, so a subquery in a CASE condition is INDIRECT, as any column there is."""
    alias, _, column = name.rpartition(".")
    uses = [c for c in projection.find_all(exp.Column) if c.name == column and c.table == alias]
    if not uses and not alias:
        uses = [s for s in projection.find_all(exp.Select) if s is not projection and column in s.named_selects]
    return {_kind(c, projection) for c in uses} or {("DIRECT", "TRANSFORMATION", False)}


def _kind(column: exp.Column, projection: exp.Expr) -> tuple[str, str, bool]:
    """How one use of a column inside a projection feeds its value, as an OpenLineage type, subtype and masking flag.
    A column inside an aggregate's FILTER, a window's PARTITION BY or ORDER BY, or a CASE or IF condition only decides
    which values count: INDIRECT. So does the operand of a simple CASE, as in `case l.status when 'active' then ...`.
    Anything else is DIRECT, and COALESCE's arguments count as values, since any of them can become the output's value,
    though OpenLineage lists COALESCE under CONDITIONAL."""
    subtype, masking, node = "IDENTITY", False, column
    while node is not projection and node.parent is not None:
        parent, key = node.parent, node.arg_key
        if isinstance(parent, exp.Filter) and key == "expression":
            return "INDIRECT", "FILTER", False
        if isinstance(parent, exp.Window) and key in ("partition_by", "order"):
            return "INDIRECT", "WINDOW", False
        if isinstance(parent, (exp.If, exp.Case)) and key == "this":
            return "INDIRECT", "CONDITIONAL", False
        if isinstance(parent, exp.AggFunc):
            subtype = "AGGREGATION"
        elif not isinstance(parent, exp.Alias) and subtype == "IDENTITY":
            subtype = "TRANSFORMATION"
        masking = masking or isinstance(parent, HASHES)
        node = parent
    return "DIRECT", subtype, masking


def _input(leaf: Node, schema: dict, hops: list[tuple[str, str, bool]]) -> Input | None:
    """The input a leaf of sqlglot's tree stands for. None for a leaf computed from no column, such as a constant or a
    count(*) in a CTE; LineageError for one that reads a column sqlglot couldn't place, such as a Placeholder."""
    if not isinstance(leaf.expression, exp.Table):
        if isinstance(leaf.expression, exp.Placeholder) or leaf.expression.find(exp.Column):
            raise LineageError(f"no source table found for {leaf.name}: qualify the column, or add its table to the "
                               "schema")
        return None
    table, column = leaf.expression, leaf.name.rpartition(".")[2]
    if column not in schema.get(table.db, {}).get(table.name, {}):
        raise LineageError(f"{table.db}.{table.name} has no column {column}")
    indirect = next((hop for hop in hops if hop[0] == "INDIRECT"), None)
    kind, subtype, masking = indirect or ("DIRECT", max((h[1] for h in hops), key=DIRECT_ORDER.index),
                                          any(h[2] for h in hops))
    return Input(f"{table.db}.{table.name}", column, kind, subtype, masking)


def _row_inputs(query: exp.Expr, schema: dict) -> list[Input]:
    qualified = qualify(query.copy(), schema=schema, dialect=DIALECT, validate_qualify_columns=False)
    found = set()
    for scope in traverse_scope(qualified):
        for column in scope.columns:
            node = column
            while node.parent is not None and node.parent is not scope.expression:
                node = node.parent
            subtype = ROW_CLAUSES.get(node.arg_key) if node.parent is scope.expression else None
            if subtype:
                found.update(Input(table, name, "INDIRECT", subtype) for table, name in _resolve(scope, column))
    return sorted(found)


def _resolve(scope: Scope, column: exp.Column) -> list[tuple[str, str]]:
    """The base table columns a column stands for, following CTEs and subqueries to the tables they read."""
    source = scope.sources.get(column.table)
    if isinstance(source, exp.Table):
        return [(f"{source.db}.{source.name}", column.name)]
    if isinstance(source, Scope):
        projection = next((s for s in source.expression.selects if s.alias_or_name == column.name), None)
        inner = projection.find_all(exp.Column) if projection else []
        return [found for column in inner for found in _resolve(source, column)]
    return []


# ---------------------------------------------------------------- run events


def client(path: Path) -> OpenLineageClient:
    """An OpenLineage client that appends every event to one file, one JSON object per line."""
    return OpenLineageClient(transport=FileTransport(FileConfig(log_file_path=str(path), append=True)))


def record(client: OpenLineageClient, job: str, statement: str, catalog: Catalog, work: Callable[[], object],
           at: datetime = NIGHTLY) -> str:
    """Do one job's work, `statement` being the SQL that describes it, and tell OpenLineage: a START event, then COMPLETE
    with the output's schema and column lineage, or FAIL if the work raised. The lineage is read before the job runs,
    so a statement whose lineage can't be read never runs. Both events carry the time `at`, and the run's ID is derived
    from it and the job's name. Returns the output's name."""
    fields, rows = column_lineage(statement, schema(catalog))
    output = pipelines.target(statement)
    inputs = [InputDataset(NAMESPACE, name) for name in tables_read(statement)]
    run_id = str(generate_static_uuid(at, job.encode()))

    def emit(state: RunState, facets: dict | None = None) -> None:
        client.emit(RunEvent(eventType=state, eventTime=at.isoformat(), run=Run(runId=run_id), job=Job(JOBS, job),
                             inputs=inputs, outputs=[OutputDataset(NAMESPACE, output, facets=facets or {})]))

    emit(RunState.START)
    try:
        work()
    except Exception:
        emit(RunState.FAIL)
        raise
    written = [schema_dataset.SchemaDatasetFacetFields(f.name, str(f.field_type))
               for f in catalog.load_table(output).schema().fields]
    emit(RunState.COMPLETE, {
        "schema": schema_dataset.SchemaDatasetFacet(fields=written),
        "columnLineage": lineage_facet.ColumnLineageDatasetFacet(
            fields={name: lineage_facet.Fields(inputFields=_input_fields(inputs)) for name, inputs in fields.items()},
            dataset=_input_fields(rows))})
    return output


def run(catalog: Catalog, name: str, client: OpenLineageClient, at: datetime = NIGHTLY) -> str:
    """Run one of the platform's pipelines, as lib.pipelines.run does, with its lineage recorded."""
    return record(client, name, pipelines.sql(name), catalog, lambda: pipelines.run(catalog, name), at)


def run_all(catalog: Catalog, path: Path, at: datetime = NIGHTLY) -> list[str]:
    """Run every pipeline in pipelines/, in name order, with their events appended to the file at `path`."""
    emitter = client(path)
    return [run(catalog, sql.stem, emitter, at) for sql in sorted(pipelines.PIPELINES.glob("*.sql"))]


def _input_fields(inputs: list[Input]) -> list[lineage_facet.InputField]:
    grouped: dict[tuple[str, str], list[lineage_facet.Transformation]] = {}
    for i in inputs:
        transformation = lineage_facet.Transformation(i.type, i.subtype, masking=i.masking)
        grouped.setdefault((i.table, i.column), []).append(transformation)
    return [lineage_facet.InputField(NAMESPACE, table, column, transformations=transformations)
            for (table, column), transformations in grouped.items()]


# ---------------------------------------------------------------- the graph, read back from events


def read_events(path: Path) -> list[dict]:
    """The events in a file the client wrote, oldest first: none if it was never written."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def graph(events: list[dict]) -> Graph:
    """The column graph that the COMPLETE events' column lineage describes."""
    edges, columns = set(), {}
    for event in events:
        if event.get("eventType") != "COMPLETE":
            continue
        for output in event.get("outputs", []):
            facets = output.get("facets", {})
            columns[output["name"]] = [f["name"] for f in facets.get("schema", {}).get("fields", [])]
            lineage = facets.get("columnLineage", {})
            targets = [(f"{output['name']}.{name}", spec["inputFields"])
                       for name, spec in lineage.get("fields", {}).items()]
            for target, inputs in targets + [(output["name"], lineage.get("dataset", []))]:
                edges.update(Edge(f"{i['name']}.{i['field']}", target, t["type"], t.get("subtype", ""))
                             for i in inputs for t in i.get("transformations", []))
    return Graph(sorted(edges), columns)


def table_of(name: str) -> str:
    """"crm.learners" for "crm.learners.email"."""
    return ".".join(name.split(".")[:2])


def downstream(graph: Graph, start: str) -> list[Edge]:
    """Every edge a change to a column can travel, at any depth: what the change can break. An edge into a whole table
    means every row of it can change, so the walk goes on from each of that table's columns."""
    reached, queue, seen = [], [start], {start}
    while queue:
        node = queue.pop(0)
        for edge in graph.edges:
            if edge.source == node or (node.count(".") == 1 and table_of(edge.source) == node):
                if edge not in reached:
                    reached.append(edge)
                if edge.target not in seen:
                    seen.add(edge.target)
                    queue.append(edge.target)
    return reached


def columns_reached(graph: Graph, edges: list[Edge]) -> list[str]:
    """The output columns some edges reach: each edge's column, or every column of a table whose rows it shapes."""
    found: list[str] = []
    for edge in edges:
        whole_table = edge.target.count(".") == 1
        names = [f"{edge.target}.{c}" for c in graph.columns.get(edge.target, [])] if whole_table else [edge.target]
        found += [name for name in names if name not in found]
    return found


# ---------------------------------------------------------------- classifications along lineage


def rules(path: Path = RULES) -> dict:
    """How classifications travel along lineage (governance/lineage.toml)."""
    return tomllib.loads(path.read_text(encoding="utf-8"))


def declared(tables: dict[str, metadata.Table] | None = None) -> dict[str, str]:
    """Each column's classification as governance/metadata.toml declares it, keyed "table.column"."""
    tables = metadata.load() if tables is None else tables
    return {f"{t.name}.{c.name}": c.classification for t in tables.values() for c in t.columns.values()}


def propose(graph: Graph, known: dict[str, str], through: list[str] | None = None) -> dict[str, tuple[str, list[Edge]]]:
    """For every column a job wrote, the classification lineage proposes: the stricter of its inputs' classifications
    (lib.metadata.stricter), counting the edges whose type `through` lists (governance/lineage.toml by default). An
    input a job wrote counts at the stricter of its declared and proposed classifications, and an input with no
    classification counts as sensitive, so a gap in the catalog can only raise a proposal. Returns each column's
    proposal with the edges that decided it, DIRECT ones first."""
    through = rules()["propagate_through"] if through is None else through
    proposed: dict[str, tuple[str, list[Edge]]] = {}

    def level(column: str) -> str:
        found = [c for c in (known.get(column), proposed.get(column, (None,))[0]) if c]
        return reduce(stricter, found) if found else "sensitive"

    for table, columns in graph.columns.items():
        for column in (f"{table}.{name}" for name in columns):
            edges = [e for e in graph.edges if e.type in through and e.target in (column, table)]
            classification = reduce(stricter, (level(e.source) for e in edges), "public")
            deciding = [e for e in edges if level(e.source) == classification]
            proposed[column] = (classification, sorted(deciding, key=lambda e: e.type != "DIRECT"))   # DIRECT first
    return proposed


def understated(proposed: dict[str, tuple[str, list[Edge]]], known: dict[str, str]) -> list[str]:
    """Columns declared less protected than lineage proposes, such as a copy of personal data tagged internal."""
    return [f"{column}: declared {known[column]}, lineage proposes {classification}"
            for column, (classification, _) in proposed.items()
            if column in known and stricter(known[column], classification) != known[column]]


# ---------------------------------------------------------------- did the lineage arrive?


def missing_runs(jobs: list[str], events: list[dict]) -> list[str]:
    """Jobs that ran but sent no COMPLETE event: their lineage never arrived."""
    done = {e["job"]["name"] for e in events if e.get("eventType") == "COMPLETE"}
    return [job for job in jobs if job not in done]


def unaccounted(catalog: Catalog, events: list[dict]) -> list[str]:
    """Tables in the catalog that are neither one of the platform's sources nor written by a job lineage knows about:
    something wrote them without telling OpenLineage."""
    written = {o["name"] for e in events if e.get("eventType") == "COMPLETE" for o in e.get("outputs", [])}
    tables = {f"{namespace}.{name}" for (namespace,) in catalog.list_namespaces()
              for _, name in catalog.list_tables(namespace)}
    return sorted(tables - written - set(TABLES))
