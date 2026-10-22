from lib import policy

rego = policy.interpreter()

# Each row of lib.policy.WHO_SEES_WHAT is a request and the decision a reviewer expects for it.
matching = 0
for case in policy.WHO_SEES_WHAT:
    decision = policy.decide(policy.request(case.user, case.table, case.columns, case.purpose), rego)
    matching += policy.matches(case, decision)
    who = case.user + (f" ({case.purpose})" if case.purpose else "")
    shown = "allowed" if decision["allow"] else "denied"
    filters = ", ".join(policy.filtered_on(decision)) or "-"
    masks = ", ".join(f"{column}: {mask}" for column, mask in decision["column_masks"].items()) or "-"
    print(f"{who:26} {case.table:17} {shown:8} rows: {filters:29} masks: {masks}")
print("rows of the table the policy matches:", f"{matching} of {len(policy.WHO_SEES_WHAT)}")
