"""Access decisions enforced on queries. Every query is rewritten before DuckDB runs it: each catalog table it reads is
replaced by a subquery that keeps only the rows the user's decision allows, and shows each column as the decision says.

The rewrite fails closed. Every source a query reads, at every level, must be a catalog table, a CTE in scope or a
subquery: anything else (a file, a table function, a name the catalog doesn't hold) is denied, and so are functions
DuckDB marks as having side effects, and any statement but a single query. A column the query doesn't name comes back
as a typed NULL, so a read the rewrite can't see, such as a whole row as a struct, gets no value it wasn't decided for.
A column it does name is gated on the row filters, so the query's own conditions get nothing from a row the filters
drop, in whatever order DuckDB evaluates them. A service calls `answer`, which turns any engine error into one generic
error and logs the engine's message, which can hold a value, for the platform team.

Two regression tests guard the layer, for every change to it and every DuckDB upgrade: `source_cases` for the
allowlist, and `order_cases` for the order in which DuckDB evaluates a user's conditions and the row filters."""
from __future__ import annotations

import contextlib
import logging
import re
import tomllib
from pathlib import Path

import duckdb
import sqlglot
from pyiceberg.catalog import Catalog
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from lib import policy
from lib.catalog import open_catalog
from lib.warehouse import connect

SETTINGS = Path(__file__).resolve().parent.parent / "governance" / "enforcement.toml"
DIALECT = "duckdb"
# The book's pseudonymization key, fixed so outputs never change. A real one is the enforcement layer's secret, read
# from a secret manager when the layer starts: never in a policy, a query, the repository or a log.
EXAMPLE_KEY = "example-key-not-a-secret"

# Each mask as SQL over `value`: the one place a mask's name becomes SQL. `redact` is a typed NULL, built in `masked`.
EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+"
PHONE = r"\+?\(?[0-9][0-9 ().-]{6,}[0-9]"
MASKS = {
    "last4": "right(regexp_replace(value, '[^0-9]', '', 'g'), 4)",
    "pseudonymize": "sha256($pseudonym_key || CAST(value AS VARCHAR))",
    "redact_contacts": f"regexp_replace(regexp_replace(value, '{EMAIL}', '[email]', 'g'), '{PHONE}', '[phone]', 'g')",
}


# The platform team's log: engine errors go here, never to users, because an engine's message can hold a value.
OPERATOR_LOG = logging.getLogger("lib.enforce")


class Denied(Exception):
    """A query the enforcement layer refuses. Its message names tables, columns and reasons, never a value."""


class QueryFailed(Exception):
    """A query the engine couldn't run, as `answer` reports it to the user: the same message for every failure."""


@contextlib.contextmanager
def quiet_parser():
    """sqlglot logs a warning that quotes the whole statement, values included, whenever it keeps one as text, such as
    a CALL. The layer parses with it silenced, so no query's values reach whatever collects the process's output."""
    parser_log = logging.getLogger("sqlglot")
    level = parser_log.level
    parser_log.setLevel(logging.ERROR)
    try:
        yield
    finally:
        parser_log.setLevel(level)


def settings(path: Path = SETTINGS) -> dict:
    """The enforcement layer's settings (governance/enforcement.toml): rules of thumb, kept out of code."""
    return tomllib.loads(path.read_text(encoding="utf-8"))


def masked(kind: str, value: exp.Expression, type_: str) -> exp.Expression:
    """One column mask as SQL, applied to `value`."""
    if kind == "redact":
        return exp.cast(exp.null(), exp.DataType.build(type_, dialect=DIALECT))
    if kind not in MASKS:
        raise Denied(f"the policy names a mask the enforcement layer doesn't know: {kind}")
    with quiet_parser():
        template = sqlglot.parse_one(MASKS[kind], read=DIALECT)
    return template.transform(lambda node: value.copy() if isinstance(node, exp.Column) and node.name == "value" else node)


