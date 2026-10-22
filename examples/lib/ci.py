"""The platform's governance checks as one job, run on every pull request: the checks of chapters 2 to 7, except chapter
3's sampling of values, which runs on a schedule, plus two rules of the book's own about what a change may do.

A pull request is two checkouts, the main branch's and the branch's, each a folder holding `governance/`, the
platform's governance files, `pipelines/`, its pipelines' SQL, `migrations/`, the `alter table` statements that change
its tables' schemas, and the files generated from the metadata: a contract per table and `.github/CODEOWNERS`. The job
builds a scratch platform for each side the way the platform runs (`build`), runs the checks that the branch's
governance/ci.toml lists (`run`), and reports what each schema change reaches downstream.

The job judges what the pull request changes. Every finding has a stable ID, "check/subject". A finding the main
branch's own job also reports is "on main": it is reported and counted, with the team that owns it, and fails nothing.
A finding the pull request introduces fails it, unless the team whose approval it asks for approves, or a waiver in the
branch's governance/waivers.toml covers it, which ci.toml forbids for some checks. A check that crashes is one finding
that fails the job, and the other checks still run. `main` is the job as a CI step runs it: it returns the exit code
and writes the report as Markdown. Nothing reads the clock, git or the network: a waiver's date is compared with the
book's "today", lib.platform.TODAY."""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path

import sqlglot
import yaml
from pyiceberg.catalog import Catalog
from pyiceberg.types import BooleanType, DateType, DoubleType, IntegerType, LongType, StringType, TimestamptzType
from regopy import Interpreter
from sqlglot import exp

from lib import enforce, lineage, metadata, pii, pipelines, policy, retention
from lib.catalog import open_catalog
from lib.lineage import table_of
from lib.metadata import LEVELS
from lib.platform import TODAY, scratch
from lib.warehouse import connect

GOVERNANCE = Path(__file__).resolve().parent.parent / "governance"
CODEOWNERS = ".github/CODEOWNERS"
SQL_TYPES = {"TEXT": StringType(), "VARCHAR": StringType(), "INT": IntegerType(), "BIGINT": LongType(),
             "DOUBLE": DoubleType(), "BOOLEAN": BooleanType(), "DATE": DateType(), "TIMESTAMPTZ": TimestamptzType()}
LOGICAL_TYPES = {"string": "string", "int": "integer", "long": "integer", "double": "number", "boolean": "boolean",
                 "date": "date", "timestamptz": "timestamp"}     # Iceberg's types, as ODCS names them


# ---------------------------------------------------------------- a pull request, as two folders


class Unreadable(Exception):
    """A file of a checkout that doesn't parse, named by its path in the checkout."""


def read_toml(path: Path) -> dict:
    """A TOML file of a checkout. A file that doesn't parse raises Unreadable, which names it: TOML's own errors give
    only a line and a column."""
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise Unreadable(f"{path.parent.name}/{path.name} doesn't parse: {error}") from None


def checkout(source: Path | None = None) -> Path:
    """A copy of a checkout in a temporary folder. By default it is the main branch as the book holds it: the book's
    governance files and pipelines, no migrations, and the contracts and CODEOWNERS generated from them (`generate`),
    as the last merged pull request left them. An example changes the copy to make a branch. The folder is removed when
    the process exits."""
    root = scratch("checkout-")
    if source is None:
        shutil.copytree(GOVERNANCE, root / "governance")
        shutil.copytree(pipelines.PIPELINES, root / "pipelines")
        (root / "migrations").mkdir()
        generate(build(root))
    else:
        shutil.copytree(source, root, dirs_exist_ok=True)
    return root


def edit(root: Path, path: str, old: str, new: str) -> None:
    """Change one file of a checkout, as a commit on a branch would: `old` must occur in it exactly once."""
    file = root / path
    text = file.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise ValueError(f"{path}: {old!r} occurs {text.count(old)} times, not once")
    file.write_text(text.replace(old, new), encoding="utf-8")


def changed(main: Path, branch: Path) -> list[str]:
    """The files a pull request adds, changes or removes, relative to the checkout, as git would list them."""
    def files(root: Path) -> dict[str, bytes]:
        return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    before, after = files(main), files(branch)
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def migrate(catalog: Catalog, statements: str) -> None:
    """Apply a migration's `alter table` statements as Iceberg schema changes: add, drop or rename a column, or change
    its type, which Iceberg allows only as a promotion, such as int to long."""
    for statement in filter(None, sqlglot.parse(statements, read="duckdb")):
        table = catalog.load_table(f"{statement.this.db}.{statement.this.name}")
        with table.update_schema() as update:
            for action in statement.args["actions"]:
                if isinstance(action, exp.RenameColumn):
                    update.rename_column(action.this.name, action.args["to"].name)
                elif isinstance(action, exp.ColumnDef):
                    update.add_column(action.name, SQL_TYPES[action.args["kind"].this.name])
                elif isinstance(action, exp.Drop):
                    update.delete_column(action.args["tables"][0].name)
                elif isinstance(action, exp.AlterColumn):
                    update.update_column(action.this.name, field_type=SQL_TYPES[action.args["dtype"].this.name])
                else:
                    raise ValueError(f"a migration can't {action.key}: {statement.sql()}")


