"""The CRM team's pull request adds a column to crm.learners and no metadata for it. One check runs on it: coverage."""
from lib import ci

main = ci.checkout()                      # the main branch: the governance files and pipelines, and no migrations yet
branch = ci.checkout(main)                # the pull request's branch, starting from main
(branch / "migrations" / "0001-add-work-email.sql").write_text("alter table crm.learners add column work_email varchar;\n")
print("files the pull request changes:", ", ".join(ci.changed(main, branch)))

# Build a scratch platform from each side, run the coverage check on both, and compare their findings by ID.
report = ci.run(main, branch, checks=["coverage"])
for line in ci.lines(report):
    if not line.startswith("change "):    # schema changes are chapter 10's report
        print(line)
for line in ci.totals(report):
    if line.startswith(("findings the pull request introduces", "findings already on main", "exit code")):
        print(line)
