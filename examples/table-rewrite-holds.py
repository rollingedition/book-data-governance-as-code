import logging
import re

from lib.catalog import open_catalog
from lib.enforce import EMAIL, Enforcer, QueryFailed, answer

platform = Enforcer(open_catalog(["crm.learners"]))
operator_log = []                     # the platform team's log, kept in a list here so the example can print it
logging.getLogger("lib.enforce").addHandler(handler := logging.Handler())
handler.emit = lambda record: operator_log.append(record.getMessage().splitlines()[0])

# The five attacks on the column rewrite, run by analyst-1 again: through the table rewrite this time, and through
# `answer`, the function a service calls at its boundary.
email = platform.connection.sql("select email from crm.learners where learner_id = 2").fetchall()[0][0]
attacks = {
    "a WHERE on the raw column": f"select learner_id from crm.learners where email = '{email}'",
    "the whole row as a struct": "select struct_extract(l, 'email') from crm.learners as l where learner_id = 2",
    "COLUMNS(...)": "select columns('^email$') from crm.learners where learner_id = 2",
    "to_json": "select to_json(l) ->> 'email' from crm.learners as l where learner_id = 2",
    "a cast error": "select learner_id from crm.learners where cast(email as integer) = 0",
}
shorten = lambda text: re.sub(r"\b([0-9a-f]{8})[0-9a-f]{56}\b", r"\1…", re.sub(EMAIL, "<a stored email>", text))
for name, sql in attacks.items():
    try:
        got = str(answer(platform, sql, "analyst-1"))
    except QueryFailed as error:
        got = f"{type(error).__name__}, {error}"
    print(f"{name}:", shorten(got))
for line in operator_log:
    print("in the operator's log:", shorten(line))      # a pseudonym, 64 hex digits, shortened

# Why `answer` returns rows: the relation Enforcer.sql returns is a handle on the layer's connection, and runs any SQL.
relation = platform.sql("select learner_id from crm.learners", "analyst-1")
print("the relation, running SQL of its own:", shorten(str(relation.query("r", "select email from crm.learners where learner_id = 2").fetchall())))
print("what answer returns:", type(answer(platform, "select learner_id from crm.learners", "analyst-1")).__name__)