def row_filter(rule: dict, columns: dict[str, str], table: str | None = "source") -> exp.Expression:
    """One row filter as a SQL condition on a table's columns, built from the decision's data: its values become
    literals, never SQL text. A shape the layer doesn't know denies the query."""
    if set(rule) == {"learner_consented"}:
        if "learner_id" not in columns:
            raise Denied("a consent filter needs a learner_id column")
        consented = (exp.select(exp.column("learner_id", table="consent"))
                     .from_(exp.table_("learners", db="crm", alias="consent"))
                     .where(exp.column(rule["learner_consented"], table="consent").is_(exp.true())))
        return exp.column("learner_id", table=table).isin(query=consented)
    if rule.get("column") not in columns:
        raise Denied(f"a row filter names a column the table doesn't have: {rule.get('column')}")
    column = exp.column(rule["column"], table=table)
    if set(rule) == {"column", "equals"}:
        return column.eq(exp.convert(rule["equals"]))
    if set(rule) == {"column", "in"}:
        return column.isin(*map(exp.convert, rule["in"])) if rule["in"] else exp.false()
    raise Denied(f"a row filter of a shape the enforcement layer doesn't know: {sorted(rule)}")


class Enforcer:
    """The enforcement layer over one catalog: a DuckDB connection only it uses, the catalog's schema, the policies
    and the pseudonymization key. Users never get the connection, and a service exposes only `answer`, which returns
    rows. `sql` returns a relation, a handle on the layer's connection, for the layer's own wrappers; `run` executes SQL
    the layer wrote, with the key attached."""

    def __init__(self, catalog: Catalog, key: str = EXAMPLE_KEY, rego=None):
        self.connection = connect(catalog)
        self.connection.execute("set enable_external_access = false")      # no files: read_parquet and the like fail
        # The schema: the tables the catalog lists, each with the columns and types of the view lib.warehouse made for it.
        self.schema: dict[str, dict[str, dict[str, str]]] = {}
        for (namespace,) in catalog.list_namespaces():
            for _, name in catalog.list_tables(namespace):
                table = self.connection.table(f"{namespace}.{name}")
                self.schema.setdefault(namespace, {})[name] = dict(zip(table.columns, map(str, table.types)))
        # Functions a query may not call: those DuckDB marks as having side effects, and the macros that call them.
        functions = self.connection.sql(
            "select function_name, has_side_effects, macro_definition from duckdb_functions()").fetchall()
        self.side_effects = {name for name, flagged, _ in functions if flagged}
        while calling := {name for name, _, body in functions if body and name not in self.side_effects
                          and any(re.search(rf"\b{re.escape(f)}\s*\(", body, re.I) for f in self.side_effects)}:
            self.side_effects |= calling
        self.rego = rego or policy.interpreter()
        self.key = key

    def columns(self, table: str) -> dict[str, str]:
        namespace, name = table.split(".")
        return self.schema[namespace][name]

    def plan(self, sql: str) -> tuple[exp.Expression, dict[str, set[str]], dict[str, set[str]]]:
        """The query parsed, checked and qualified against the catalog's schema, with `*` expanded; the columns it
        reads from each catalog table; and which tables each alias in it stands for."""
        with quiet_parser():
            statements = sqlglot.parse(sql, read=DIALECT)
        if len(statements) != 1 or not isinstance(statements[0], exp.Query):
            raise Denied("only a single query is allowed")
        tree = normalize_identifiers(statements[0], dialect=DIALECT)
        if tree.find(exp.Placeholder, exp.Parameter):
            raise Denied("queries can't carry parameters: the enforcement layer's own values travel as parameters")
        self.check_sources(tree)
        for function in tree.find_all(exp.Func):      # named as DuckDB will see it: the SQL the layer hands it
            name = function.name if isinstance(function, exp.Anonymous) else function.sql(DIALECT).split("(", 1)[0]
            if (name := name.strip('"').lower()) in self.side_effects:
                raise Denied(f"a function DuckDB marks as having side effects: {name}")
        try:
            tree = qualify(tree, schema=self.schema, dialect=DIALECT)
        except OptimizeError as error:
            raise Denied(str(error)) from None
        aliases: dict[str, set[str]] = {}
        for scope in traverse_scope(tree):
            for alias, source in scope.sources.items():
                if isinstance(source, exp.Table) and source.db:
                    aliases.setdefault(alias, set()).add(f"{source.db}.{source.name}")
        reads = {f"{t.db}.{t.name}": set() for t in tree.find_all(exp.Table) if t.db}
        for column in tree.find_all(exp.Column):
            for table in aliases.get(column.table, ()):
                if column.name in self.columns(table):
                    reads[table].add(column.name)
        return tree, reads, aliases

    def check_sources(self, node: exp.Expression, ctes: frozenset[str] = frozenset()) -> None:
        """Deny the query unless every source it reads, at every level, is a catalog table, a CTE in scope where it's
        named, or a subquery: an allowlist, so a kind of source nobody thought of is denied. A CTE is in scope in its
        own query and below it, and in the CTEs after it. A recursive CTE also sees itself in its UNION's second
        branch: anywhere else, DuckDB binds the name to whatever else holds it."""
        with_ = node.args.get("with_") if isinstance(node, exp.Query) else None
        if with_:
            visible = set(ctes)
            for cte in with_.expressions:
                body = cte.this
                if with_.recursive and isinstance(body, exp.SetOperation):
                    for part in body.iter_expressions():
                        self.check_sources(part, frozenset(visible | {cte.alias} if part is body.expression else visible))
                else:
                    self.check_sources(body, frozenset(visible))
                visible.add(cte.alias)
            ctes = frozenset(visible)
        if isinstance(node, (exp.From, exp.Join)) and not isinstance(node.this, (exp.Table, exp.Subquery, exp.Lateral)):
            raise Denied(f"not a catalog table, a CTE or a subquery: {node.this.sql(DIALECT)}")
        elif isinstance(node, exp.Subquery) and not isinstance(node.this, exp.Query):     # such as a join in parentheses
            raise Denied(f"not a subquery: {node.sql(DIALECT)}")
        elif isinstance(node, exp.Table):
            named = isinstance(node.this, exp.Identifier) and not node.catalog
            in_catalog = named and node.db and node.name in self.schema.get(node.db, {})
            if not (in_catalog or named and not node.db and node.name in ctes):
                raise Denied(f"not a table in the catalog: {node.sql(DIALECT)}")
        elif isinstance(node, exp.Lateral) and not isinstance(node.this, exp.Subquery):
            raise Denied(f"a LATERAL that isn't a subquery: {node.sql(DIALECT)}")
        for child in node.iter_expressions():
            if child is not with_:
                self.check_sources(child, ctes)

    def reads(self, sql: str) -> dict[str, list[str]]:
        """Every catalog table the query reads, and the columns it reads from each."""
        return {table: sorted(columns) for table, columns in sorted(self.plan(sql)[1].items())}

    def decide(self, user: str, reads: dict[str, set[str]], purpose: str | None = None) -> dict[str, dict]:
        """The user's decision for each table, asked about the columns the query reads. One denied table denies all."""
        decisions = {}
        for table, columns in sorted(reads.items()):
            decision = policy.decide(policy.request(user, table, sorted(columns), purpose), self.rego)
            if not decision["allow"]:
                raise Denied(f"{user} may not read {table}: {'; '.join(decision['reasons'])}")
            decisions[table] = decision
        return decisions

    def protected(self, table: str, decision: dict, read: set[str]) -> exp.Select:
        """The subquery that stands in for a table: every column in the table's order, each as stored, masked, or a
        typed NULL when the query doesn't read it, and only the rows every row filter keeps. Each column the query reads
        is also gated on the row filters, CASE WHEN <filters> THEN <value> END, so the query's own conditions depend on
        the filters' result: DuckDB may run them before the WHERE below, but then they see NULLs, never a dropped row."""
        columns = self.columns(table)
        for rule in decision["row_filters"]:
            consent = rule.get("learner_consented")
            if consent is not None and consent not in self.schema.get("crm", {}).get("learners", {}):
                raise Denied(f"a consent filter names a column crm.learners doesn't have: {consent}")
        filters = [row_filter(rule, columns) for rule in decision["row_filters"]]
        kept = exp.and_(*filters, copy=False) if filters else None
        masks = decision["column_masks"]
        shown = []
        for column, type_ in columns.items():
            value = exp.column(column, table="source")
            if column not in read:
                value = masked("redact", value, type_)
            else:
                if column in masks:
                    value = masked(masks[column], value, type_)
                if kept is not None:
                    value = exp.case().when(kept.copy(), value)
            shown.append(exp.alias_(value, column))
        namespace, name = table.split(".")
        query = exp.select(*shown).from_(exp.table_(name, db=namespace, alias="source"))
        return query.where(kept, copy=False) if kept is not None else query

    def rewrite(self, sql: str, user: str, purpose: str | None = None) -> str:
        """The query as DuckDB will run it: each catalog table replaced, under its own alias, by its protected subquery."""
        tree, reads, _ = self.plan(sql)
        decisions = self.decide(user, reads, purpose)
        for table in list(tree.find_all(exp.Table)):
            name = f"{table.db}.{table.name}"
            if name in decisions:
                alias = exp.TableAlias(this=exp.to_identifier(table.alias_or_name, quoted=True))
                subquery = exp.Subquery(this=self.protected(name, decisions[name], reads[name]), alias=alias)
                for modifier in ("pivots", "sample"):          # what the query applies to the table, kept for the subquery
                    subquery.set(modifier, table.args.get(modifier))
                table.replace(subquery)
        return tree.sql(DIALECT)

    def mask_select_list(self, sql: str, user: str) -> str:
        """The rewrite that leaks, kept to show why: it masks each masked column where the query's SELECT list names
        it, and leaves the tables, and every other reference to them, as they are."""
        tree, reads, aliases = self.plan(sql)
        decisions = self.decide(user, reads)
        for column in [c for projection in tree.selects for c in projection.find_all(exp.Column)]:
            for table in aliases.get(column.table, ()):
                kind = decisions[table]["column_masks"].get(column.name)
                if kind:
                    column.replace(masked(kind, column, self.columns(table)[column.name]))
                    break
        return tree.sql(DIALECT)

    def run(self, rewritten: str) -> duckdb.DuckDBPyRelation:
        """Run SQL the layer wrote. The key travels as a parameter, so it's never in the query's text, which a log or
        an error message may show."""
        with quiet_parser():
            placeholders = sqlglot.parse_one(rewritten, read=DIALECT).find_all(exp.Placeholder)
        uses_key = any(placeholder.name == "pseudonym_key" for placeholder in placeholders)
        return self.connection.sql(rewritten, params={"pseudonym_key": self.key} if uses_key else None)

    def sql(self, sql: str, user: str, purpose: str | None = None) -> duckdb.DuckDBPyRelation:
        """Run a user's query as that user: rewritten, then run. Raises Denied for anything the layer refuses. The
        layer's own call, for its examples and wrappers: the relation is lazy, so DuckDB's errors arrive when it's
        fetched, with DuckDB's own message, and it's a handle on the layer's connection, whose `query` method runs any
        SQL there. Never hand it to a user: at a service's boundary, call `answer`, which returns rows."""
        return self.run(self.rewrite(sql, user, purpose))


