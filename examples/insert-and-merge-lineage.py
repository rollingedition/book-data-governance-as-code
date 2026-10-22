"""Two incremental statements sqlglot.lineage answers without complaint and gets wrong: an INSERT, whose outputs it
names after the SELECT, and a MERGE, for which it returns nothing, or the columns of the subquery its USING names."""
import pyarrow as pa
from sqlglot.lineage import lineage as sqlglot_lineage

from lib import lineage
from lib.catalog import open_catalog

catalog = open_catalog(["billing.payments", "crm.learners"])
catalog.create_namespace("analytics")             # the incremental table, as it exists before the day's INSERT
catalog.create_table("analytics.daily_revenue", schema=pa.schema([("day", pa.date32()), ("revenue_usd", pa.float64())]))
schema = lineage.schema(catalog)
insert = """insert into analytics.daily_revenue
select cast(p.paid_at as date), sum(p.amount) from billing.payments p where p.status = 'succeeded' group by 1"""
merges = {"USING a table": """merge into analytics.learner_activity a using crm.learners l
on a.learner_id = l.learner_id when matched then update set country = l.country""",
          "USING a subquery": """merge into analytics.learner_activity a
using (select l.learner_id, l.country, l.email from crm.learners l) s on a.learner_id = s.learner_id
when not matched then insert (learner_id, country) values (s.learner_id, s.country)"""}

print("analytics.daily_revenue columns:", ", ".join(schema["analytics"]["daily_revenue"]))
print("sqlglot, INSERT outputs:", ", ".join(sqlglot_lineage(None, insert, schema, dialect="duckdb")))
fields, rows = lineage.column_lineage(insert, schema)
for column, inputs in fields.items():
    print(f"book, INSERT {column}:", ", ".join(f"{dependency.source} {dependency.type} {dependency.subtype}"
                                               for dependency in inputs))
print("book, INSERT rows shaped by:", ", ".join(f"{dependency.source} {dependency.subtype}" for dependency in rows))
for label, merge in merges.items():
    outputs = sqlglot_lineage(None, merge, schema, dialect="duckdb")
    print(f"sqlglot, MERGE {label}, outputs:", ", ".join(outputs) or "none")

refused = {"INSERT with one value too many": insert.replace("sum(p.amount)", "sum(p.amount), count(*)"),
           **{f"MERGE {label}": merge for label, merge in merges.items()}}
for label, statement in refused.items():
    try:
        lineage.column_lineage(statement, schema)
    except lineage.LineageError as error:
        print(f"book, {label}:", error)
