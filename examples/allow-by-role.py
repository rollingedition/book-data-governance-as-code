from lib import policy

rego = policy.interpreter()

# The groups the policy knows, and the tables each may read: the role part of the policy.
readable = policy.evaluate(rego, "data.platform.access.readable", {})
for group, tables in readable.items():
    print(f"{group} may read:", ", ".join(tables))

# Who may read what. Anything no rule allows is denied, including a user or an operation the policy has never seen.
requests = [
    policy.request("finance-1", "billing.payments", ["amount"]),
    policy.request("marketing-1", "billing.payments", ["amount"]),
    policy.request("someone-else", "crm.learners", ["learner_id"]),
]
delete = policy.request("finance-1", "billing.payments", ["amount"])
delete["action"]["operation"] = "DeleteFromTable"
for request in requests + [delete]:
    decision = policy.decide(request, rego)
    who, table = request["context"]["identity"]["user"], request["action"]["resource"]["table"]
    print(f"{who}, {request['action']['operation']} on {table['schemaName']}.{table['tableName']}:",
          "allowed" if decision["allow"] else "denied", "|", "; ".join(decision["reasons"]))