def answer(enforcer: Enforcer, sql: str, user: str, purpose: str | None = None) -> list[tuple]:
    """A user's query at the service boundary: run as that user through the layer and fetched. Returns the rows, as
    tuples, which hold no reference to the layer's connection, as a relation would.
    A refusal raises Denied, whose message names no value. Any failure in the engine raises QueryFailed, with one
    message for every failure, and DuckDB's own message, which can quote a value, goes to OPERATOR_LOG only. Works
    with anything whose `sql(query, user, purpose)` returns a relation, such as chapter 8's audited layer."""
    try:
        return enforcer.sql(sql, user, purpose).fetchall()
    except SqlglotError:
        engine_said = None
    except duckdb.Error as error:
        engine_said = str(error)
    # Raised outside the except blocks, so neither error carries DuckDB's, not even as its __context__.
    if engine_said is None:
        raise Denied("the query doesn't parse")
    OPERATOR_LOG.error("a query by %s failed in the engine: %s", user, engine_said)
    raise QueryFailed("the query failed: the platform team can see why")


def observe(enforcer: Enforcer, user: str, table: str, columns: list[str], purpose: str | None = None) -> dict | None:
    """What a user gets when they read some columns of a table through the layer, against what's stored: None when
    denied, else the rows they get, the rows stored, and the columns whose values differ from storage. Rows are
    matched on the table's first column, its ID."""
    key = next(iter(enforcer.columns(table)))
    try:
        rewritten = enforcer.rewrite(f"select {', '.join(dict.fromkeys([key, *columns]))} from {table}", user, purpose)
    except Denied:
        return None
    differs = [f"count(*) filter (where seen.{c}::varchar is distinct from stored.{c}::varchar)" for c in columns]
    found = enforcer.run(f"with seen as ({rewritten}) select count(*), {', '.join(differs)} "
                         f"from seen join {table} as stored using ({key})").fetchall()[0]
    stored = enforcer.connection.sql(f"select count(*) from {table}").fetchall()[0][0]
    return {"rows": found[0], "stored": stored, "changed": [c for c, n in zip(columns, found[1:]) if n]}


