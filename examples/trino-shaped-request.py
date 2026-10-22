import json

from lib import policy

# A support agent's query reads three columns of the tickets table: the request Trino's OPA plugin would send for it.
request = policy.request("agent-a05", "support.tickets", ["ticket_id", "opened_at", "body"])
print(json.dumps(request, indent=2))
