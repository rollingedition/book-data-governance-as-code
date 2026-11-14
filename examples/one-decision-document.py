"""The whole decision for one read: marketing's four columns of crm.learners, one of them unclassified. The point rests on the masks and the reasons, which give one line per mask and per group that allowed the read."""
import json

from lib import policy

# Marketing reads four columns of crm.learners. `linkedin_url` stands for a column added to the table and not yet
# classified in governance/metadata.toml.
request = policy.request("marketing-1", "crm.learners", ["full_name", "email", "phone", "linkedin_url"])
# with no interpreter given, decide loads the platform's policy itself
decision = policy.decide(request)
print(json.dumps(decision, indent=2))
print("keys:", ", ".join(sorted(decision)))
