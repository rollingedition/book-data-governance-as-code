"""The steps that erase one learner: the tables with their learner_id column, then the tables lineage says their rows reach,
with the exception the legal team recorded. The counts show how many steps each route found; the last two lines are the text the scan searches for and the rows that name the learner."""
from lib import pii, retention

LEARNER = 3590
catalog, graph = retention.open_platform()           # weeks after the learner withdrew marketing consent
steps = retention.plan(catalog, graph, LEARNER)
for step in steps:
    # no column is copied from the learner here: the table's own job runs again once its sources are erased
    if step.column is None:
        print(f"{step.table}: run {retention.job_for(step.table)} again")
    elif step.kept:
        print(f"{step.table}: keep, under an exception, and erase {', '.join(step.cleared)}")
    else:
        print(f"{step.table}: delete where {step.column} in {list(step.values)}"
              + (f", a copy of {step.via}" if step.via else ""))

print("tables in the plan:", len(steps))
# a step with a column and no lineage edge came from the table's own learner_id column
print("tables found by their learner_id column:", sum(1 for step in steps if step.column and not step.via))
# found by lineage: a copy of another table's ID, or a rebuild, because the learner's rows only shaped the table
print("tables found by lineage:", sum(1 for step in steps if step.via or step.column is None))
for step in (step for step in steps if step.kept):
    print(f"the reason recorded for {step.table}:", step.kept)
theirs = retention.rows_of([LEARNER])
for name in ("billing.payments", "marketing.audience"):
    print(f"the learner's rows in {name}:", retention.count(catalog.load_table(name), theirs))
# the values the scan will search for, noted before any row is deleted
found = retention.traces(catalog, steps, LEARNER)
# The phone number is printed through lib.pii.masked, which keeps its shape and its last four digits.
print("the learner's identifying values, as the scan searches for them:", pii.masked(", ".join(found.texts)))
# rows the plan keeps, in any string column, whose text holds one of those values
named = retention.mentions(catalog, steps, found)
print("rows the plan doesn't delete whose text names the learner:", sum(named.values()))