def minimum_group_size() -> int:
    """The fewest people a reported group may hold (governance/enforcement.toml)."""
    return settings()["reports"]["minimum_group_size"]


def agrees(case: policy.Case, got: dict | None) -> bool:
    """Whether what a user gets (from `observe`) agrees with a who-sees-what case's decision: denied exactly when the
    case is denied, fewer rows than stored exactly when it's filtered, and values that differ from storage in exactly
    the columns it masks."""
    if not case.allowed or got is None:
        return not case.allowed and got is None
    return (got["rows"] < got["stored"]) == bool(case.filtered_on) and set(got["changed"]) == set(case.masked)


# ---------------------------------------------------------------- regression tests, for the examples and for CI

# Queries that read data other than through a catalog table, or change something: the layer must deny each one.
# They name crm.learners and support.tickets, and `scan_crm_learners`, the scan lib.warehouse registers for the first.
MUST_DENY = {
    "a table function in FROM": "select count(*) from query_table('crm.learners')",
    "a table function in a scalar subquery": "select (select count(*) from query_table('crm.learners'))",
    "a table function under LATERAL": "select count(*) from support.tickets, lateral query_table('crm.learners')",
    "a catalog-qualified name": "select count(*) from memory.crm.learners",
    "an information_schema view": "select table_name from information_schema.tables",
    "a CTE's name outside its scope": "select count(*) from (with other as (select 1) select 1), other",
    "a CTE's name before the CTE": "with a as (select * from scan_crm_learners), scan_crm_learners as (select 1) "
                                   "select count(*) from a",
    "a function that writes DuckDB's log": "select write_log('an entry', log_type := 'QueryLog')",
    "a statement sqlglot keeps as text": "call enable_logging('QueryLog')",
}
# What the allowlist must still let through.
MUST_ALLOW = {
    "a CTE, a join and a scalar subquery": "with open as (select learner_id from support.tickets where status = 'open') "
                                           "select count(*) from crm.learners as l join open using (learner_id) "
                                           "where l.signup_at < (select max(opened_at) from support.tickets)",
}


