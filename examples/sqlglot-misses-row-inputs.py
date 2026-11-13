"""What sqlglot.lineage says the pipeline outputs come from, and what it leaves out. The prose rests on the sources of
the two course performance columns, and on the assertion that neither column the WHERE reads reaches any output."""
import sqlglot
from sqlglot import exp

from lib import lineage, pipelines
from lib.catalog import open_catalog

# sqlglot resolves each name against the catalog's columns, so lineage.schema builds them from the table metadata.
schema = lineage.schema(open_catalog(["crm.learners", "learning.courses", "learning.enrollments", "learning.reviews",
                                      "billing.payments"]))
statement = pipelines.sql("marketing_audience")
found = lineage.sqlglot_sources(statement, schema)
for column, sources in found.items():
    print(f"marketing.audience.{column} comes from:", ", ".join(sources))

parsed = sqlglot.parse_one(statement, read="duckdb")
# the WHERE names columns by alias, as in l.status, so each alias maps to its table
tables = {table.alias_or_name: f"{table.db}.{table.name}" for table in parsed.find_all(exp.Table)}
where = parsed.find(exp.Where)
print("the pipeline's WHERE:", where.this.sql(dialect="duckdb"))
in_lineage = {source for sources in found.values() for source in sources}     # every source column of every output
for column in where.find_all(exp.Column):
    source = f"{tables[column.table]}.{column.name}"
    assert source not in in_lineage        # every column the WHERE reads, checked against the lineage above
    print(f"{source} in any column's lineage:", source in in_lineage)

course_performance = lineage.sqlglot_sources(pipelines.sql("course_performance"), schema)
for column in ("completions", "revenue_usd"):    # the two aggregates that carry a FILTER inside them
    print(f"analytics.course_performance.{column} comes from:", ", ".join(course_performance[column]))
