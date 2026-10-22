"""The platform's audit log. `Audited` wraps the enforcement layer (lib/enforce.py) and writes one record for every
query it's given, before DuckDB runs anything: who asked, and through which assistant; what the query reads; each
table's decision, with its row filters, masks and reasons; and the revision of the policy that decided. Records are
JSON Lines, each carrying the SHA-256 of the line before it. The chain shows an edited or removed record only up to the
last head written down where the log's writer can't change it: after that head, anyone who can write the file can cut
it or rebuild it unseen.

Records hold IDs, the principal's among them, table and column names, decisions and digests. They hold no value from
the data a query read or from the query's text, so chapter 7's erasure of a learner never has to reach them."""
from __future__ import annotations

import contextlib
import functools
import hashlib
import hmac
import json
import logging
import re
import tomllib
from collections.abc import Callable, Iterable
from datetime import datetime, time, timedelta, timezone
from itertools import count
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import sqlglot
from pyiceberg.catalog import Catalog
from regopy import Interpreter
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

from lib import metadata, policy
from lib.enforce import DIALECT, Denied, Enforcer
from lib.platform import DATA, TABLES, TODAY

SETTINGS = policy.GOVERNANCE / "audit.toml"
# The book's key for digests of query text, fixed so outputs never change. A real one is the enforcement layer's
# secret, kept like its pseudonymization key, and a different one.
EXAMPLE_KEY = "example-audit-key-not-a-secret"
GENESIS = "0" * 64                                                   # what the first record's prev_hash points to
MIDNIGHT = datetime.combine(TODAY, time(), tzinfo=timezone.utc)      # the book's "today", never the machine's clock
ENGINE_LOG = "select message from duckdb_logs where type = 'QueryLog'"
DECISION = "data.platform.access.decision"
# Every node that holds a value typed into a query, in any of DuckDB's syntaxes: 'text', 541, $$text$$ and
# $tag$text$tag$, e'text', n'text', TRUE and FALSE, and a JSON path written as a string.
VALUES = (exp.Literal, exp.RawString, exp.Heredoc, exp.ByteString, exp.National, exp.HexString, exp.BitString,
          exp.UnicodeString, exp.Boolean, exp.JSONPath)
# The units DuckDB's date functions and intervals take, with their synonyms (DuckDB 2.0's date part specifiers), and
# the names sqlglot gives them. sqlglot keeps a unit written as a string, such as 'month', as a word, so any other
# word in that place may be a value.
UNITS = set("""century cent centuries c day days d dayofmonth decade dec decades decs hour hr hours hrs h microseconds
    microsecond us usec usecs usecond useconds millennium mil millenniums millennia mils millenium milliseconds
    millisecond ms msec msecs msecond mseconds minute min minutes mins m month mon months mons quarter quarters second
    sec seconds secs s year yr y years yrs dayofweek weekday dow dayofyear doy epoch era isodow isoyear julian
    timezone_hour timezone_minute timezone week weeks w yearweek nanosecond nanoseconds ns""".split())
# The same values, as tokens of a statement sqlglot keeps as text, such as a CALL.
VALUE_TOKENS = {TokenType.STRING, TokenType.NUMBER, TokenType.RAW_STRING, TokenType.HEREDOC_STRING,
                TokenType.BYTE_STRING, TokenType.NATIONAL_STRING, TokenType.NATIONAL_RAW_STRING, TokenType.HEX_STRING,
                TokenType.BIT_STRING, TokenType.UNICODE_STRING, TokenType.TRUE, TokenType.FALSE}


