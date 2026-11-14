"""A misspelt column, emial for email, in a query on one table: sqlglot raises when the column is qualified by its
alias, and traces it to the table when it isn't. The prose rests on the sqlglot answer for each form, and on the book's
check against the schema."""
from sqlglot.errors import OptimizeError

from lib import lineage
from lib.catalog import open_catalog

schema = lineage.schema(open_catalog(["crm.learners"]))    # the schema lets sqlglot check each column name
# the alias l is right, and only the column name is misspelt
qualified = "create table marketing.audience as select l.learner_id, l.emial from crm.learners l"
# no alias, so nothing names the table the column comes from
unqualified = "create table marketing.audience as select learner_id, emial from crm.learners"

try:
    lineage.sqlglot_sources(qualified, schema)
except OptimizeError as error:
    print("sqlglot, qualified l.emial:", f"{type(error).__name__}: {error}")
print("sqlglot, unqualified emial comes from:", ", ".join(lineage.sqlglot_sources(unqualified, schema)["emial"]))
try:
    lineage.column_lineage(unqualified, schema)
except lineage.LineageError as error:
    print("book, unqualified emial:", error)
