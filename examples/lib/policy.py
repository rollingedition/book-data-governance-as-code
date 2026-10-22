"""Access decisions from the platform's Rego policies (governance/policies/), evaluated in-process with regopy.

A decision is the `data.platform.access.decision` document, always bound to a variable: queried on its own, a rule
whose value is false comes back "undefined" from regopy, where OPA would return false."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Iterable, NamedTuple

from regopy import Input, Interpreter

from lib import metadata
from lib.platform import load

GOVERNANCE = Path(__file__).resolve().parent.parent / "governance"
POLICIES = GOVERNANCE / "policies"
USERS = GOVERNANCE / "users.toml"
CEDAR_POLICIES = POLICIES / "access.cedar"            # the same rules in Cedar, for comparison only
CEDAR_SCHEMA = POLICIES / "access.cedarschema"


def data_document() -> dict:
    """What the policies read besides the request, generated from the files that own it: users and their attributes
    (governance/users.toml), each column's classification (governance/metadata.toml), and each instructor's courses
    (learning.courses)."""
    users = tomllib.loads(USERS.read_text(encoding="utf-8"))["users"]
    classifications = {name: {c.name: c.classification for c in table.columns.values()} for name, table in metadata.load().items()}
    courses = load("learning.courses")
    by_instructor: dict[str, list[int]] = {}
    for instructor, course in zip(courses.column("instructor_id").to_pylist(), courses.column("course_id").to_pylist()):
        by_instructor.setdefault(str(instructor), []).append(course)
    return {"platform": {"users": users, "classifications": classifications,
                         "instructor_courses": {k: sorted(v) for k, v in sorted(by_instructor.items())}}}


def request(user: str, table: str, columns: list[str], purpose: str | None = None) -> dict:
    """A request to read some columns of a table, shaped like the requests Trino's OPA plugin sends."""
    schema, name = table.split(".")
    found = {"context": {"identity": {"user": user}},
             "action": {"operation": "SelectFromColumns",
                        "resource": {"table": {"catalogName": "platform", "schemaName": schema, "tableName": name,
                                               "columns": list(columns)}}}}
    return found | ({"purpose": purpose} if purpose else {})


def interpreter(folder: Path = POLICIES, tests: bool = False, data: dict | None = None) -> Interpreter:
    """An interpreter with every policy in the folder loaded, its tests too when asked, and the data document."""
    rego = Interpreter()
    rego.add_data_json(json.dumps(data if data is not None else data_document()))
    for path in sorted(folder.glob("*.rego")):
        if tests or not path.stem.endswith("_test"):
            rego.add_module(path.stem, path.read_text(encoding="utf-8"))
    return rego


def evaluate(rego: Interpreter, document: str, request: dict) -> dict:
    """The value of a policy document for one request, as Python values. A failed query raises RuntimeError: its
    output is never read, because regopy crashes when printing some failed outputs."""
    rego.set_input(Input(request))
    output = rego.query(f"result := {document}")
    if not output.ok():
        raise RuntimeError(f"regopy couldn't evaluate {document}, so there is no decision to read")
    return json.loads(str(output))["bindings"]["result"]


def decide(request: dict, rego: Interpreter | None = None) -> dict:
    """The platform's access decision for one request: allow, row filters, column masks and the reasons."""
    return evaluate(rego or interpreter(), "data.platform.access.decision", request)


def run_tests(folder: Path = POLICIES, data: dict | None = None) -> dict[str, str]:
    """Run every `test_` rule in the folder's `_test.rego` files, as `opa test` would: PASS when the rule is true, FAIL
    when it's false or undefined, ERROR when evaluation fails. Each test package is queried once; regopy leaves the
    tests that didn't pass out of it, so those are queried one by name, to tell a FAIL from an ERROR. Every output's
    status is checked before the output is read, because regopy crashes when printing some failed outputs."""
    rego = interpreter(folder, tests=True, data=data)
    results = {}
    for path in sorted(folder.glob("*_test.rego")):
        source = path.read_text(encoding="utf-8")
        package = re.search(r"^package\s+([\w.]+)", source, re.M).group(1)
        passed = _bound(rego, f"data.{package}") or {}
        for test in re.findall(r"^(test_\w+)\s+if\b", source, re.M):
            if passed.get(test) is True:
                results[test] = "PASS"
                continue
            output = rego.query(f"result := data.{package}.{test}")
            if not output.ok():
                results[test] = "ERROR"
                continue
            text = str(output)
            results[test] = "PASS" if text != "undefined" and json.loads(text)["bindings"]["result"] is True else "FAIL"
    return results


