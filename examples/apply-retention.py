"""Apply the retention rules on the book's "today": Iceberg row deletes, which leave the old rows in older snapshots."""
from datetime import datetime, timedelta, timezone

from lib import retention
from lib.catalog import open_catalog
from lib.platform import TODAY

rules = retention.rules()["tables"]
catalog = open_catalog(sorted(rules))
before = {name: catalog.load_table(name).current_snapshot().snapshot_id for name in rules}

deleted = retention.apply(catalog, TODAY)
for name, rows in deleted.items():
    print(f"{name}, rows deleted:", f"{rows:,}")
print("rows deleted by running again for the same day:", sum(retention.apply(catalog, TODAY).values()))

for name in (name for name, rows in deleted.items() if rows):
    rule, table = rules[name], catalog.load_table(name)
    summary = table.current_snapshot().summary
    print(f"{name}, snapshots:", " then ".join(s.summary.operation.value for s in table.snapshots()))
    print(f"{name}, rows written again to delete the others:", f"{int(summary['added-records']):,}")
    print(f"{name}, the commit records:", summary["retention-rule"], "on", summary["retention-day"])
    rows_due = retention.due(rule, TODAY)
    print(f"{name}, rows due, read now:", f"{retention.count(table, rows_due):,}")
    print(f"{name}, rows due, read by time travel:", f"{retention.count(table, rows_due, before[name]):,}")
    # Snapshots carry real commit times, so their age is measured on the clock, by the table's own setting.
    print(f"{name}, snapshots expired by age today:", retention.expire_by_age(table))
    later = datetime.now(timezone.utc) + timedelta(days=6)
    print(f"{name}, snapshots expired by age six days later:", retention.expire_by_age(table, later))
