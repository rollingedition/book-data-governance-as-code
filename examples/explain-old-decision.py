"""Replays a logged decision under the revision it was made with and under the current one, after a change to the users
file alone. The printed email and match lines are the point: only the archived revision explains the record."""
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

# Later, a pull request adds analyst-1 to support for EMEA, as in chapter 6. Only users.toml changes, which reaches
# the policy through its data document, and no Rego.
data = policy.data_document()
data["platform"]["users"]["analyst-1"] = {"groups": ["data-science", "support"], "region": "emea"}
current, logged = audit.bundle(data=data), archive.get(record["policy_revision"])     # the bundle the record was decided under
print("logged under revision:", record["policy_revision"][:12])
print("current revision:", audit.revision(current)[:12])
modules_changed = current["modules"] != logged["modules"]
assert not modules_changed                      # no Rego changed, so the policy modules are the same
print("policy modules changed:", modules_changed)
data_changed = current["data"] != logged["data"]
assert data_changed                             # the data document is the only part that changed
print("data document changed:", data_changed)

# Explain the logged decision by making it again, from the request the record holds, under each revision.
print("email, as logged:", record["decisions"][0]["column_masks"].get("email", "shown as stored"))
for name, revision in [("the current revision", current), ("the logged revision", logged)]:
    again = audit.replay(record, revision)
    matches = again == record["decisions"]
    assert matches == (revision is logged)      # only the revision the record was decided under explains it
    print(f"under {name}, email:", again[0]["column_masks"].get("email", "shown as stored"))
    print(f"under {name}, the decision matches the log:", matches)