def canonical(value) -> str:
    """One JSON text per value, keys sorted and no spaces, so the same record always has the same bytes and hash."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def settings(path: Path = SETTINGS) -> dict:
    """How the log is reviewed (governance/audit.toml): rules of thumb, kept out of code."""
    return tomllib.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- the policy that decided


def modules(folder: Path = policy.POLICIES) -> dict[str, str]:
    """The policy modules that make decisions, by name: every .rego file in the folder but the tests."""
    return {path.stem: path.read_text(encoding="utf-8")
            for path in sorted(folder.glob("*.rego")) if not path.stem.endswith("_test")}


def bundle(folder: Path = policy.POLICIES, data: dict | None = None) -> dict:
    """Everything a decision depends on besides the request, as OPA ships it in a bundle: the policy's modules and the
    data document lib.policy generates from users.toml, metadata.toml and learning.courses."""
    return {"modules": modules(folder), "data": data if data is not None else policy.data_document()}


def revision(bundle: dict) -> str:
    """The policy's fingerprint: the SHA-256 of its modules and data document, so a change to either changes it."""
    return sha256(canonical(bundle))


def interpreter(bundle: dict) -> Interpreter:
    """An interpreter holding one bundle and nothing else, so every decision it makes belongs to that revision."""
    rego = Interpreter()
    rego.add_data_json(json.dumps(bundle["data"]))
    for name, source in sorted(bundle["modules"].items()):
        rego.add_module(name, source)
    return rego


class Archive:
    """Every bundle that has made a decision, kept by revision, so a logged decision can be made again under the policy
    that made it. A bundle holds rules and the attributes of the platform's users, not data about learners."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def put(self, bundle: dict) -> str:
        found = revision(bundle)
        path = self.folder / f"{found}.json"
        if not path.exists():
            path.write_text(canonical(bundle), encoding="utf-8")
        return found

    def get(self, revision: str) -> dict | None:
        path = self.folder / f"{revision}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ---------------------------------------------------------------- query text


@functools.cache
def platform_schema() -> dict[str, dict[str, dict[str, str]]]:
    """The platform's eight tables and their columns, from the Parquet files' schemas: the names `normalized` keeps
    when it isn't given a catalog's schema."""
    found: dict[str, dict[str, dict[str, str]]] = {}
    for full, file in TABLES.items():
        namespace, name = full.split(".")
        found.setdefault(namespace, {})[name] = {f.name: str(f.type) for f in pq.read_schema(DATA / f"{file}.parquet")}
    return found


def catalog_names(schema: dict[str, dict[str, dict[str, str]]]) -> set[str]:
    """Every name a catalog holds: its namespaces, tables and columns, in lower case, as DuckDB compares them."""
    names = set()
    for namespace, tables in schema.items():
        names.add(namespace.lower())
        for table, columns in tables.items():
            names.add(table.lower())
            names.update(column.lower() for column in columns)
    return names


@contextlib.contextmanager
def quiet_parser():
    """sqlglot warns, with the whole query in the message, each time it keeps a statement as text. Its warnings would
    put the query's values into whatever log collects the process's output, so they're silenced wherever the layer
    parses a user's query."""
    parser_log = logging.getLogger("sqlglot")
    level = parser_log.level
    parser_log.setLevel(logging.ERROR)
    try:
        yield
    finally:
        parser_log.setLevel(level)


def shaped(sql: str, schema: dict | None = None) -> tuple[str | None, set[str]]:
    """The query's shape, as `normalized` returns it, and every text it took out of the query."""
    names = catalog_names(schema if schema is not None else platform_schema())
    try:
        with quiet_parser():
            statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except SqlglotError:
        return None, set()
    shapes, removed = [], set()
    for statement in statements:
        if isinstance(statement, exp.Command):
            text = command_shape(statement.expression.name, names, removed)
            shapes.append(f"{statement.this} {text}".strip())
        else:
            shapes.append(tree_shape(statement, names, removed))
    return "; ".join(shapes), removed


