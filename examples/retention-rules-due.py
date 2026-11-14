"""The rows each retention rule makes due on the book's today and 90 days later, counted against the day the job is given.
The `rows due` lines are the counts the prose gives."""
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

# the book's today, then 90 days later: the day is passed in, never read from the clock
for day in (TODAY, TODAY + timedelta(days=90)):
    for name, rule in sorted(rules.items()):
        table = catalog.load_table(name)
        # the earliest date kept: keep_days before the day
        print(f"{name} on {day}, kept from:", retention.cutoff(rule, day))
        print(f"{name} on {day}, rows due:", f"{retention.count(table, retention.due(rule, day)):,}")
        print(f"{name} on {day}, rows in the table:", f"{retention.count(table):,}")
