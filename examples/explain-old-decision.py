from lib import audit, policy
from lib.catalog import open_catalog
from lib.platform import scratch

folder = scratch()
archive = audit.Archive(folder / "bundles")         # every policy revision that has decided anything, by revision
log = audit.Log(folder / "audit.jsonl")

# analyst-1 reads learners' emails, which the policy pseudonymizes.
platform = audit.Audited(open_catalog(["crm.learners"]), log, archive=archive)
platform.sql("select learner_id, email from crm.learners", "analyst-1").fetchall()
record = log.records()[0]

# Later, a pull request adds analyst-1 to support for EMEA, as in chapter 6. Only users.toml changes.
data = policy.data_document()
data["platform"]["users"]["analyst-1"] = {"groups": ["data-science", "support"], "region": "emea"}
current, logged = audit.bundle(data=data), archive.get(record["policy_revision"])
print("logged under revision:", record["policy_revision"][:12])
print("current revision:", audit.revision(current)[:12])
print("policy modules changed:", current["modules"] != logged["modules"])
print("data document changed:", current["data"] != logged["data"])

# Explain the logged decision by making it again, from the request the record holds, under each revision.
print("email, as logged:", record["decisions"][0]["column_masks"].get("email", "shown as stored"))
for name, revision in [("the current revision", current), ("the logged revision", logged)]:
    again = audit.replay(record, revision)
    print(f"under {name}, email:", again[0]["column_masks"].get("email", "shown as stored"))
    print(f"under {name}, the decision matches the log:", again == record["decisions"])
