from lib import audit
from lib.catalog import open_catalog
from lib.platform import scratch

log = audit.Log(scratch() / "audit.jsonl")
platform = audit.Audited(open_catalog(["crm.learners", "learning.reviews", "billing.payments", "support.tickets"]), log)
day = audit.run_day(platform)
print("queries allowed:", day["allowed"])
print("queries denied:", day["denied"])

# DuckDB's own query log, kept on the layer's connection, against the records written since the last check. The check
# then empties DuckDB's log, which holds the queries' values.
found = platform.check_engine_log()
print("queries DuckDB ran:", found["engine queries"])
print("queries DuckDB ran with no decision record:", len(found["unrecorded"]))

# Two more queries through the layer: one whose caller never fetches it, and one the caller narrows to five rows.
platform.sql("select learner_id, country from crm.learners", "analyst-1")
platform.sql("select learner_id, country from crm.learners", "analyst-1").limit(5).fetchall()
# Then a notebook helper that was handed the layer's connection reads a learner's email with it, around the layer.
platform.enforcer.connection.sql("select email from crm.learners where learner_id = 541").fetchall()
found = platform.check_engine_log()
print("next check, records checked:", " to ".join(map(str, found["records checked"])))
print("next check, queries with no decision record:", len(found["unrecorded"]))
for query in found["unrecorded"]:
    print("unrecorded:", query)
print("next check, decision records DuckDB has no query for:", len(found["not in the engine log"]))
