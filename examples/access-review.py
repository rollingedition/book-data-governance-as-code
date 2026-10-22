from lib import audit, policy
from lib.catalog import open_catalog
from lib.platform import TABLES, scratch

log = audit.Log(scratch() / "audit.jsonl")
platform = audit.Audited(open_catalog(["crm.learners", "learning.reviews", "billing.payments", "support.tickets"]), log)
audit.run_day(platform)

# users.toml as the review finds it: analyst-2 is still listed in bi, a group the policy no longer has.
data = policy.data_document()
data["platform"]["users"]["analyst-2"] = {"groups": ["bi"]}
rows = audit.review(audit.bundle(data=data), log.records(), TABLES)      # all eight of the platform's tables

for row in rows:
    print(f"{row['user']}: may read {len(row['may read'])}, read {len(row['read'])}, denied {row['denied']}"
          + (f" (sent by {', '.join(row['denied via'])})" if row["denied via"] else ""))
    if row["unused"]:
        print("  not used:", ", ".join(row["unused"]))
    for finding in row["findings"]:
        print("  look at:", finding)
print("users reviewed:", len(rows))
print("grants not used:", sum(len(row["unused"]) for row in rows))
print("users to look at:", sum(bool(row["findings"]) for row in rows))
print("denied queries flagged at:", audit.settings()["review"]["denied_queries_flagged"])
