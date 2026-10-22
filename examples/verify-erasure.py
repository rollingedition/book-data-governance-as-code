"""The whole erasure job on the platform with statistics off for protected columns, ending with a scan of every file
under the warehouse. What it prints is the evidence: IDs and counts, never the learner's values."""
from datetime import timedelta

from lib import retention

LEARNER, REQUEST = 3590, "erasure-0001"
catalog, graph = retention.open_platform(statistics_off=True)
steps, found, done = retention.erase_everywhere(catalog, graph, LEARNER, REQUEST, min_age=timedelta(0))

report = retention.scan(catalog, found)
for table, (files, holding) in report.items():
    print(f"{table}: {files} files read, {holding} holding the learner")

print("request:", REQUEST)
print("tables visited:", len(steps))
print("rows deleted:", sum(rows for rows in done.values() if rows != "rebuilt"))
print("tables rebuilt:", ", ".join(table for table, rows in done.items() if rows == "rebuilt"))
print("tables kept under an exception, columns erased:",
      "; ".join(f"{step.table}, {', '.join(step.cleared)}" for step in steps if step.kept))
print("snapshots a reader can still time travel to:",
      sum(len(catalog.load_table(step.table).snapshots()) - 1 for step in steps))
print("files no metadata references:",
      sum(len(retention.unreferenced(catalog.load_table(name))) for name in retention.tables(catalog)))
print("tables missing their generated properties:", ", ".join(retention.missing_properties(catalog)))
print("files read:", sum(files for files, _ in report.values()))
print("files holding the learner:", sum(holding for _, holding in report.values()))