def _bound(rego: Interpreter, document: str):
    """A document's value, or None when it's undefined or its query fails (a failed output is never printed)."""
    output = rego.query(f"result := {document}")
    if not output.ok():
        return None
    text = str(output)
    return None if text == "undefined" else json.loads(text)["bindings"]["result"]


class Case(NamedTuple):
    """One row of the who-sees-what table: a request, and what the policy must decide for it."""
    user: str
    table: str
    columns: list[str]
    purpose: str | None
    allowed: bool
    filtered_on: list[str]              # the columns the row filters test, sorted
    masked: dict[str, str]              # each masked column and its mask


# Who sees what on the platform, one request per row, as a reviewer would check it. Chapter 6 runs the same rows
# through the query rewrite and checks the rows and values each user actually gets.
WHO_SEES_WHAT = [
    Case("agent-a05", "crm.learners", ["learner_id", "email", "notes"], None, True, ["support_region"], {}),
    Case("agent-a05", "billing.payments", ["amount"], None, False, [], {}),
    Case("instructor-17", "learning.reviews", ["rating", "body"], None, True, ["course_id"], {"body": "redact_contacts"}),
    Case("instructor-17", "crm.learners", ["full_name"], None, False, [], {}),
    Case("marketing-1", "crm.learners", ["email", "phone"], None, True, ["marketing_consent", "status"], {"phone": "redact"}),
    Case("finance-1", "billing.payments", ["amount", "card_number"], None, True, [], {"card_number": "last4"}),
    Case("analyst-1", "billing.payments", ["amount", "card_number"], None, True, [], {"card_number": "redact"}),
    Case("analyst-1", "crm.learners", ["learner_id", "email", "notes"], None, True, [],
         {"email": "pseudonymize", "notes": "redact"}),
    Case("analyst-1", "learning.reviews", ["rating", "body"], None, True, [], {"body": "redact"}),
    Case("analyst-1", "learning.reviews", ["rating", "body"], "ai-training", True, ["ai_training_consent"],
         {"body": "redact_contacts"}),
    Case("assistant-1", "learning.reviews", ["rating", "body"], None, True, ["ai_training_consent"],
         {"body": "redact_contacts"}),
    Case("someone-else", "crm.learners", ["email"], None, False, [], {}),
]


def filtered_on(decision: dict) -> list[str]:
    """The columns a decision's row filters test, sorted. The consent filter tests the review's author, in crm.learners."""
    return sorted(f["column"] if "column" in f else f["learner_consented"] for f in decision["row_filters"])


def matches(case: Case, decision: dict) -> bool:
    """Whether a decision is the one the who-sees-what table expects."""
    return (decision["allow"], filtered_on(decision), decision["column_masks"]) == (case.allowed, case.filtered_on, case.masked)


def cedar_entities(learners: Iterable[dict] = (), data: dict | None = None) -> list[dict]:
    """The entities access.cedar decides over, generated from the same data document the Rego policy reads: users with
    their groups and region, tables, columns with their classification, and the learners given (rows of crm.learners)."""
    platform = (data if data is not None else data_document())["platform"]
    uid = lambda kind, name: {"type": kind, "id": str(name)}
    users = platform["users"]
    groups = sorted({group for user in users.values() for group in user["groups"]})
    entities = [{"uid": uid("Group", group), "attrs": {}, "parents": []} for group in groups]
    entities += [{"uid": uid("User", name), "attrs": {k: v for k, v in user.items() if k == "region"},
                  "parents": [uid("Group", group) for group in user["groups"]]} for name, user in sorted(users.items())]
    for table, columns in sorted(platform["classifications"].items()):
        entities.append({"uid": uid("Table", table), "attrs": {}, "parents": []})
        entities += [{"uid": uid("Column", f"{table}.{column}"), "attrs": {"classification": level},
                      "parents": [uid("Table", table)]} for column, level in sorted(columns.items())]
    entities += [{"uid": uid("Learner", learner["learner_id"]), "attrs": {"support_region": learner["support_region"]},
                  "parents": [uid("Table", "crm.learners")]} for learner in learners]
    return entities