def source_cases(enforcer: Enforcer) -> list[dict]:
    """The allowlist's regression test: every query in MUST_DENY, a call to each function in `enforcer.side_effects`
    (named "the function <name>"), and every query in MUST_ALLOW, each checked by `Enforcer.plan`, so no policy is
    asked. One dict per case: "case", "query", "must" ("deny" or "allow"), "denied" (bool), "reason" (the denial's
    message, or None) and "logged" (whether sqlglot logged anything while the layer parsed it, which would quote the
    query). A case passes when "denied" is (must == "deny") and nothing was logged. The enforcer's catalog must hold
    crm.learners and support.tickets."""
    functions = [(f"the function {name}", f"select {name}()") for name in sorted(enforcer.side_effects)]
    cases = [("deny", case, query) for case, query in [*MUST_DENY.items(), *functions]]
    cases += [("allow", case, query) for case, query in MUST_ALLOW.items()]
    heard, listener = [], logging.Handler()
    listener.emit = heard.append
    logging.getLogger("sqlglot").addHandler(listener)
    results = []
    try:
        for must, case, query in cases:
            before = len(heard)
            try:
                enforcer.plan(query)
                reason = None
            except Denied as error:
                reason = str(error)
            results.append({"case": case, "query": query, "must": must, "denied": reason is not None,
                            "reason": reason, "logged": len(heard) > before})
    finally:
        logging.getLogger("sqlglot").removeHandler(listener)
    return results


