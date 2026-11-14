"""Carries out the erasure plan with Iceberg row deletes. The current snapshots no longer hold the learner, but the snapshots
before them still do, and the `read by time travel` lines show it."""
from lib import retention

LEARNER = 3590
catalog, graph = retention.open_platform()
steps = retention.plan(catalog, graph, LEARNER)
# each table's snapshot from before the erasure, which the time-travel lines read
before = {step.table: catalog.load_table(step.table).current_snapshot().snapshot_id for step in steps}

done = retention.erase(catalog, steps, "erasure-0001")       # the request's ID goes into every commit's summary
for table, rows in done.items():
    # a rebuilt table reports 'rebuilt' rather than a count of rows
    print(f"{table}, rows deleted:" if rows != "rebuilt" else f"{table}:", rows)
print("tables one row was deleted from:", ", ".join(table for table, rows in done.items() if rows == 1))
theirs = retention.rows_of([LEARNER])
payments = catalog.load_table("billing.payments").scan(row_filter=theirs).to_arrow()
print("billing.payments, the learner's rows kept:", payments.num_rows)
# the exception erases card_number to null, so the values that are not null are the card numbers left
print("billing.payments, card numbers left in them:", payments.num_rows - payments["card_number"].null_count)
for name in ("crm.learners", "learning.events"):
    table = catalog.load_table(name)
    print(f"the learner's rows in {name}, read now:", retention.count(table, theirs))
    print(f"the learner's rows in {name}, read by time travel:", retention.count(table, theirs, before[name]))

summary = catalog.load_table("crm.learners").current_snapshot().summary
# copy-on-write writes the rows it keeps again as new records, so this counts every other row of the file
print("crm.learners, rows written again to delete one:", f"{int(summary['added-records']):,}")
print("crm.learners, the commit records:", summary["erasure-request"])
# snapshots of tables the plan deleted from (not rebuilt, not kept) that still return a row the erasure deleted
holding = [(table, snapshot) for step in steps if step.column is not None
           for table in [catalog.load_table(step.table)] for snapshot in table.snapshots()
           if not step.kept and retention.count(table, retention.rows(step), snapshot.snapshot_id)]
print("snapshots that still hold the rows the erasure deleted:", len(holding))
print("of them current:", sum(snapshot == table.current_snapshot() for table, snapshot in holding))
