"""The book's column lineage: each column's inputs as OpenLineage types them, plus the inputs that shape every row,
which sqlglot.lineage leaves out."""
from lib import lineage, pipelines
from lib.catalog import open_catalog

schema = lineage.schema(open_catalog(["crm.learners", "learning.courses", "learning.enrollments", "learning.reviews",
                                      "billing.payments"]))
fields, rows = lineage.column_lineage(pipelines.sql("marketing_audience"), schema)
for column, inputs in fields.items():
    print(f"marketing.audience.{column}:", ", ".join(f"{dependency.source} {dependency.type} {dependency.subtype}"
                                                    for dependency in inputs))
print("inputs that shape every row of marketing.audience:")
for source in sorted({dependency.source for dependency in rows}):
    print(f"  {source}:", "INDIRECT",
          ", ".join(dependency.subtype for dependency in rows if dependency.source == source))

values = {dependency.source for inputs in fields.values() for dependency in inputs}
print("source columns, values only:", len(values))
print("source columns, with the rows' inputs:", len(values | {dependency.source for dependency in rows}))

fields, _ = lineage.column_lineage(pipelines.sql("course_performance"), schema)
for column in ("completions", "revenue_usd"):
    print(f"analytics.course_performance.{column}:",
          ", ".join(f"{dependency.source} {dependency.type} {dependency.subtype}" for dependency in fields[column]))
