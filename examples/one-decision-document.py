import json

from lib import policy

# Marketing reads four columns of crm.learners. `linkedin_url` stands for a column added to the table and not yet
# classified in governance/metadata.toml.
request = policy.request("marketing-1", "crm.learners", ["full_name", "email", "phone", "linkedin_url"])
decision = policy.decide(request)
print(json.dumps(decision, indent=2))
print("keys:", ", ".join(sorted(decision)))