def run_pipeline(catalog: Catalog, statement: str) -> str:
    """Run one pipeline's SQL, as lib.pipelines.run does for the book's own pipelines: write its output to the catalog,
    replacing the table if it exists. Returns the output's name."""
    output = pipelines.target(statement)
    select = sqlglot.parse_one(statement, read="duckdb").expression
    result = connect(catalog).sql(select.sql(dialect="duckdb")).to_arrow_table()
    catalog.create_namespace_if_not_exists(output.split(".")[0])
    if catalog.table_exists(output):
        catalog.drop_table(output)
    catalog.create_table(output, schema=result.schema).append(result)
    return output


# ---------------------------------------------------------------- one side, built


@dataclass
class Side:
    """One side of a pull request, built: its checkout, its scratch catalog, its metadata, the lineage graph its
    nightly run sent, why each pipeline that couldn't run failed, and why Iceberg refused each migration it refused."""
    root: Path
    catalog: Catalog
    described: dict[str, metadata.Table]
    graph: lineage.Graph
    failed: dict[str, str]
    refused: dict[str, str] = field(default_factory=dict)
    reported: dict[str, Check] = field(default_factory=dict, repr=False)    # its own checks, once each: findings on main

    @property
    def governance(self) -> Path:
        return self.root / "governance"

    @property
    def pipelines(self) -> list[Path]:
        return sorted((self.root / "pipelines").glob("*.sql"))


def build(root: Path) -> Side:
    """Build one side's scratch platform the way the platform runs: the source tables; the side's migrations, in name
    order; the deploy, which writes the side's metadata into the catalog (lib.metadata.apply) and sets every table's
    generated properties; then the nightly run of every pipeline in the side's pipelines/, with lineage. A migration
    Iceberg refuses and a pipeline that fails are recorded, not raised: each is a finding. The scratch platform's folder
    is removed when the process exits."""
    folder = scratch("build-")
    catalog = open_catalog(folder=folder)
    refused = {}
    for path in sorted((root / "migrations").glob("*.sql")):
        try:
            migrate(catalog, path.read_text(encoding="utf-8"))
        except Exception as error:          # Iceberg refuses it, or the job can't read it: the job reports it
            refused[path.name] = str(error).splitlines()[0]
    read_toml(root / "governance" / "metadata.toml")      # so a file that doesn't parse is named
    described = metadata.load(root / "governance" / "metadata.toml")
    metadata.apply(catalog, described)
    for name, wanted in generated_properties(catalog, described, root / "governance").items():
        with catalog.load_table(name).transaction() as transaction:
            transaction.set_properties(wanted)
    events, failed = folder / "lineage.jsonl", {}
    emitter = lineage.client(events)
    for path in sorted((root / "pipelines").glob("*.sql")):
        statement = path.read_text(encoding="utf-8")
        try:
            lineage.record(emitter, path.stem, statement, catalog, lambda: run_pipeline(catalog, statement))
        except Exception as error:          # whatever stops a pipeline, the job reports it and goes on
            failed[path.stem] = str(error).splitlines()[0]
    return Side(root, catalog, described, lineage.graph(lineage.read_events(events)), failed, refused)


def generated_properties(catalog: Catalog, described: dict[str, metadata.Table], governance: Path) -> dict[str, dict]:
    """The properties every table should carry: chapter 7's, generated from the classifications and the retention rules
    (lib.retention.properties), plus the owner and domain that lib.metadata.apply writes."""
    found = retention.properties(catalog, described, retention.rules(governance / "retention.toml"))
    for name, table in described.items():
        if name in found:
            found[name] |= {"owner": table.owner, "domain": table.domain}
    return found


def deprecations(governance: Path) -> set[tuple[str, int]]:
    """The columns a side's metadata marks `deprecated = true`, as (table, field ID), as lib.metadata.load reads them."""
    read_toml(governance / "metadata.toml")      # so a file that doesn't parse is named
    return {(name, column.field_id) for name, table in metadata.load(governance / "metadata.toml").items()
            for column in table.columns.values() if column.deprecated}


# ---------------------------------------------------------------- ownership as ODCS contracts, generated


def contract(side: Side, name: str) -> dict:
    """One table's metadata as an ODCS v3.2.0 data contract: one domain, ownership as the owner role on the contract's
    team, and each column a property keyed by its field ID, with its classification and `deprecated` when it is."""
    table, deprecated = side.described[name], deprecations(side.governance)
    fields = {f.field_id: f for f in side.catalog.load_table(name).schema().fields} if side.catalog.table_exists(name) else {}
    properties = []
    for field_id, column in sorted(table.columns.items()):
        found = fields.get(field_id)
        typed = {"logicalType": LOGICAL_TYPES[str(found.field_type)], "physicalType": str(found.field_type),
                 "required": found.required} if found else {}
        properties.append({"id": f"field-{field_id}", "name": column.name, **typed, "description": column.description,
                           "classification": column.classification}
                          | ({"deprecated": True} if (name, field_id) in deprecated else {}))
    return {"apiVersion": "v3.2.0", "kind": "DataContract", "id": name, "name": name, "version": "1.0.0",
            "status": "active", "domain": table.domain, "description": {"purpose": table.description},
            "schema": [{"name": name.split(".")[1], "physicalName": name, "physicalType": "table", "logicalType": "object",
                        "description": table.description, "properties": properties}],
            "team": {"name": table.owner, "members": [{"username": table.owner, "role": "owner"}]}}


