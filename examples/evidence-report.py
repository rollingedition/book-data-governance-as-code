import json
from datetime import timedelta

from lib import audit, retention
from lib.platform import scratch

# The platform on Oct 1: every table and pipeline output, chapter 7's erasure of learner 3590 at 07:00, then the day's
# queries through the audited layer.
catalog, graph = retention.open_platform(statistics_off=True)
log = audit.Log(scratch() / "audit.jsonl")
_, found, done = retention.erase_everywhere(catalog, graph, 3590, "erasure-0001", min_age=timedelta(0))
audit.record_erasure(log, "erasure-0001", done, retention.scan(catalog, found), at=audit.MIDNIGHT.replace(hour=7))
platform = audit.Audited(catalog, log)
audit.run_day(platform)

# The first report, so there's no head from an earlier one to check the chain against.
report = audit.evidence(platform, catalog, anchor=None)
reports = scratch()          # in production, another system, which the log's writer can't change
(reports / "evidence-2026-10-01.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
result = {name: section["result"] for name, section in report.items()}
print("sections:", ", ".join(report))
print("policy tests passed:", f"{result['policy tests']['passed']} of {result['policy tests']['of']}")
print("who-sees-what cases the policy agrees with:", f"{result['who sees what']['agree']} of {result['who sees what']['of']}")
for problem in result["classification coverage"]["problems"]:
    print("classification problem:", problem)
print("audit log records:", result["audit log"]["records"])
print("chain problems, checked alone:", len(result["audit log"]["chain problems"]))
print("queries DuckDB ran with no decision record:", len(result["engine cross-check"]["unrecorded"]))
print("decision records DuckDB has no query for:", len(result["engine cross-check"]["not in the engine log"]))
for erasure in result["erasures"]:
    print(f"erasure {erasure['request']}: {erasure['rows_deleted']} rows deleted, "
          f"{erasure['files_holding_the_person']} of {erasure['files_read']} files hold the person")
    for kept in erasure["kept"]:
        print(f"erasure {erasure['request']}, kept under an exception: {kept['table']}, decided by {kept['decided_by']},",
              f"{', '.join(kept['columns_erased'])} erased")
print("chain head written down, at record:", report["audit log"]["inputs"]["head"]["records"])
print("engine cross-check, computed again from:", "; ".join(report["engine cross-check"]["computed again from"]))
print("engine cross-check, not computed again:", report["engine cross-check"]["not computed again"])
