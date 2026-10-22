"""The platform's governance job on one pull request: the CRM team adds a work email to crm.learners."""

from lib import ci
from lib.platform import scratch

main = ci.checkout()                      # the main branch: governance files, pipelines and the files generated from them
branch = ci.checkout(main)                # the pull request's branch, starting from main
(branch / "migrations" / "0001-add-work-email.sql").write_text("alter table crm.learners add column work_email varchar;\n")
print("files the pull request changes:", ", ".join(ci.changed(main, branch)))

# What a CI step runs, `python -m lib.ci MAIN BRANCH --summary FILE`: it prints the report and its totals, writes the
# report as Markdown for the pull request, and exits with the code it returns.
summary = scratch() / "summary.md"
code = ci.main([str(main), str(branch), "--summary", str(summary)])
print("the step exits with:", code)
print("the summary it writes for the pull request:")
print(summary.read_text(encoding="utf-8"), end="")
