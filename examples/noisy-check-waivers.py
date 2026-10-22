"""A noisy check added to the job: every column named like an ID is personal. The platform decided otherwise for IDs
used for joining, so each owning team waives its findings, and the waiver count climbs."""
from collections import Counter

from lib import ci

main = ci.checkout()
branch = ci.checkout(main)                          # the platform team adds the check to the job's settings
ci.edit(branch, "governance/ci.toml", '"properties"]', '"properties", "id-columns"]')
before, after = ci.build(main), ci.build(branch)

report = ci.run(before, after)
print("waivers in the file, before:", len(ci.waivers(after.governance)))
noisy = next(check for check in report.checks if check.name == "id-columns")
print("job, before the waivers:", "passed" if report.passed else "failed")
print("id-columns, findings:", len(noisy.findings))
print("id-columns, findings on main:", len(noisy.on_main))
classified = Counter(finding.message.rpartition(" ")[2] for finding in noisy.findings)
for level, count in sorted(classified.items()):
    print(f"id-columns, findings classified {level}:", count)
print("id-columns, the public one:", ", ".join(f.id.split("/")[1] for f in noisy.findings if f.message.endswith("public")))

with open(after.governance / "waivers.toml", "a", encoding="utf-8") as waivers:
    for finding in noisy.findings:
        waivers.write(f'\n[[waivers]]\nfinding = "{finding.id}"\nowner = "{finding.owner}"\n'
                      'reason = "An ID for joining, internal by decision."\nexpires = 2026-10-31\n')

report = ci.run(before, after)
print("waivers in the file, after:", len(ci.waivers(after.governance)))
print("job, with the waivers:", "passed" if report.passed else "failed")
for check in (check for check in report.checks if check.findings):
    idle = [f for f in check.findings if f.waiver or f.on_main]          # findings nobody acted on
    print(f"{check.name}, not acted on:", f"{len(idle)} of {len(check.findings)} findings, {len(idle) / len(check.findings):.0%}: "
          f"{len(check.waived)} waived, {len(check.on_main)} on main")
