import duckdb

from lib import audit
from lib.catalog import open_catalog
from lib.platform import scratch

AROUND = "select email from crm.learners where learner_id = 541"       # a read around the enforcement layer
folder = scratch()

# A layer that doesn't lock its configuration: code holding its connection tries a file log, then switches logging off.
unlocked = audit.Audited(open_catalog(["crm.learners"]), audit.Log(folder / "unlocked.jsonl"), lock=False)
connection = unlocked.enforcer.connection
try:
    connection.execute("set log_query_path = 'queries.log'")
except duckdb.Error as error:
    print("a query log in a file, on the layer's connection:", type(error).__name__)
connection.execute("CALL disable_logging()")
connection.sql(AROUND).fetchall()
for query in unlocked.check_engine_log()["unrecorded"]:
    print("unlocked, unrecorded:", query)

# lib.audit's layer locks its configuration: switching the log off is refused, but emptying it isn't.
log = audit.Log(folder / "locked.jsonl")
locked = audit.Audited(open_catalog(["crm.learners"]), log)
locked.sql("select learner_id, email from crm.learners", "analyst-1").fetchall()
connection = locked.enforcer.connection
try:
    connection.execute("CALL disable_logging()")
except duckdb.Error as error:
    print("locked, disable_logging():", type(error).__name__)
connection.sql(AROUND).fetchall()
connection.execute("CALL truncate_duckdb_logs()")
found = locked.check_engine_log()
print("locked, after truncate_duckdb_logs(), queries with no decision record:", len(found["unrecorded"]))
print("locked, decision records DuckDB has no query for:", len(found["not in the engine log"]))