def tree_shape(statement: exp.Expression, names: set[str], removed: set[str]) -> str:
    """One parsed statement's shape. Every value becomes `?`, and so does any word but a date unit where sqlglot keeps
    a word, such as the unit of date_trunc. The names the catalog holds stay. An alias or a CTE's name that the query
    defines becomes alias_1, alias_2 and so on, the same one wherever it's used, and any other name, such as a value
    typed in double quotes, becomes `?`. Comments are left out."""
    for node in statement.walk():
        node.comments = None
    for value in list(statement.find_all(*VALUES)):
        removed.update(node.this for node in value.walk() if isinstance(node.args.get("this"), str))
        value.replace(exp.Placeholder())
    for word in list(statement.find_all(exp.Var)):
        if word.name.lower() not in UNITS:
            removed.add(word.name)
            word.replace(exp.Placeholder())
    for type_ in statement.find_all(exp.DataType):
        if type_.this == exp.DataType.Type.USERDEFINED:          # a type the catalog doesn't name: a name typed in
            removed.add(type_.args.get("kind") or "")
            type_.set("kind", "?")
    defined = {alias.alias.lower() for alias in statement.find_all(exp.Alias)}
    for alias in statement.find_all(exp.TableAlias):
        defined.update(i.name.lower() for i in [alias.this, *alias.columns] if isinstance(i, exp.Identifier))
    settings_named = {id(i) for item in statement.find_all(exp.SetItem) if isinstance(item.this, exp.EQ)
                      for i in item.this.this.find_all(exp.Identifier)}         # the setting in SET x = ..., not a value
    renamed: dict[str, str] = {}
    for identifier in list(statement.find_all(exp.Identifier)):
        name = identifier.name
        if name.lower() in names or id(identifier) in settings_named:
            continue
        removed.add(name)
        if name.lower() in defined:
            new = renamed.setdefault(name.lower(), f"alias_{len(renamed) + 1}")
        else:
            new = "?"
        identifier.replace(exp.Identifier(this=new, quoted=False))
    return statement.sql(DIALECT)


def command_shape(text: str, names: set[str], removed: set[str]) -> str:
    """The shape of a statement sqlglot keeps as text, from its tokens: every value, and every name after the first
    that the catalog doesn't hold, becomes `?`. Comments, which aren't tokens, are left out."""
    tokens = Dialect.get_or_raise(DIALECT).tokenize(text)
    out, end, first = [], -1, True
    for token in tokens:
        piece = text[token.start:token.end + 1]
        if token.token_type in VALUE_TOKENS:
            removed.add(token.text)
            piece = "?"
        elif token.token_type in (TokenType.VAR, TokenType.IDENTIFIER):
            if not first and token.text.lower() not in names:
                removed.add(token.text)
                piece = "?"
            first = False
        if out and token.start > end + 1:
            out.append(" ")
        out.append(piece)
        end = token.end
    return "".join(out)


def normalized(sql: str, schema: dict | None = None) -> str | None:
    """The query's shape, without anything typed into it that may be a value, such as an email or a learner's ID.
    Every literal, in any of DuckDB's syntaxes, becomes `?`. Every name the catalog doesn't hold becomes `?`, or
    alias_1, alias_2 and so on when the query defines it as an alias or a CTE. Comments are left out. `schema` is the
    catalog's, as Enforcer.schema holds it; without one, the platform's eight tables. None when the query doesn't
    parse."""
    return shaped(sql, schema)[0]


def redacted(message: str, removed: Iterable[str]) -> str:
    """A message with every text `normalized` took out of the query replaced by `?`, wherever and in whatever case it
    appears, so that a denial reason quoting the query, as the parser's and the layer's own can, keeps none of it. A
    position in the query's text goes too, since the record doesn't keep that text."""
    message = re.sub(r"\.? Line: \d+, Col: \d+\.?", "", message)
    for text in sorted({t for t in removed if t} | {t.replace("'", "''") for t in removed if t}, key=len, reverse=True):
        pattern = re.escape(text)
        if re.fullmatch(r"\w+", text):
            pattern = rf"(?<!\w){pattern}(?!\w)"          # a short word or number, but not inside another word
        message = re.sub(pattern, "?", message, flags=re.IGNORECASE)
    return message


