import cedarpy

from lib import policy
from lib.platform import load

guarded = policy.CEDAR_POLICIES.read_text(encoding="utf-8")
unguarded = guarded.replace("principal has region && ", "")            # the same forbid, without its `has` check
schema = policy.CEDAR_SCHEMA.read_text(encoding="utf-8")

# A new support agent with no region yet, and the first learner of crm.learners in each region.
data = policy.data_document()
data["platform"]["users"]["agent-new"] = {"groups": ["support"]}
first = {}
for learner in load("crm.learners").select(["learner_id", "support_region"]).to_pylist():
    first.setdefault(learner["support_region"], learner)
entities = policy.cedar_entities(learners=first.values(), data=data)

for name, policies in [("with `has region`", guarded), ("without it", unguarded)]:
    for region, learner in sorted(first.items()):
        request = {"principal": 'User::"agent-new"', "action": 'Action::"read"',
                   "resource": f'Learner::"{learner["learner_id"]}"', "context": {}}
        answer = cedarpy.is_authorized(request, policies, entities, schema)
        print(f"{name}, a learner in {region}:", answer.decision.value)
    print(f"{name}, policies skipped on an error:", list(answer.diagnostics.errors))
print("validation without it:", cedarpy.validate_policies(unguarded, schema).errors[0].error)