def contract_path(side: Side, name: str) -> str:
    """Where a table's contract lives in a checkout: one folder per domain, and `no-domain` for a table without one."""
    folder = settings(side.governance)["codeowners"]["contracts"]
    return f"{folder}/{side.described[name].domain.strip() or 'no-domain'}/{name.split('.')[1]}.odcs.yaml"


def settings(governance: Path) -> dict:
    """How the job runs (governance/ci.toml)."""
    return read_toml(governance / "ci.toml")


def codeowners(side: Side) -> list[str]:
    """CODEOWNERS lines for a repository with a folder per domain. The platform team owns this file, the governance
    files, the migrations and the pipelines; each domain's team owns its folder of contracts and its rules, if it has
    any; and each pipeline goes to the team that owns the table it writes. GitHub applies the last line that matches."""
    owners = settings(side.governance)["codeowners"]
    team = lambda names: " ".join(f"@{owners['organization']}/{name}" for name in names)
    platform = team([owners["platform"]])
    lines = [f"/{CODEOWNERS} {platform}", f"/governance/ {platform}", f"/migrations/ {platform}", f"/pipelines/ {platform}"]
    for path in side.pipelines:
        written = side.described.get(pipelines.target(path.read_text(encoding="utf-8")))
        if written and written.owner:
            lines.append(f"/pipelines/{path.name} {team([written.owner])}")
    for domain in sorted({table.domain.strip() for table in side.described.values()} - {""}):
        owner = sorted({t.owner for t in side.described.values() if t.domain.strip() == domain and t.owner})
        lines.append(f"/{owners['contracts']}/{domain}/ {team(owner)}")
        if (side.governance / "ci" / "domains" / f"{domain}.rego").exists():
            lines.append(f"/governance/ci/domains/{domain}.rego {team(owner)}")
    return lines


def generated(side: Side) -> dict[str, str]:
    """The files generated from a side's metadata, by path in the checkout: every described table's contract, as YAML,
    and the CODEOWNERS file."""
    files = {contract_path(side, name): yaml.safe_dump(contract(side, name), sort_keys=False, allow_unicode=True)
             for name in sorted(side.described)}
    header = "# Generated by lib.ci.codeowners from governance/metadata.toml and governance/ci.toml. Never edit it by hand."
    files[CODEOWNERS] = "\n".join([header, *codeowners(side)]) + "\n"
    return files