def digest(text: str, key: str = EXAMPLE_KEY) -> str:
    """A keyed digest (HMAC-SHA256) of a text: equal for equal texts, and without the key nobody can test a guess of
    what the text held. With the key, anyone can: a digest of text that held an email is a pseudonym of that email."""
    return hmac.new(key.encode("utf-8"), text.encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- the log and its chain


class Log:
    """An append-only audit log in JSON Lines. Each record gets the next sequence number, `seq`, and the SHA-256 of the
    line before it, `prev_hash`, so the records form a chain."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.touch()

    def lines(self) -> list[str]:
        return self.path.read_text(encoding="utf-8").splitlines()

    def records(self) -> list[dict]:
        return [json.loads(line) for line in self.lines()]

    def head(self) -> dict:
        """Where the chain ends: how many records it holds, and the hash of the last."""
        lines = self.lines()
        return {"records": len(lines), "hash": sha256(lines[-1]) if lines else GENESIS}

    def append(self, record: dict) -> dict:
        head = self.head()
        written = {"seq": head["records"] + 1, **record, "prev_hash": head["hash"]}
        with self.path.open("a", encoding="utf-8") as file:
            file.write(canonical(written) + "\n")
        return written


def check(lines: list[str], anchor: dict | None = None) -> list[str]:
    """What's wrong with a chain of records. Each record must be numbered one after the one before it and carry that
    record's hash. With an anchor, a head written down somewhere the log's writer can't change, the chain must also
    still pass through that head. Nothing written after the anchor is protected: a chain cut or rebuilt after it, with
    its records renumbered, passes."""
    problems, previous, before = [], GENESIS, 0
    for line in lines:
        record = json.loads(line)
        if record["seq"] <= before:
            problems.append(f"record {record['seq']} is out of order: it follows record {before}")
        elif record["seq"] != before + 1:
            problems.append(f"record {before + 1} is missing: record {record['seq']} follows record {before}")
        elif record["prev_hash"] != previous:
            problems.append(f"record {before} was changed: record {record['seq']} doesn't follow it")
        previous, before = sha256(line), record["seq"]
    if anchor and anchor["records"]:
        if len(lines) < anchor["records"] or sha256(lines[anchor["records"] - 1]) != anchor["hash"]:
            problems.append(f"the chain no longer passes through the head written down at record {anchor['records']}")
    return problems


# ---------------------------------------------------------------- the audited enforcement layer


def ticking(start: datetime, step: timedelta = timedelta(minutes=1)) -> Callable[[], datetime]:
    """A clock for examples: `start`, then one step later at each call."""
    ticks = count()
    return lambda: start + step * next(ticks)


def stamp(at: datetime) -> str:
    """A time as the log writes it: UTC, to the second, with a Z."""
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Audited:
    """The enforcement layer, with an audit log. `sql` decides, writes the record, and only then runs the query, so no
    query reaches DuckDB through the layer without a record. The layer's connection also keeps DuckDB's own query log,
    for `check_engine_log`, and locks its configuration, so that code on the connection can't switch that log off.

    What the cross-check can and can't catch: it catches a read around the layer by code that leaves DuckDB's log
    alone, such as a notebook helper or a test handed the connection. Code that holds the connection and means to hide
    can empty the log between two checks, and can write entries into it with DuckDB's write_log function, which the
    lock doesn't stop. Only a log written outside this process guards against that."""

    def __init__(self, catalog: Catalog, log: Log, policy_bundle: dict | None = None, archive: Archive | None = None,
                 key: str = EXAMPLE_KEY, clock: Callable[[], datetime] | None = None, lock: bool = True):
        self.bundle = policy_bundle if policy_bundle is not None else bundle()
        self.revision = revision(self.bundle)
        if archive is not None:
            archive.put(self.bundle)
        self.enforcer = Enforcer(catalog, rego=interpreter(self.bundle))
        self.log, self.key, self.catalog = log, key, catalog.name
        self.clock = clock or ticking(MIDNIGHT.replace(hour=9))
        connection = self.enforcer.connection
        connection.execute("CALL enable_logging('QueryLog')")
        if lock:
            connection.execute("set lock_configuration = true")
        connection.execute("CALL truncate_duckdb_logs()")       # the setup above isn't a query anyone ran
        self.checked = log.head()["records"]       # records before this ran on other connections, in other checks
        # Results are handed back from a connection of the layer's own that holds nothing but them.
        self.results = duckdb.connect()
        for setting in ("set TimeZone = 'UTC'", "set enable_external_access = false", "set lock_configuration = true"):
            self.results.execute(setting)

    def tables(self) -> list[str]:
        return sorted(f"{namespace}.{name}" for namespace, tables in self.enforcer.schema.items() for name in tables)

    def sql(self, query: str, user: str, purpose: str | None = None, via: str | None = None,
            at: datetime | None = None) -> duckdb.DuckDBPyRelation:
        """Run a user's query as that user, as Enforcer.sql does, with a record written first. `via` names the
        assistant that sent the query on the user's behalf, if one did. A denial is recorded, and raised, with every
        value of the query taken out of its reason.

        DuckDB logs a relation's query each time it's fetched, and logs the relational API's own SQL when a relation
        is narrowed, so the layer fetches the result itself, once, and hands it back as a relation on its own results
        connection. Whatever the caller then does with it, or doesn't, DuckDB's log holds each allowed query once."""
        shape, removed = shaped(query, self.enforcer.schema)
        record = {"event": "query", "time": stamp(at or self.clock()), "catalog": self.catalog, "user": user, "via": via,
                  "purpose": purpose, "query": shape, "policy_revision": self.revision}
        denied = {"outcome": "denied", "rewritten_digest": None}
        try:
            with quiet_parser():
                _, reads, _ = self.enforcer.plan(query)
        except (Denied, SqlglotError) as error:
            reason = redacted(str(error), removed) if isinstance(error, Denied) else "the query doesn't parse"
            self.log.append(record | denied | {"decisions": [], "denial": reason})
            raise Denied(reason) from None
        decisions = [{"table": table, "columns": sorted(columns)}
                     | policy.decide(policy.request(user, table, sorted(columns), purpose), self.enforcer.rego)
                     for table, columns in sorted(reads.items())]
        refused = next((decision for decision in decisions if not decision["allow"]), None)
        if refused:
            reason = f"{user} may not read {refused['table']}: {'; '.join(refused['reasons'])}"
            self.log.append(record | denied | {"decisions": decisions, "denial": reason})
            raise Denied(reason)
        try:
            with quiet_parser():
                rewritten = self.enforcer.rewrite(query, user, purpose)
        except Denied as error:
            reason = redacted(str(error), removed)
            self.log.append(record | denied | {"decisions": decisions, "denial": reason})
            raise Denied(reason) from None
        self.log.append(record | {"outcome": "allowed", "decisions": decisions, "denial": None,
                                  "rewritten_digest": digest(rewritten, self.key)})
        return self.results.from_arrow(self.enforcer.run(rewritten).to_arrow_table())

    def check_engine_log(self) -> dict:
        """Cross-check DuckDB's log against the records written since the last check, then empty DuckDB's log, which
        holds the queries' values. The result is cross_check's, with the records it covered and the keyed digest of
        every query DuckDB ran, so anyone holding the audit log can redo the matching without the key. Nobody can read
        DuckDB's log again: this empties it."""
        ran = engine_queries(self.enforcer.connection)
        self.enforcer.connection.execute("CALL truncate_duckdb_logs()")
        records = [record for record in self.log.records() if record["seq"] > self.checked]
        self.checked = self.log.head()["records"]
        return compared(ran, records, self.key, self.enforcer.schema)


def record_erasure(log: Log, request: str, done: dict[str, int | str], scanned: dict[str, tuple[int, int]],
                   at: datetime) -> dict:
    """The erasure's record in the audit log, and so in the evidence: the request's ID, the tables the erasure visited
    (lib.retention), the rows it deleted, the tables an exception in governance/retention.toml kept, each with the
    reason, who decided it and the columns erased from the kept rows, as the file gives them when the record is
    written, and what the scan of every file found afterwards. Never the person's ID or values, so the record never
    needs erasing itself. Chapter 7's erasure register, which ties the request to a keyed hash of the ID, is its own."""
    from lib import retention                   # here, so the audit log needs lineage's packages only for erasures

    excepted = retention.exceptions()
    kept = [{"table": table, "reason": excepted[table]["reason"], "decided_by": excepted[table].get("decided_by"),
             "columns_erased": sorted(excepted[table].get("erase_columns", []))}
            for table in sorted(done) if table in excepted]
    return log.append({"event": "erasure", "time": stamp(at), "user": "erasure-job", "request": request,
                       "tables": sorted(done), "rows_deleted": sum(n for n in done.values() if n != "rebuilt"),
                       "rebuilt": sorted(table for table, n in done.items() if n == "rebuilt"), "kept": kept,
                       "files_read": sum(files for files, _ in scanned.values()),
                       "files_holding_the_person": sum(holding for _, holding in scanned.values())})


def queries(records: list[dict]) -> list[dict]:
    """The records of queries, leaving out other events such as erasures."""
    return [record for record in records if record["event"] == "query"]


def replay(record: dict, policy_bundle: dict) -> list[dict]:
    """A record's decisions made again under a bundle, from the request each one logged: user, table, columns and
    purpose."""
    rego = interpreter(policy_bundle)
    return [{"table": d["table"], "columns": d["columns"]}
            | policy.decide(policy.request(record["user"], d["table"], d["columns"], record["purpose"]), rego)
            for d in record["decisions"]]


# ---------------------------------------------------------------- DuckDB's query log, as a cross-check


def engine_queries(connection: duckdb.DuckDBPyConnection) -> list[str]:
    """Every query DuckDB's QueryLog holds, but its own reads of the log."""
    return [message for (message,) in connection.sql(ENGINE_LOG).fetchall() if message != ENGINE_LOG]


def compared(ran: list[str], records: list[dict], key: str = EXAMPLE_KEY, schema: dict | None = None) -> dict:
    """Queries DuckDB ran against decision records, matched by keyed digest."""
    records = queries(records)
    handed = {record["rewritten_digest"] for record in records if record["rewritten_digest"]}
    seen = {digest(query, key) for query in ran}
    return {"engine queries": len(ran),
            "unrecorded": sorted(normalized(q, schema) or "(doesn't parse)" for q in ran if digest(q, key) not in handed),
            "not in the engine log": [record["seq"] for record in records
                                      if record["rewritten_digest"] and record["rewritten_digest"] not in seen],
            "records checked": [records[0]["seq"], records[-1]["seq"]] if records else [],
            "engine digests": sorted(seen)}


def cross_check(connection: duckdb.DuckDBPyConnection, records: list[dict], key: str = EXAMPLE_KEY) -> dict:
    """DuckDB's query log against the decision records, matched by keyed digest: the queries DuckDB ran that no record
    handed it, shown without their values, and the allowed records whose query DuckDB has no trace of. Give it the
    records written since DuckDB's log was last emptied: an older record's query has gone from the log with the rest.
    Audited.check_engine_log does that bookkeeping."""
    return compared(engine_queries(connection), records, key)


# ---------------------------------------------------------------- a day of the platform's queries

# One day of queries on the platform, Oct 1, 2026, in UTC: (time, user, the assistant that sent it, purpose, SQL).
DAY = [
    ("08:55", "agent-a05", None, None, "select ticket_id, channel, status from support.tickets where status = 'open'"),
    ("09:10", "agent-a05", None, None, "select ticket_id, opened_at, body from support.tickets where learner_id = 541"),
    ("09:12", "agent-a05", None, None, "select full_name, email, phone from crm.learners where learner_id = 541"),
    ("09:30", "finance-1", None, None,
     "select date_trunc('month', paid_at) as month, sum(amount) as revenue from billing.payments "
     "where status = 'succeeded' group by all"),
    ("10:00", "instructor-17", "assistant-1", None,
     "select course_id, count(*) as reviews, round(avg(rating), 2) as rating from learning.reviews group by all"),
    ("10:01", "instructor-17", "assistant-1", None, "select full_name, email from crm.learners"),
    ("10:01", "instructor-17", "assistant-1", None,
     "select l.full_name, r.rating from learning.reviews as r join crm.learners as l using (learner_id)"),
    ("10:02", "instructor-17", "assistant-1", None, "select body from support.tickets"),
    ("11:00", "marketing-1", None, None, "select full_name, email from crm.learners"),
    ("13:00", "analyst-1", None, None, "select country, count(distinct email) as learners from crm.learners group by all"),
    ("13:30", "analyst-1", None, "ai-training", "select rating, body from learning.reviews"),
    ("14:00", "someone-else", None, None, "select email from crm.learners"),
    ("16:00", "assistant-1", None, None, "select review_id, body from learning.reviews"),
]


def run_day(audited: Audited, day: Iterable[tuple] = DAY) -> dict[str, int]:
    """Send a day of queries through the audited layer, fetching each result as its user would. Returns how many were
    allowed and how many denied."""
    counts = {"allowed": 0, "denied": 0}
    for clock, user, via, purpose, sql in day:
        hour, minute = map(int, clock.split(":"))
        try:
            audited.sql(sql, user, purpose, via=via, at=MIDNIGHT.replace(hour=hour, minute=minute)).fetchall()
            counts["allowed"] += 1
        except Denied:
            counts["denied"] += 1
    return counts


# ---------------------------------------------------------------- reviews and evidence, generated


def review(policy_bundle: dict, records: list[dict], tables: Iterable[str]) -> list[dict]:
    """An access review, one row per user in the bundle's users and per other user the log names: the tables the policy
    lets them read, the tables the log shows they read, the grants they didn't use, their denied queries and the
    assistants that sent those, and what someone should look at."""
    rego = interpreter(policy_bundle)
    users = policy_bundle["data"]["platform"]["users"]
    groups = set(policy.evaluate(rego, "data.platform.access.readable", policy.request("", "none.none", [])))
    flagged_at = settings()["review"]["denied_queries_flagged"]
    records = queries(records)
    rows = []
    for user in sorted(set(users) | {record["user"] for record in records}):
        theirs = [record for record in records if record["user"] == user]
        may = [table for table in sorted(tables)
               if user in users and policy.decide(policy.request(user, table, []), rego)["allow"]]
        read = sorted({d["table"] for record in theirs if record["outcome"] == "allowed" for d in record["decisions"]})
        denied = [record for record in theirs if record["outcome"] == "denied"]
        findings = [] if user in users else ["not in users.toml"]
        findings += [f"in group {group}, which the policy doesn't know" for group in users.get(user, {}).get("groups", [])
                     if group not in groups]
        if len(denied) >= flagged_at:
            findings.append(f"{len(denied)} denied queries")
        rows.append({"user": user, "may read": may, "read": read, "unused": [t for t in may if t not in read],
                     "denied": len(denied), "denied via": sorted({r["via"] for r in denied if r["via"]}),
                     "findings": findings})
    return rows


def evidence(audited: Audited, catalog: Catalog, anchor: dict | None = None) -> dict:
    """An evidence report, computed from records and code. Each section names the policy revision it was computed
    under, the digests of its inputs, and what anyone needs to compute it again. One section can be computed again only
    in part: the engine cross-check reads DuckDB's log, which only the layer can read, and empties it."""
    if audited.bundle["modules"] != modules():
        raise ValueError("the layer's policy isn't the one in governance/policies, whose tests the report runs")
    lines, head = audited.log.lines(), audited.log.head()
    records = [json.loads(line) for line in lines]
    data, rego, tables = audited.bundle["data"], interpreter(audited.bundle), audited.tables()
    tests = policy.run_tests(data=data)
    agree = [policy.matches(case, policy.evaluate(rego, DECISION, policy.request(*case[:4]))) for case in policy.WHO_SEES_WHAT]
    reviewed = review(audited.bundle, records, tables)
    erasures = [{key: record.get(key) for key in ("request", "time", "rows_deleted", "rebuilt", "kept", "files_read",
                                                  "files_holding_the_person")}
                for record in records if record["event"] == "erasure"]
    engine = audited.check_engine_log()
    test_files = sorted(policy.POLICIES.glob("*_test.rego"))
    schemas = {table: sha256(canonical([(f.field_id, f.name, str(f.field_type), f.required)
                                        for f in catalog.load_table(table).schema().fields])) for table in tables}
    bundled = "the bundle, archived under its revision"
    sections = {
        "policy": ({name: sha256(source) for name, source in audited.bundle["modules"].items()}
                   | {"data document": sha256(canonical(data))},
                   {"revision": audited.revision}, [bundled]),
        "policy tests": ({path.name: sha256(path.read_text(encoding="utf-8")) for path in test_files},
                         {"passed": sum(result == "PASS" for result in tests.values()), "of": len(tests),
                          "not passed": sorted(test for test, result in tests.items() if result != "PASS")},
                         [bundled, "the test files"]),
        "who sees what": ({"cases": sha256(canonical([list(case) for case in policy.WHO_SEES_WHAT]))},
                          {"agree": sum(agree), "of": len(agree)}, [bundled, "lib.policy's cases"]),
        "classification coverage": ({"metadata.toml": sha256(metadata.METADATA.read_text(encoding="utf-8")),
                                     "table schemas": schemas},
                                    {"tables": len(tables),
                                     "columns": sum(len(catalog.load_table(t).schema().fields) for t in tables),
                                     "problems": metadata.problems(catalog, metadata.load())},
                                    ["metadata.toml", "each table's schema"]),
        "audit log": ({"head": head, "anchor": anchor},
                      {"records": len(records), "chain problems": check(lines, anchor),
                       "from": records[0]["time"] if records else None, "to": records[-1]["time"] if records else None},
                      ["the audit log, up to the head", "the anchor, from the last report"]),
        "engine cross-check": ({"records": engine.pop("records checked"), "engine digests": engine.pop("engine digests")},
                               engine, ["the audit log", "the engine digests, for the two counts"]),
        "erasures": ({"head": head}, erasures, ["the audit log, up to the head"]),
        "access review": ({"head": head, "tables": tables,
                           "audit.toml": sha256(SETTINGS.read_text(encoding="utf-8"))},
                          {"users": len(reviewed), "unused grants": sum(len(row["unused"]) for row in reviewed),
                           "flagged": {row["user"]: row["findings"] for row in reviewed if row["findings"]}},
                          [bundled, "the audit log, up to the head", "audit.toml"]),
    }
    report = {name: {"policy_revision": audited.revision, "inputs": inputs, "result": result,
                     "computed again from": again}
              for name, (inputs, result, again) in sections.items()}
    report["engine cross-check"]["not computed again"] = (
        "the reading of DuckDB's log, and the unrecorded queries' shapes: the layer read that log once, then emptied it")
    return report


def show(record: dict) -> str:
    """A query's record, one field a line in reading order and one line per decision, with each hash and digest cut
    to its first 12 characters."""
    fields = ["seq", "event", "time", "catalog", "user", "via", "purpose", "query", "outcome", "denial", "decisions",
              "policy_revision", "rewritten_digest", "prev_hash"]
    parts = ["table", "columns", "allow", "row_filters", "column_masks", "reasons"]
    lines = []
    for field in fields:
        value = record[field]
        if field == "decisions":
            lines += [f"  decision: {json.dumps({part: d[part] for part in parts}, ensure_ascii=False)}" for d in value]
        elif isinstance(value, str):
            lines.append(f"  {field}: {value[:12] + '...' if len(value) == 64 else value}")
        else:
            lines.append(f"  {field}: {json.dumps(value)}")
    return "\n".join(lines)
