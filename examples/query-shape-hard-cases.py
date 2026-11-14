"""Eleven places to type a learner's values into a query, and what each record keeps of it: the query's shape, with a
`?` for each value and each name the catalog doesn't hold, and a numbered alias for each alias or CTE the query defines.
The printed count of records that hold the email, the name or the ID
is the line the prose rests on."""
import json
import re

from lib import audit
from lib.catalog import open_catalog
from lib.enforce import Denied
from lib.platform import load, scratch

log = audit.Log(scratch() / "audit.jsonl")
platform = audit.Audited(open_catalog(["crm.learners"]), log)

# An EMEA learner's email, name and ID, typed into agent-a05's queries as values and as names. None is ever printed.
learner = next(row for row in load("crm.learners").to_pylist() if row["support_region"] == "emea")
email, name, learner_id = learner["email"], learner["full_name"], learner["learner_id"]
cases = {                                                 # each query puts the same values in one more way
    "a string": f"select learner_id from crm.learners where email = '{email}'",
    "a dollar-quoted string": f"select learner_id from crm.learners where email = $${email}$$",
    "an escape string": f"select learner_id from crm.learners where email = e'{email}'",
    "a list": f"select learner_id from crm.learners where email in ['{email}']",
    "a number": f"select email from crm.learners where learner_id = {learner_id}",
    "a comment": f"select email from crm.learners where learner_id = {learner_id} -- {name}",
    "a name in double quotes": f'select learner_id from crm.learners where full_name = "{name}"',    # a column, to DuckDB
    "a column alias": f'select learner_id as "{email}" from crm.learners',
    "a table alias": f'select "{name}".email from crm.learners as "{name}"',
    "a CTE's name": f'with "{email}" as (select learner_id from crm.learners) select learner_id from "{email}"',
    "a table the catalog doesn't hold": f'select learner_id from "{email}"',
}

for query in cases.values():
    try:
        platform.sql(query, "agent-a05").fetchall()      # a denial is recorded, then raised: the script carries on
    except Denied:
        pass

# What each record keeps of its query. The email or the name anywhere in its line, or the ID in what it keeps, is a
# value that got through, and is never printed.
held = []
for case, line in zip(cases, log.lines()):               # one record per query, in the order they ran
    record = json.loads(line)
    kept = record["query"] + (f" (denied: {record['denial']})" if record["denial"] else "")
    held.append(email in line or name in line or bool(re.search(rf"\b{learner_id}\b", kept)))     # the ID as a whole word
    print(f"{case}:", "(holds a value)" if held[-1] else kept)
print("records holding the email, the name or the ID:", sum(held), "of", len(cases))
