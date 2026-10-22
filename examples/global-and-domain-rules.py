"""The platform's global rules and the crm domain's own rule, run on every contract of the CRM team's pull request,
which this time classifies the new work_email column: as public, with no description. Then the same column described,
with crm.learners moved to the analytics domain, which has no rules of its own."""
from lib import ci

main = ci.checkout()
branch = ci.checkout(main)
(branch / "migrations" / "0001-add-work-email.sql").write_text("alter table crm.learners add column work_email varchar;\n")
ci.edit(branch, "governance/metadata.toml", '15 = { name = "notes"',
        '16 = { name = "work_email", classification = "public", description = "" }\n15 = { name = "notes"')

for path in sorted((branch / "governance" / "ci").rglob("*.rego")):
    print("rules:", path.relative_to(branch).as_posix())
before = ci.build(main)
report = ci.run(before, branch, checks=["coverage", "contract-rules"])
for line in ci.lines(report):
    print(line)

# The second try: a description, and the table moved to a domain whose rules allow public columns.
ci.edit(branch, "governance/metadata.toml", 'description = "" }', 'description = "The learner\'s work address." }')
ci.edit(branch, "governance/metadata.toml", 'owner = "team-crm"\ndomain = "crm"\ndescription = "Every learner',
        'owner = "team-crm"\ndomain = "analytics"\ndescription = "Every learner')
report = ci.run(before, branch, checks=["coverage", "contract-rules", "owner-review"])
for line in ci.lines(report):
    print("moved to analytics,", line)
