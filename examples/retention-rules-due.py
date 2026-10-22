"""Which rows the platform's retention rules no longer keep, counted against a day the job is given, never the clock."""
from datetime import timedelta

from lib import retention
from lib.catalog import open_catalog
from lib.platform import TODAY

rules = retention.rules()["tables"]
catalog = open_catalog(sorted(rules))
for name, rule in sorted(rules.items()):
    state = rule.get("only_when")
    print(f"{name}: kept {rule['keep_days']} days from {rule['counted_from']}"
          + (f", once {state['column']} is {state['equals']}" if state else ""))

for day in (TODAY, TODAY + timedelta(days=90)):
    for name, rule in sorted(rules.items()):
        table = catalog.load_table(name)
        print(f"{name} on {day}, kept from:", retention.cutoff(rule, day))
        print(f"{name} on {day}, rows due:", f"{retention.count(table, retention.due(rule, day)):,}")
        print(f"{name} on {day}, rows in the table:", f"{retention.count(table):,}")
