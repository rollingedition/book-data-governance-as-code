"""Masks the email in the select list only, then runs five reads aimed at one learner's email through that rewrite and
prints what each returns. The prose's point rests on the five attack lines, each of which returns learner 2's ID, the
stored email, or an error that holds it."""
import re

import duckdb

from lib.catalog import open_catalog
from lib.enforce import EMAIL, Enforcer

platform = Enforcer(open_catalog(["crm.learners"]))
# analyst-1, a data scientist, may read crm.learners with `email` pseudonymized. The attacks aim at learner 2.
email = platform.connection.sql("select email from crm.learners where learner_id = 2").fetchall()[0][0]
print("the column rewrite:", platform.mask_select_list("select learner_id, email from crm.learners", "analyst-1"))

attacks = {
    "a WHERE on the raw column": f"select learner_id from crm.learners where email = '{email}'",
    "the whole row as a struct": "select struct_extract(l, 'email') from crm.learners as l where learner_id = 2",
    "COLUMNS(...)": "select columns('^email$') from crm.learners where learner_id = 2",
    "to_json": "select to_json(l) ->> 'email' from crm.learners as l where learner_id = 2",
    "a cast error": "select learner_id from crm.learners where cast(email as integer) = 0",
}
for name, sql in attacks.items():
    try:
        # the column rewrite changes only the select list, so every other clause reads the stored email
        answer = str(platform.run(platform.mask_select_list(sql, "analyst-1")).fetchall())
    except duckdb.Error as error:
        answer = str(error).splitlines()[0]
    print(f"{name}:", re.sub(EMAIL, "<a stored email>", answer))      # the book never prints a stored email
