import json

from lib import metadata, policy
from lib.enforce import row_filter

# The platform's policy, with a support agent added before anyone set their region (chapter 5's missing-region case).
data = policy.data_document()
data["platform"]["users"]["agent-new"] = {"groups": ["support"]}
rego = policy.interpreter(data=data)
described = metadata.load()


def trino_request(user: str, operation: str, resource: dict | None = None) -> dict:
    """A request in the shape Trino 484's OPA plugin sends (OpaAccessControl, OpaHighLevelClient)."""
    action = {"operation": operation} | ({"resource": resource} if resource else {})
    return {"context": {"identity": {"user": user, "groups": []}, "softwareStack": {"trinoVersion": "484"}}, "action": action}


# Trino asks `allow` about every operation: ExecuteQuery before any query runs, AccessCatalog for each catalog it names.
for operation, resource in [("ExecuteQuery", None), ("AccessCatalog", {"catalog": {"name": "platform"}})]:
    print(f"allow, {operation}, agent-a05:", policy.decide(trino_request("agent-a05", operation, resource), rego)["allow"])

# GetRowFilters names a table. The policy's row_filters rule on its own skips `allow` and its missing-attribute guard,
# so for agent-new it returns no filter, which Trino reads as no filtering. The answer comes from the decision instead,
# for the same read asked as SelectFromColumns: its filters in Trino's SQL, or a filter that keeps no rows.
for user, schema, table in [("agent-a05", "support", "tickets"), ("instructor-17", "learning", "reviews"),
                            ("agent-new", "support", "tickets")]:
    request = trino_request(user, "GetRowFilters", {"table": {"catalogName": "platform", "schemaName": schema, "tableName": table}})
    print(f"GetRowFilters, {user}, {schema}.{table}, the rule alone:",
          json.dumps(policy.evaluate(rego, "data.platform.access.row_filters", request)))
    decision = policy.decide(policy.request(user, f"{schema}.{table}", []), rego)
    columns = {c.name: "" for c in described[f"{schema}.{table}"].columns.values()}
    filters = [{"expression": row_filter(rule, columns, table=None).sql("trino")} for rule in decision["row_filters"]]
    print(f"GetRowFilters, {user}, {schema}.{table}, answered:", json.dumps(filters if decision["allow"] else [{"expression": "false"}]))

# GetColumnMask names one column, under action.resource.column. The policy reads the columns of a SelectFromColumns
# request instead, so it masks nothing here until the request is translated.
column = {"catalogName": "platform", "schemaName": "crm", "tableName": "learners", "columnName": "email", "columnType": "varchar"}
decision = policy.decide(trino_request("analyst-1", "GetColumnMask", {"column": column}), rego)
print("GetColumnMask as sent, analyst-1, crm.learners.email:", json.dumps(decision["column_masks"]), decision["reasons"][0])
translated = policy.request("analyst-1", "crm.learners", [column["columnName"]])
print("GetColumnMask translated, analyst-1, crm.learners.email:", json.dumps(policy.decide(translated, rego)["column_masks"]))
