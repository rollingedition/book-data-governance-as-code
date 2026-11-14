"""A pull request from the data-science team lowers crm.learners.city from personal to internal. The prose's point rests
on analyst-1's mask on each side, and on the owner rule failing alone."""
from lib import ci, policy

main = ci.checkout()
branch = ci.checkout(main)
ci.edit(branch, "governance/metadata.toml", '6 = { name = "city", classification = "personal"',
        '6 = { name = "city", classification = "internal"')
before, after = ci.build(main), ci.build(branch)

reading_city = policy.request("analyst-1", "crm.learners", ["learner_id", "city"])
for side, name in ((before, "main"), (after, "the branch")):
    rego = policy.interpreter(side.governance / "policies", data=ci.policy_data(side))
    # a column with no mask in the decision is shown as it is stored
    print(f"analyst-1 reading city, on {name}:", policy.decide(reading_city, rego)["column_masks"].get("city", "unmasked"))

checks = ["policy-tests", "enforcement-tests", "removals", "owner-review"]
report = ci.run(before, after, checks=checks)
print("schema changes:", len(report.changes))
for line in ci.lines(report):
    print(line)

# The approvals come from the pull request's reviews: one entry per approving review, named by the reviewer's team.
# The owner rule passes when it raises no unapproved finding.
owner_review = lambda approvals: "passed" if ci.run(before, after, approvals, checks=["owner-review"]).passed else "failed"
print("owner-review, once team-crm approves:", owner_review(["team-crm"]))
print("owner-review, once team-analytics approves:", owner_review(["team-analytics"]))
ci.edit(after.root, "governance/ci.toml", "teams = {}", "teams = { team-crm = 2 }")     # team-crm asks for two
# one approval from team-crm and one from another team: team-crm still needs a second of its own
print("owner-review, two required, one from team-crm:", owner_review(["team-crm", "team-platform"]))
print("owner-review, two required, two from team-crm:", owner_review(["team-crm", "team-crm"]))
