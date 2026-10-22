"""A misspelt column: sqlglot raises when the column is qualified, and when it isn't, in a query on one table, traces it
to that table anyway. lib.lineage checks every source against the schema."""
from sqlglot.errors import OptimizeError

from lib import lineage
from lib.catalog import open_catalog

schema = lineage.schema(open_catalog(["crm.learners"]))
qualified = "create table marketing.audience as select l.learner_id, l.emial from crm.learners l"
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
