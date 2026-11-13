"""The request a support agent's read sends the policy, shaped as Trino's OPA plugin sends it. The point rests on the printed `action`: the operation SelectFromColumns, and the table with the columns the query reads."""
import json

from lib import policy

# A support agent's query reads three columns of the tickets table: the request Trino's OPA plugin would send for it.
# no groups here, because the policy reads them from users.toml; no purpose either, since request adds one only when given
request = policy.request("agent-a05", "support.tickets", ["ticket_id", "opened_at", "body"])
print(json.dumps(request, indent=2))
