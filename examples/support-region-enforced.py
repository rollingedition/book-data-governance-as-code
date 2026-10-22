"""One access decision from the platform's Rego policy, and the same decision enforced on one query."""
import json

from lib import policy
from lib.catalog import open_catalog
from lib.enforce import Enforcer, answer
from lib.platform import load

# agent-a05, a support agent in EMEA, asks to read three columns of crm.learners.
request = policy.request("agent-a05", "crm.learners", ["learner_id", "email", "support_region"])
decision = policy.decide(request)
print("allow:", decision["allow"])
print("row filters:", json.dumps(decision["row_filters"]))
print("column masks:", json.dumps(decision["column_masks"]))
print("reasons:", "; ".join(decision["reasons"]))

# The enforcement layer asks the policy about every table a query reads, then rewrites the query to apply the answer.
layer = Enforcer(open_catalog(["crm.learners"]))
query = "select support_region, count(*) as learners from crm.learners group by support_region order by support_region"
for region, learners in answer(layer, query, "agent-a05"):
    print(f"learners agent-a05 sees in {region}:", f"{learners:,}")

# Compare the emails the agent gets with the platform's data, without printing any.
stored = load("crm.learners")
emails = dict(zip(stored.column("learner_id").to_pylist(), stored.column("email").to_pylist()))
seen = answer(layer, "select learner_id, email from crm.learners", "agent-a05")
print("emails agent-a05 sees, as stored:", f"{sum(email == emails[learner] for learner, email in seen):,} of {len(seen):,}")
print("learners in the platform's data:", f"{stored.num_rows:,}")
