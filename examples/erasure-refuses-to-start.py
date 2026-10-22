"""The erasure job checks every table in its plan before it changes any. When a table's own settings would keep the
learner, it refuses to start and names the tables; the scan of every file still runs, and finds the learner."""
from datetime import timedelta

from lib import retention

LEARNER = 3590
catalog, graph = retention.open_platform()
for name, settings in (("crm.learners", {"gc.enabled": "false"}),
                       ("learning.events", {"history.expire.min-snapshots-to-keep": "3"})):
    with catalog.load_table(name).transaction() as transaction:
        transaction.set_properties(settings)
before = {name: catalog.load_table(name).current_snapshot().snapshot_id for name in retention.tables(catalog)}

try:
    steps, found, done = retention.erase_everywhere(catalog, graph, LEARNER, "erasure-0001", min_age=timedelta(0))
except retention.ErasureRefused as refusal:
    for name, why in refusal.tables.items():
        print(f"the job refuses, because of {name}:", why)
    found = refusal.found
print("tables the job changed:", sum(catalog.load_table(name).current_snapshot().snapshot_id != snapshot
                                     for name, snapshot in before.items()))
report = retention.scan(catalog, found)
print("tables whose files hold the learner:", sum(1 for _, holding in report.values() if holding))
print("files holding the learner:", sum(holding for _, holding in report.values()))
