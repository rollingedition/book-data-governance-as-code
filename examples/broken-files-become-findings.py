"""A pull request that breaks two files the job reads: a support agent added to the users file with a quote missing,
and a migration Iceberg refuses. It also carries the CRM team's unclassified work_email column. The prose's point rests
on the two checks that could not run and on the exit code."""
from lib import ci

main = ci.checkout()
branch = ci.checkout(main)
# the closing quote after "support" is missing, so the users file no longer parses
ci.edit(branch, "governance/users.toml", '[users.agent-a09]', '[users.agent-a12]\ngroups = ["support]\nregion = "emea"\n\n'
        '[users.agent-a09]')
(branch / "migrations" / "0001-add-work-email.sql").write_text("alter table crm.learners add column work_email varchar;\n")
# a string column cannot become an int: that isn't one of Iceberg's type promotions, so the migration is refused
(branch / "migrations" / "0002-country-as-number.sql").write_text("alter table crm.learners alter column country type int;\n")
print("files the pull request changes:", ", ".join(ci.changed(main, branch)))

report = ci.run(main, branch)
for line in ci.lines(report):
    print(line)
print("checks that could not run:", ", ".join(check.name for check in report.checks if check.error))
print("checks that ran:", sum(check.error is None for check in report.checks))
print("exit code:", report.exit_code)
