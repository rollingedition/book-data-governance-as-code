from lib import pipelines, policy
from lib.catalog import open_catalog
from lib.enforce import Enforcer, agrees, observe

catalog = open_catalog(["crm.learners", "learning.reviews", "billing.payments", "learning.enrollments", "learning.courses"])
pipelines.run(catalog, "marketing_audience")          # a copy of learners' names and emails, with no metadata yet
platform = Enforcer(catalog)

# Chapter 5's who-sees-what table, run as queries: what each user gets, against what's stored. A column is "changed"
# when at least one value the user gets differs from the stored one.
agreeing = 0
for case in policy.WHO_SEES_WHAT:
    got = observe(platform, case.user, case.table, case.columns, case.purpose)
    who = case.user + (f" for {case.purpose}" if case.purpose else "")
    if got is None:
        print(f"{who}, {case.table}: denied")
    else:
        print(f"{who}, {case.table}: {got['rows']:,} of {got['stored']:,} rows, changed: {', '.join(got['changed']) or 'none'}")
    agreeing += agrees(case, got)
print("cases where what the user gets agrees with the decision:", f"{agreeing} of {len(policy.WHO_SEES_WHAT)}")

# marketing.audience is a table marketing may read, but nobody has classified its columns, so the policy treats each
# one as sensitive: marketing gets every row, and no value in any column.
rows = platform.sql("select * from marketing.audience", "marketing-1").fetchall()
shown = sum(any(value is not None for value in column) for column in zip(*rows))
print("marketing-1, marketing.audience, which has no metadata, rows:", f"{len(rows):,}")
print("marketing-1, marketing.audience, which has no metadata, columns with a value:", f"{shown} of {len(rows[0])}")
