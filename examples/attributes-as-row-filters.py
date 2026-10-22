import json

from lib import policy

rego = policy.interpreter()

# Region is the agent's attribute, consent the learner's, and purpose the request's. Each becomes a row filter.
reads = [
    ("agent-a01", "support.tickets", ["ticket_id"], None),
    ("agent-a05", "support.tickets", ["ticket_id"], None),
    ("agent-a09", "support.tickets", ["ticket_id"], None),
    ("instructor-17", "learning.reviews", ["rating"], None),
    ("marketing-1", "crm.learners", ["email"], None),
    ("analyst-1", "learning.reviews", ["rating", "body"], None),
    ("analyst-1", "learning.reviews", ["rating", "body"], "ai-training"),
]
for user, table, columns, purpose in reads:
    decision = policy.decide(policy.request(user, table, columns, purpose), rego)
    who = user + (f" for {purpose}" if purpose else "")
    print(f"{who} reads {table}, row filters:", json.dumps(decision["row_filters"]))
    if "body" in columns:
        print(f"{who} reads {table}, body shown as:", decision["column_masks"]["body"])
