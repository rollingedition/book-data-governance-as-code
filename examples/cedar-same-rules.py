"""The platform's Cedar policies checked against their schema, then asked the questions the Rego policy answers, over entities generated from its data. The points rest on the printed decisions, and on the misspelt-region error the schema gives before any request."""
import cedarpy

from lib import policy
from lib.platform import load

policies = policy.CEDAR_POLICIES.read_text(encoding="utf-8")
schema = policy.CEDAR_SCHEMA.read_text(encoding="utf-8")
print("policies valid against the schema:", cedarpy.validate_policies(policies, schema).validation_passed)
# misspells region in the policy's text, so the schema reports the attribute that User lacks
typo = cedarpy.validate_policies(policies.replace("principal.region", "principal.regoin"), schema)
# the validator's first error
print("with region misspelt:", typo.errors[0].error)

# Entities generated from the files the Rego policy reads, plus the first learner of crm.learners in each region.
first = {}
for learner in load("crm.learners").select(["learner_id", "support_region"]).to_pylist():
    # setdefault keeps the first learner seen in each region
    first.setdefault(learner["support_region"], learner)
entities = policy.cedar_entities(learners=first.values())


def ask(user: str, resource: str) -> cedarpy.AuthzResult:
    request = {"principal": f'User::"{user}"', "action": 'Action::"read"', "resource": resource, "context": {}}
    return cedarpy.is_authorized(request, policies, entities, schema)


for user, kind, name in [("finance-1", "Column", "billing.payments.card_number"),
                         ("analyst-1", "Column", "billing.payments.card_number"),
                         ("analyst-1", "Column", "billing.payments.amount"),
                         ("marketing-1", "Table", "billing.payments")]:
    print(f"{user} reads {name}:", ask(user, f'{kind}::"{name}"').decision.value)
for region, learner in sorted(first.items()):
    print(f"agent-a05 (emea) reads a learner in {region}:", ask("agent-a05", f'Learner::"{learner["learner_id"]}"').decision.value)
answer = ask("finance-1", 'Column::"billing.payments.card_number"')
# policy3 is the fourth policy in the file, named by position, not by its @id
print("everything Cedar returns for finance-1's card numbers:", answer.decision.value, list(answer.diagnostics.reasons), list(answer.diagnostics.errors))
