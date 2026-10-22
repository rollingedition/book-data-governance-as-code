"""After the erasure, expire the old snapshots, then remove the files no snapshot references. PyIceberg's expiry
deletes no file, and it has no orphan-file removal, so lib.retention.sweep does it with PyIceberg's inspect tables."""
from datetime import timedelta

from lib import retention

LEARNER = 3590
catalog, graph = retention.open_platform()
steps = retention.plan(catalog, graph, LEARNER)
found = retention.traces(catalog, steps, LEARNER)
retention.erase(catalog, steps, "erasure-0001")
tables = [catalog.load_table(step.table) for step in steps]
print("snapshots expired:", sum(retention.expire(table) for table in tables))
print("files under the plan's tables:", sum(len(retention.files_under(table.location())) for table in tables))
print("files no metadata references:", sum(len(retention.unreferenced(table)) for table in tables))
print("files holding the learner, after expiry:", sum(held for _, held in retention.scan(catalog, found).values()))
print("files swept with the configured minimum age:", sum(retention.sweep(t, retention.min_file_age()) for t in tables))
# One writer, nothing in flight: no file is too young to remove, so the examples sweep with no minimum age.
print("files swept with no minimum age:", sum(retention.sweep(table, timedelta(0)) for table in tables))
print("files holding the learner, after the sweep:", sum(held for _, held in retention.scan(catalog, found).values()))

for table in tables:
    entries = table.inspect.entries()
    for status, metrics in zip(entries["status"].to_pylist(), entries["readable_metrics"].to_pylist()):
        bounds = metrics.get("email") or {}
        kept = (bounds.get("lower_bound"), bounds.get("upper_bound"))
        if any(text in bound.lower() for bound in kept if bound for text in found.texts):
            print(f"{'.'.join(table.name()[-2:])}, manifest entry with status {status}, email bounds:",
                  bounds["lower_bound"], "to", bounds["upper_bound"])
