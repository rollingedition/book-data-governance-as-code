from lib import policy
from lib.catalog import open_catalog
from lib.enforce import Enforcer, agrees, observe

# A pull request changes one user in governance/users.toml, in two lines: analyst-1 joins support, with the region
# support's row filter needs, EMEA, to help with tickets.
data = policy.data_document()
data["platform"]["users"]["analyst-1"] = {"groups": ["data-science", "support"], "region": "emea"}
platform = Enforcer(open_catalog(["crm.learners", "learning.reviews", "billing.payments"]),
                    rego=policy.interpreter(data=data))

# The who-sees-what cases, run as queries with the change in place. The cases are unchanged: they say what each
# user should get, and the change was meant to touch support tickets only.
failing = 0
for case in policy.WHO_SEES_WHAT:
    got = observe(platform, case.user, case.table, case.columns, case.purpose)
    if not agrees(case, got):
        failing += 1
        who = case.user + (f" for {case.purpose}" if case.purpose else "")
        print(f"fails, {who}, {case.table}, rows: {got['rows']:,} of {got['stored']:,}")
        print(f"fails, {who}, {case.table}, columns changed: {', '.join(got['changed']) or 'none'}, "
              f"expected: {', '.join(case.masked)}")
print("cases that fail:", f"{failing} of {len(policy.WHO_SEES_WHAT)}")

# Chapter 5's table, which checks the decisions themselves, on the same change.
decisions = [policy.decide(policy.request(c.user, c.table, c.columns, c.purpose), platform.rego) for c in policy.WHO_SEES_WHAT]
print("cases that fail as decisions:", f"{sum(not policy.matches(c, d) for c, d in zip(policy.WHO_SEES_WHAT, decisions))} of {len(decisions)}")
