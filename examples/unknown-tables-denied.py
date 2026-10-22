import sqlglot
from sqlglot.optimizer.qualify import qualify

from lib.catalog import open_catalog
from lib.enforce import Denied, Enforcer

platform = Enforcer(open_catalog(["crm.learners"]))

# marketing.audience stands for a table a pipeline wrote after the schema was read. qualify alone lets it through.
query = "select * from crm.learners as l join marketing.audience as a on a.learner_id = l.learner_id"
print("qualify on its own:", qualify(sqlglot.parse_one(query, read="duckdb"), schema=platform.schema, dialect="duckdb").sql("duckdb"))

# The enforcement layer denies anything that isn't a catalog table, before it asks the policy.
attempts = {
    "a table missing from the catalog": query,
    "a column the table doesn't have": "select shoe_size from crm.learners",
    "the scan behind a view": "select count(*) from scan_crm_learners",
    "a Parquet file": "select count(*) from read_parquet('data/platform/learners.parquet')",
    "a table function": "select count(*) from query_table('crm.learners')",
    "a statement that isn't a query": "summarize crm.learners",
}
for name, sql in attempts.items():
    try:
        platform.rewrite(sql, "analyst-1")
        print(f"{name}: allowed")
    except Denied as reason:
        print(f"{name}: denied, {reason}")

# What those reads would do on the layer's own connection, without the rewrite in front of it.
rows = lambda sql: f"{platform.connection.sql(sql).fetchall()[0][0]:,} rows"
print("the scan, run directly:", rows("select count(*) from scan_crm_learners"))
print("the table function, run directly:", rows("select count(*) from query_table('crm.learners')"))
try:
    rows("select count(*) from read_parquet('data/platform/learners.parquet')")
except Exception as error:
    print("the file, run directly:", str(error).split(":")[0])
try:
    platform.connection.execute("set enable_external_access = true")
except Exception as error:
    print("turning external access back on:", str(error).split(": ", 1)[1])
