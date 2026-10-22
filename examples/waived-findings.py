"""Waivers on the CRM team's pull request: one for the unclassified work_email column, which coverage never allows;
then, with the column classified but not yet described, one that asks for too long, and one that fits, on the book's
today and a month later."""
from datetime import date

from lib import ci
from lib.platform import TODAY


def waive(branch, finding, expires, reason):
    with open(branch / "governance" / "waivers.toml", "a", encoding="utf-8") as waivers:
        waivers.write(f'\n[[waivers]]\nfinding = "{finding}"\nowner = "team-crm"\nreason = "{reason}"\nexpires = {expires}\n')


main = ci.checkout()
before = ci.build(main)
unclassified = ci.checkout(main)
(unclassified / "migrations" / "0001-add-work-email.sql").write_text("alter table crm.learners add column work_email varchar;\n")
waive(unclassified, "coverage/crm.learners.work_email", "2026-10-31", "Classified once the sign-up form is live.")
for line in ci.lines(ci.run(before, unclassified, checks=["coverage"])):
    print("unclassified,", line)

undescribed = ci.checkout(main)          # the second try: classified, with its description still to come
(undescribed / "migrations" / "0001-add-work-email.sql").write_text("alter table crm.learners add column work_email varchar;\n")
ci.edit(undescribed, "governance/metadata.toml", '15 = { name = "notes"',
        '16 = { name = "work_email", classification = "personal", description = "" }\n15 = { name = "notes"')
waive(undescribed, "contract-rules/crm.learners.work_email/described", "2027-06-30", "Described once the form's copy is final.")
after = ci.build(undescribed)
for line in ci.lines(ci.run(before, after, checks=["coverage", "contract-rules"])):
    print("undescribed, asked until 2027-06-30,", line)

ci.edit(undescribed, "governance/waivers.toml", "2027-06-30", "2026-10-31")
for day in (TODAY, date(2026, 11, 1)):
    report = ci.run(before, after, today=day, checks=["coverage", "contract-rules"])
    print(f"undescribed, asked until 2026-10-31, on {day}, contract-rules:", "passed" if report.checks[1].passed else "failed")
    print(f"undescribed, asked until 2026-10-31, on {day}, findings waived:", sum(len(c.waived) for c in report.checks))
    print(f"undescribed, asked until 2026-10-31, on {day}, waivers that silence nothing:",
          "; ".join(why for _, why in report.unused) or "none")