def committed(root: Path) -> dict[str, str]:
    """The generated files a checkout commits, by path: its contracts and its CODEOWNERS file."""
    folder = root / settings(root / "governance")["codeowners"]["contracts"]
    paths = sorted(folder.rglob("*.odcs.yaml")) + [p for p in [root / CODEOWNERS] if p.exists()]
    return {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8") for path in paths}


def generate(side: Side) -> list[str]:
    """Write the generated files into the side's checkout, as the author's hook or a command would before a commit,
    removing contracts no table generates any more. Returns the paths written."""
    shutil.rmtree(side.root / settings(side.governance)["codeowners"]["contracts"], ignore_errors=True)
    files = generated(side)
    for path, text in files.items():
        (side.root / path).parent.mkdir(parents=True, exist_ok=True)
        (side.root / path).write_text(text, encoding="utf-8")
    return sorted(files)


def reviewers(lines: list[str], files: Iterable[str]) -> dict[str, list[str]]:
    """The code owners GitHub would ask to review each file: the last matching line wins. A pattern ending in "/" matches
    everything under that folder; any other pattern matches one file. Comments and blank lines are skipped."""
    rules = [line.split() for line in lines if line.strip() and not line.startswith("#")]
    found = {}
    for file in files:
        matches = [owners for pattern, *owners in rules
                   if ("/" + file).startswith(pattern) and (pattern.endswith("/") or "/" + file == pattern)]
        found[file] = matches[-1] if matches else []
    return found


# ---------------------------------------------------------------- findings, checks and waivers


@dataclass(frozen=True)
class Waiver:
    """One entry of governance/waivers.toml: the one finding it silences, up to and including its last day."""
    finding: str        # the ID of the one finding it covers
    owner: str          # the team answerable for it
    reason: str
    expires: date       # the last day it applies


@dataclass(frozen=True)
class Finding:
    """One problem a check found, under a stable ID, and what keeps it from failing the job: an approval, a waiver, or
    the main branch's own job reporting it too."""
    id: str                             # "check/subject": the same on every run, so it can be counted and waived
    message: str
    approver: str | None = None         # the team whose approval answers it, for a finding an approval can settle
    owner: str | None = None            # the team answerable for its subject (`run` fills it in)
    on_main: bool = False               # the main branch's own job reports it too, so it fails nothing
    waiver: Waiver | None = None
    approved_by: str | None = None      # the approver, once the pull request has its approval

    @property
    def failing(self) -> bool:
        return not self.on_main and self.waiver is None and self.approved_by is None


@dataclass
class Check:
    """One check's result: its findings, how much it read, and the error that stopped it, if one did."""
    name: str
    findings: list[Finding]
    checked: int        # how many things it read: what its cost grows with
    unit: str
    error: str | None = None            # why it could not run, if it couldn't

    @property
    def waived(self) -> list[Finding]:
        return [f for f in self.findings if f.waiver]

    @property
    def on_main(self) -> list[Finding]:
        return [f for f in self.findings if f.on_main]

    @property
    def new(self) -> list[Finding]:
        return [f for f in self.findings if not f.on_main]

    @property
    def passed(self) -> bool:
        return not any(f.failing for f in self.findings)


@dataclass(frozen=True)
class Change:
    """One column's schema change, by field ID, and what the main branch's lineage says it reaches."""
    table: str
    field_id: int
    kind: str                # added, removed, renamed, retyped or required
    before: str              # "category string", or "" for an added column
    after: str               # "subject string", or "" for a removed one
    jobs: list[str]          # the pipelines whose SQL names the column
    values: list[str]        # downstream columns whose values come from it: DIRECT
    reached: list[str]       # every downstream column it can change: DIRECT and INDIRECT
    owners: list[str]        # the teams that own the tables it reaches


@dataclass
class Report:
    """The job's result for one pull request: every check, every schema change, and the waivers that silenced nothing."""
    checks: list[Check]
    changes: list[Change]
    unused: list[tuple[Waiver, str]]     # waivers that silenced nothing, and why

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def exit_code(self) -> int:
        """What the job exits with, which a required status check reads: 1 when the pull request fails, else 0."""
        return 0 if self.passed else 1


def waivers(governance: Path) -> list[Waiver]:
    """The waivers in a side's governance/waivers.toml."""
    path = governance / "waivers.toml"
    raw = read_toml(path).get("waivers", []) if path.exists() else []
    return [Waiver(w["finding"], w["owner"], w["reason"], w["expires"]) for w in raw]


def waive(checks: list[Check], governance: Path, today: date = TODAY) -> list[tuple[Waiver, str]]:
    """Attach each waiver to the finding it names, if it may apply. A waiver for a check that ci.toml's `never` lists,
    or for a check that could not run, is refused whatever its end date; so is one that has expired, or that runs
    further past today than ci.toml's `longest_days`. Returns the waivers that silenced nothing, with the reason."""
    rules = settings(governance)["waivers"]
    longest, never = timedelta(days=rules["longest_days"]), set(rules["never"])
    crashed = {f"{check.name}/error" for check in checks if check.error}
    valid, unused = {}, []
    for waiver in waivers(governance):
        check = waiver.finding.split("/")[0]
        if check in never:
            unused.append((waiver, f"{check} findings are never waived"))
        elif waiver.finding in crashed:
            unused.append((waiver, "a check that could not run is never waived"))
        elif waiver.expires < today:
            unused.append((waiver, f"expired on {waiver.expires}"))
        elif waiver.expires > today + longest:
            unused.append((waiver, f"runs to {waiver.expires}, past the longest allowed, {today + longest}"))
        else:
            valid[waiver.finding] = waiver
    for check in checks:
        check.findings = [replace(f, waiver=valid.get(f.id)) for f in check.findings]
    found, ran = {f.id for check in checks for f in check.findings}, {check.name for check in checks}
    return unused + [(waiver, "matches no finding") for waiver in valid.values()
                     if waiver.finding not in found and waiver.finding.split("/")[0] in ran]


def _split(problem: str) -> tuple[str, str]:
    subject, _, message = problem.partition(": ")
    return subject, message


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---------------------------------------------------------------- the checks


def suggested(column: str) -> str | None:
    """The classification chapter 3's name rules suggest for a column (lib.pii.by_name), or None when none matches."""
    entity = pii.by_name(column)
    return pii.CLASSIFICATION.get(entity, pii.FLOOR) if entity else None


def coverage(main: Side, branch: Side, approvals: frozenset[str]) -> Check:
    """Chapter 2's test on the branch: every table in the catalog has metadata and an owner, every field ID one of the
    four classifications (lib.metadata.problems), and no entry describes a column that's gone (lib.metadata.dropped).
    For a column nobody classified, the finding adds what chapter 3's name rules suggest. A column classified less
    strictly than its name suggests is a finding too, which only the table's owner on the main branch can answer:
    the name is evidence, and the job has no values to read in a pull request."""
    findings = []
    for problem in metadata.problems(branch.catalog, branch.described) + metadata.dropped(branch.catalog, branch.described):
        subject, message = _split(problem)
        rule = suggested(subject.rpartition(".")[2]) if message == "not classified" else None
        findings.append(Finding(f"coverage/{subject}", message + (f"; its name suggests {rule}" if rule else "")))
    for name, table in sorted(branch.described.items()):
        owner = (main.described.get(name) or table).owner
        for column in sorted(table.columns.values(), key=lambda c: c.field_id):
            rule = suggested(column.name)
            if rule and column.classification in LEVELS and metadata.stricter(rule, column.classification) != column.classification:
                findings.append(Finding(f"coverage/{name}.{column.name}", f"classified {column.classification}; its name "
                                        f"suggests {rule}; needs {owner}'s approval", approver=owner))
    return Check("coverage", sorted(findings, key=lambda f: f.id), len(retention.tables(branch.catalog)), "tables")


def contract_rules(main: Side, branch: Side, approvals: frozenset[str]) -> Check:
    """The platform's global rules (governance/ci/global.rego) on every table's contract, and each domain's own rules
    (governance/ci/domains/<domain>.rego) on the contracts of that domain on the branch, evaluated in-process with
    regopy. A table moved to another domain gets the new domain's rules, and owner-review asks the old owner."""
    rules = branch.governance / "ci"
    rego = Interpreter()
    for path in sorted(rules.rglob("*.rego")):
        rego.add_module(path.relative_to(rules).with_suffix("").as_posix(), path.read_text(encoding="utf-8"))
    domains = {path.stem for path in (rules / "domains").glob("*.rego")}
    findings = []
    for name in sorted(branch.described):
        document = contract(branch, name)
        packages = ["global"] + ([f"domains.{document['domain']}"] if document["domain"] in domains else [])
        for package in packages:
            for violation in policy.evaluate(rego, f"data.platform.ci.{package}.deny", document):
                findings.append(Finding(f"contract-rules/{violation['subject']}/{violation['rule']}",
                                        f"{violation['message']} ({package} rule {violation['rule']})"))
    return Check("contract-rules", sorted(findings, key=lambda f: f.id), len(branch.described), "contracts")


def generated_files(main: Side, branch: Side, approvals: frozenset[str]) -> Check:
    """The generated files are current: every contract and the CODEOWNERS file, generated again from the branch's
    metadata (`generated`), against the ones the branch commits. A pull request that changes the metadata without
    generating them again would have its reviews routed by stale files."""
    wanted, have = generated(branch), committed(branch.root)
    findings = []
    for path in sorted(wanted.keys() | have.keys()):
        if path not in have:
            findings.append(Finding(f"contracts/{path}", "missing: generate it from the metadata"))
        elif path not in wanted:
            findings.append(Finding(f"contracts/{path}", "no table's metadata generates it: delete it"))
        elif have[path] != wanted[path]:
            findings.append(Finding(f"contracts/{path}", "differs from what the metadata generates: generate it again"))
    return Check("contracts", findings, len(wanted), "files")


def policy_data(side: Side) -> dict:
    """The data document the side's policy reads: lib.policy.data_document's, with the users and the classifications
    taken from the side's own governance files."""
    data = policy.data_document()
    data["platform"]["users"] = read_toml(side.governance / "users.toml")["users"]
    data["platform"]["classifications"] = {name: {c.name: c.classification for c in table.columns.values()}
                                           for name, table in side.described.items()}
    return data


def policy_tests(main: Side, branch: Side, approvals: frozenset[str]) -> Check:
    """Chapter 5's two kinds of test, on the branch's policy and the data the branch generates for it: the Rego unit
    tests (lib.policy.run_tests) and the who-sees-what table (lib.policy.WHO_SEES_WHAT)."""
    folder, data = branch.governance / "policies", policy_data(branch)
    results = policy.run_tests(folder, data)
    findings = [Finding(f"policy-tests/{test}", status) for test, status in results.items() if status != "PASS"]
    rego = policy.interpreter(folder, data=data)
    for number, case in enumerate(policy.WHO_SEES_WHAT, 1):
        decision = policy.decide(policy.request(case.user, case.table, case.columns, case.purpose), rego)
        if not policy.matches(case, decision):
            findings.append(Finding(f"policy-tests/who-sees-what-{number}", f"{case.user} reading {case.table}: not as the table expects"))
    return Check("policy-tests", findings, len(results) + len(policy.WHO_SEES_WHAT), "tests and rows")


def enforcement_tests(main: Side, branch: Side, approvals: frozenset[str]) -> Check:
    """Chapter 6's tests of the enforcement layer, through lib.enforce: the allowlist of what a query may read
    (source_cases), on the branch's catalog; the order in which row filters run (order_cases), on a catalog of its
    own; and the who-sees-what table run as queries through the layer, with the branch's policy and data."""
    layer = enforce.Enforcer(branch.catalog, rego=policy.interpreter(branch.governance / "policies", data=policy_data(branch)))
    sources, order = enforce.source_cases(layer), enforce.order_cases()
    findings = [Finding(f"enforcement-tests/sources/{_slug(case['case'])}", "allowed, where the layer must deny it"
                        if case["must"] == "deny" else f"denied, where the layer must allow it: {case['reason']}")
                for case in sources if case["denied"] != (case["must"] == "deny")]
    findings += [Finding(f"enforcement-tests/row-filter-order/{_slug(case['shape'])}",
                         f"{case['user']} reading {case['table']}: a cast saw a value from a row the filter drops")
                 for case in order if not case["safe_as_written"]]
    for number, case in enumerate(policy.WHO_SEES_WHAT, 1):
        if not enforce.agrees(case, enforce.observe(layer, case.user, case.table, case.columns, case.purpose)):
            findings.append(Finding(f"enforcement-tests/who-sees-what-{number}",
                                    f"{case.user} reading {case.table}: what the query returns disagrees with the decision"))
    return Check("enforcement-tests", findings, len(sources) + len(order) + len(policy.WHO_SEES_WHAT), "cases")


def refused_migrations(main: Side, branch: Side, approvals: frozenset[str]) -> Check:
    """The branch's migrations, as `build` applied them: one that Iceberg refused, such as a type change that isn't a
    promotion, or that the job couldn't read."""
    findings = [Finding(f"migrations/{name}", f"refused: {why}") for name, why in sorted(branch.refused.items())]
    return Check("migrations", findings, len(list((branch.root / "migrations").glob("*.sql"))), "migrations")


def pipeline_runs(main: Side, branch: Side, approvals: frozenset[str]) -> Check:
    """Every pipeline on the branch still runs on the branch's tables, with readable lineage (chapter 4)."""
    findings = [Finding(f"pipelines/{name}", why) for name, why in sorted(branch.failed.items())]
    return Check("pipelines", findings, len(branch.pipelines), "pipelines")


def lineage_classifications(main: Side, branch: Side, approvals: frozenset[str]) -> Check:
    """Chapter 4's check on the branch's lineage: no declared classification lower than lineage proposes, counting the
    inputs governance/lineage.toml says to count (lib.lineage.understated)."""
    known = lineage.declared(branch.described)
    through = lineage.rules(branch.governance / "lineage.toml")["propagate_through"]
    proposed = lineage.propose(branch.graph, known, through)
    findings = [Finding(f"lineage-classifications/{subject}", message)
                for subject, message in map(_split, lineage.understated(proposed, known))]
    return Check("lineage-classifications", findings, len(proposed), "columns")


def owner_review(main: Side, branch: Side, approvals: frozenset[str]) -> Check:
    """The book's first rule of its own: lowering a column's classification, changing a table's owner, or moving it to
    another domain needs the approval of the team that owns the table on the main branch, so no pull request can hand
    a table to a team, or to a domain with laxer rules, and then approve what that allows. `run` settles each finding
    with the approvals the pull request has."""
    findings = []
    for name, before in sorted(main.described.items()):
        after = branch.described.get(name)
        if after is None:
            continue
        lowered = [(f"{name}.{after.columns[fid].name}", f"lowered from {column.classification} to {after.columns[fid].classification}")
                   for fid, column in sorted(before.columns.items())
                   if fid in after.columns and after.columns[fid].classification in LEVELS
                   and LEVELS.index(after.columns[fid].classification) < LEVELS.index(column.classification)]
        moved = [(f"{name}/owner", f"owner changed from {before.owner} to {after.owner}")] if after.owner != before.owner else []
        moved += [(f"{name}/domain", f"domain changed from {before.domain} to {after.domain}")] if after.domain != before.domain else []
        findings += [Finding(f"owner-review/{subject}", f"{what}; needs {before.owner}'s approval", approver=before.owner)
                     for subject, what in moved + lowered]
    return Check("owner-review", findings, sum(len(t.columns) for t in main.described.values()), "columns")


def removals(main: Side, branch: Side, approvals: frozenset[str]) -> Check:
    """The book's second rule of its own: a column leaves a table only once the main branch has marked it deprecated,
    `deprecated = true` in its metadata entry, which its contract carries as ODCS's `deprecated`. The rule checks that the
    deprecation merged first, not how long it lasted."""
    deprecated, findings, checked = deprecations(main.governance), [], 0
    for name in sorted(set(retention.tables(main.catalog)) & set(retention.tables(branch.catalog))):
        before = {f.field_id: f.name for f in main.catalog.load_table(name).schema().fields}
        after = {f.field_id for f in branch.catalog.load_table(name).schema().fields}
        checked += len(before)
        findings += [Finding(f"removals/{name}.{before[fid]}", "removed without being deprecated on the main branch first")
                     for fid in sorted(before.keys() - after) if (name, fid) not in deprecated]
    return Check("removals", findings, checked, "columns")


def table_properties(main: Side, branch: Side, approvals: frozenset[str]) -> Check:
    """Chapter 7's finding, after the nightly run: every table still carries the properties generated from the branch's
    metadata. A pipeline that creates its table again loses them."""
    findings = []
    wanted = generated_properties(branch.catalog, branch.described, branch.governance)
    for name, properties in sorted(wanted.items()):
        have = branch.catalog.load_table(name).properties
        differ = [key for key, value in properties.items() if have.get(key) != value]
        if differ:
            findings.append(Finding(f"properties/{name}", f"{len(differ)} of {len(properties)} generated properties missing"))
    return Check("properties", findings, len(wanted), "tables")


# ---------------------------------------------------------------- two checks staged to go wrong


def id_columns(main: Side, branch: Side, approvals: frozenset[str]) -> Check:
    """A noisy check, staged for this chapter's "what goes wrong": any column named like an ID is personal data, so one
    classified lower is a finding. Chapter 2 decided otherwise for IDs used for joining, so every finding is waived."""
    columns = [(name, c) for name, table in sorted(branch.described.items()) for c in table.columns.values()]
    findings = [Finding(f"id-columns/{name}.{c.name}", f"named like an ID, classified {c.classification}")
                for name, c in columns if c.name.endswith("_id") and c.classification in ("public", "internal")]
    return Check("id-columns", findings, len(columns), "columns")


def classifier(rows: int | None, name: str) -> Callable[[Side, Side, frozenset[str]], Check]:
    """Chapter 3's classification of support.tickets' text columns as a check: a finding wherever the values support a
    stricter classification than the metadata. `rows` per column, or every row when None: the slow version."""
    def check(main: Side, branch: Side, approvals: frozenset[str]) -> Check:
        table = "support.tickets"
        rows_read = rows or branch.catalog.load_table(table).scan().to_arrow().num_rows
        found = pii.classify(branch.catalog, connect(branch.catalog), pii.platform_detector(), rows_read,
                             only={table: {f.field_id for f in branch.catalog.load_table(table).schema().fields}})
        reviewed = {fid: c.classification for fid, c in branch.described[table].columns.items() if c.classification in LEVELS}
        findings = [Finding(f"{name}/{f.table}.{f.column}", f"{'values support' if f.found else 'its name suggests'} "
                            f"{f.classification}, reviewed as {reviewed[f.field_id]}")
                    for f in found if f.field_id in reviewed
                    and metadata.stricter(f.classification, reviewed[f.field_id]) != reviewed[f.field_id]]
        return Check(name, findings, sum(f.sampled for f in found), "values")
    return check


CHECKS: dict[str, Callable[[Side, Side, frozenset[str]], Check]] = {
    "coverage": coverage, "contract-rules": contract_rules, "contracts": generated_files, "policy-tests": policy_tests,
    "enforcement-tests": enforcement_tests, "migrations": refused_migrations, "pipelines": pipeline_runs,
    "lineage-classifications": lineage_classifications, "owner-review": owner_review, "removals": removals,
    "properties": table_properties,
    "id-columns": id_columns, "classify-sampled": classifier(pii.ROWS, "classify-sampled"),
    "classify-every-row": classifier(None, "classify-every-row"),
}


# ---------------------------------------------------------------- the job


def changes(main: Side, branch: Side) -> list[Change]:
    """Every schema change between the two sides' tables, field ID by field ID, with what it reaches through the main
    branch's lineage graph (lib.lineage.downstream), DIRECT and INDIRECT. A pipeline's table is created again on every
    run, so its field IDs follow the order of its select list."""
    writes = {pipelines.target(path.read_text(encoding="utf-8")): path.stem for path in main.pipelines}
    owner = lambda table: branch.described[table].owner if table in branch.described else "no owner"
    found = []
    for name in sorted(set(retention.tables(main.catalog)) & set(retention.tables(branch.catalog))):
        before = {fid: rest for fid, *rest in metadata.shape(main.catalog.load_table(name).schema())}
        after = {fid: rest for fid, *rest in metadata.shape(branch.catalog.load_table(name).schema())}
        for fid in sorted(before.keys() | after.keys()):
            old, new = before.get(fid), after.get(fid)
            kind = ("added" if old is None else "removed" if new is None else "renamed" if old[0] != new[0]
                    else "retyped" if old[1] != new[1] else "required" if old[2] != new[2] else None)
            if kind is None:
                continue
            column = f"{name}.{(old or new)[0]}"
            edges = lineage.downstream(main.graph, column) if old else []
            reached = lineage.columns_reached(main.graph, edges)
            found.append(Change(name, fid, kind, " ".join(old[:2]) if old else "", " ".join(new[:2]) if new else "",
                                sorted({writes[table_of(e.target)] for e in edges if e.source == column}),
                                lineage.columns_reached(main.graph, [e for e in edges if e.type == "DIRECT"]), reached,
                                sorted({owner(table_of(c)) for c in reached})))
    return found


def _why(error: Exception) -> str:
    """An error in one line: its type and first line, or for regopy, the messages of its parse errors."""
    text = str(error)
    messages = list(dict.fromkeys(re.findall(r"\(errormsg \d+:([^)]*)\)", text)))
    what = "; ".join(messages) if messages else text.splitlines()[0] if text else "no message"
    return what if isinstance(error, Unreadable) else f"{type(error).__name__}: {what}"


def _attempt(name: str, main: Side, branch: Side, approvals: frozenset[str]) -> Check:
    """Run one check. A check that crashes, or that ci.toml names but the job doesn't have, is one finding."""
    try:
        return CHECKS[name](main, branch, approvals)
    except Exception as error:          # one broken file must not hide the other checks' findings
        return Check(name, [Finding(f"{name}/error", f"the check could not run: {_why(error)}")], 0, "", error=_why(error))


def _on_main(main: Side, names: Iterable[str]) -> set[str]:
    """The IDs of the findings the main branch's own job reports: each of `names` that main's ci.toml lists, run on the
    main branch alone, once per side. A check main doesn't list, or that couldn't run there, has none."""
    listed, found = settings(main.governance)["checks"], set()
    for name in names:
        if name in listed:
            if name not in main.reported:
                main.reported[name] = _attempt(name, main, main, frozenset())
            if main.reported[name].error is None:
                found |= {f.id for f in main.reported[name].findings}
    return found


def _owner(finding: str, main: Side, branch: Side, platform: str) -> str:
    """The team answerable for a finding: the owner of the table its subject names, on the main branch if the table is
    there (for a pipeline, the table it writes), else the platform team, which also stands in for a table nobody owns."""
    check, _, subject = finding.partition("/")
    tables = {**branch.described, **main.described}
    if check == "pipelines":
        writes = {path.stem: pipelines.target(path.read_text(encoding="utf-8")) for path in main.pipelines + branch.pipelines}
        subject = writes.get(subject, subject)
    for name in sorted(tables, key=len, reverse=True):
        if subject == name or subject.startswith((f"{name}.", f"{name}/")):
            return tables[name].owner or platform
    return platform


def run(main: Path | Side, branch: Path | Side, approvals: Iterable[str] = (), today: date = TODAY,
        checks: list[str] | None = None) -> Report:
    """The whole job for one pull request: build both sides (or take them built), run each check the branch's ci.toml
    lists, or the ones named, and settle each finding. A finding the main branch's job also reports is on main, with
    its owner. A finding that asks for a team's approval is approved when `approvals`, one team per approving review,
    holds as many of that team's as ci.toml asks. Then the branch's waivers apply, and every schema change is
    reported. The pull request passes when every finding it introduces is waived or approved."""
    try:
        main = main if isinstance(main, Side) else build(main)
        branch = branch if isinstance(branch, Side) else build(branch)
        config = settings(branch.governance)
    except Exception as error:          # a side that can't be built, or settings that can't be read, fail the job
        return Report([Check("build", [Finding("build/error", f"the job could not run: {_why(error)}")], 0, "",
                             error=_why(error))], [], [])
    names = checks or config["checks"]
    results = [_attempt(name, main, branch, frozenset(approvals)) for name in names]
    known, given = _on_main(main, names), Counter(approvals)
    needed = lambda team: config["approvals"]["teams"].get(team, config["approvals"]["default"])
    for check in results:
        check.findings = [replace(f, owner=_owner(f.id, main, branch, config["codeowners"]["platform"]),
                                  on_main=check.error is None and f.id in known,
                                  approved_by=f.approver if f.approver and given[f.approver] >= needed(f.approver) else None)
                          for f in check.findings]
    unused = waive(results, branch.governance, today)
    return Report(results, changes(main, branch), unused)


def _how(finding: Finding) -> str:
    notes = ([f"on main, owner {finding.owner}"] if finding.on_main else []) + \
            ([f"waived by {finding.waiver.owner} until {finding.waiver.expires}"] if finding.waiver else []) + \
            ([f"approved by {finding.approved_by}"] if finding.approved_by else [])
    return "".join(f"; {note}" for note in notes)


def _change(change: Change) -> str:
    what = {"added": f"added as {change.after}", "removed": f"removed, was {change.before}"}.get(
        change.kind, f"{change.kind} from {change.before} to {change.after}")
    return f"{what}; named by {len(change.jobs)} pipelines, reaches {len(change.reached)} columns"


def lines(report: Report) -> list[str]:
    """The report as the job prints it: a line per check, one per finding, one per schema change, one per waiver that
    silenced nothing, then the verdict."""
    out = [f"{c.name}: failed, the check could not run" if c.error else
           f"{c.name}: {'passed' if c.passed else 'failed'}, findings {len(c.findings)}, new {len(c.new)}, "
           f"on main {len(c.on_main)}, waived {len(c.waived)}, {c.checked} {c.unit} checked" for c in report.checks]
    out += [f"{f.id}: {f.message}{_how(f)}" for check in report.checks for f in check.findings]
    out += [f"change {c.table}, field {c.field_id}: {_change(c)}" for c in report.changes]
    out += [f"waiver {waiver.finding}: silences nothing, {why}" for waiver, why in report.unused]
    out.append(f"job: {'passed' if report.passed else 'failed'}")
    return out


def totals(report: Report) -> list[str]:
    """The report's totals, as the job prints them after its lines."""
    found = [f for check in report.checks for f in check.findings]
    return [f"checks run: {len(report.checks)}", f"checks failed: {sum(not c.passed for c in report.checks)}",
            f"findings: {len(found)}", f"findings the pull request introduces: {sum(not f.on_main for f in found)}",
            f"findings already on main: {sum(f.on_main for f in found)}",
            f"findings waived: {sum(f.waiver is not None for f in found)}",
            f"findings approved: {sum(f.approved_by is not None for f in found)}", f"exit code: {report.exit_code}"]


def markdown(report: Report) -> str:
    """The report as Markdown, for a CI step to post where the reviewer reads the diff, such as the pull request's
    summary: the verdict, a table of the checks, what the pull request must fix, what it settled, what was already on
    main and whose it is, the schema changes and the waivers that silence nothing."""
    out = [f"## Governance job: {'passed' if report.passed else 'failed'}", "",
           "| Check | Result | New | On main | Waived | Checked |", "| --- | --- | ---: | ---: | ---: | --- |"]
    out += [f"| `{c.name}` | {'could not run' if c.error else 'passed' if c.passed else 'failed'} | {len(c.new)} | "
            f"{len(c.on_main)} | {len(c.waived)} | {c.checked} {c.unit} |" for c in report.checks]
    found = [f for check in report.checks for f in check.findings]
    for title, chosen in (("To fix in this pull request", [f for f in found if f.failing]),
                          ("Settled in this pull request", [f for f in found if not f.on_main and not f.failing]),
                          ("Already on main, and whose it is", [f for f in found if f.on_main])):
        if chosen:
            out += ["", f"### {title}", ""] + [f"- `{f.id}`: {f.message}" + (f" ({f.owner})" if f.on_main else _how(f))
                                               for f in chosen]
    if report.changes:
        out += ["", "### Schema changes", ""] + [f"- `{c.table}`, field {c.field_id}: {_change(c)}" for c in report.changes]
    if report.unused:
        out += ["", "### Waivers that silence nothing", ""] + [f"- `{w.finding}`: {why}" for w, why in report.unused]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    """The job as a CI step runs it, from the examples folder: `python -m lib.ci MAIN BRANCH [--approved-by TEAM]...
    [--summary FILE]`, where MAIN and BRANCH are the two checkouts and each --approved-by names the team of one
    approving review. Prints the report's lines and totals, writes the report as Markdown to FILE, and returns the
    exit code: 1 when the pull request fails, else 0."""
    parser = argparse.ArgumentParser(prog="python -m lib.ci", description="The platform's governance job.")
    parser.add_argument("main", type=Path, help="the main branch's checkout")
    parser.add_argument("branch", type=Path, help="the pull request's checkout")
    parser.add_argument("--approved-by", action="append", default=[], metavar="TEAM",
                        help="the team of one approving review; repeat it for each approval")
    parser.add_argument("--summary", type=Path, help="where to write the report as Markdown")
    args = parser.parse_args(argv)
    report = run(args.main, args.branch, args.approved_by)
    print("\n".join(lines(report) + totals(report)))
    if args.summary:
        args.summary.write_text(markdown(report), encoding="utf-8")
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