# Each shape of row filter the layer writes, as a real user's decision has it: (user, table). Each table has a `body`.
ORDER_SHAPES = {"equality": ("agent-a05", "support.tickets"), "IN list": ("instructor-17", "learning.reviews"),
                "consent semi-join": ("assistant-1", "learning.reviews")}


def order_cases() -> list[dict]:
    """The evaluation-order regression test, on a catalog of its own. For each shape in ORDER_SHAPES, a copy of the
    user's table whose `body` holds digits in the rows the user's row filter keeps and text in the rows it drops, so
    a query that casts `body` to a number fails only if the cast sees a dropped row's value. That query runs over the
    filter in a WHERE alone, and over the subquery the layer writes. One dict per shape: "shape", "user", "table",
    "safe_with_where_only" and "safe_as_written" (bools: True when the cast never saw a dropped row's value). The
    layer passes when every "safe_as_written" is True."""
    catalog = open_catalog(sorted({"crm.learners", *(table for _, table in ORDER_SHAPES.values())}))
    layer = Enforcer(catalog)
    catalog.create_namespace("probe")
    probes = []
    for shape, (user, table) in ORDER_SHAPES.items():
        [rule] = layer.decide(user, {table: set()})[table]["row_filters"]
        keeps = row_filter(rule, layer.columns(table)).sql(DIALECT)
        copy = layer.connection.sql(f"select source.* exclude (body), case when {keeps} then '0' else 'dropped' end "
                                    f"as body from {table} as source").to_arrow_table()
        probe = "probe." + re.sub(r"\W+", "_", shape.lower())
        catalog.create_table(probe, schema=copy.schema).append(copy)
        probes.append((shape, user, table, probe, rule))
    enforcer = Enforcer(catalog, rego=layer.rego)

    def safe(subquery: str) -> bool:
        try:
            enforcer.run(f"select count(*) from ({subquery}) as t where cast(t.body as integer) >= 0").fetchall()
            return True
        except duckdb.ConversionException:
            return False

    results = []
    for shape, user, table, probe, rule in probes:
        keeps = row_filter(rule, enforcer.columns(probe)).sql(DIALECT)
        written = enforcer.protected(probe, {"row_filters": [rule], "column_masks": {}}, {"body"}).sql(DIALECT)
        results.append({"shape": shape, "user": user, "table": table,
                        "safe_with_where_only": safe(f"select source.body from {probe} as source where {keeps}"),
                        "safe_as_written": safe(written)})
    return results
