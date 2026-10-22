"""team-billing removes the legacy card_number column from billing.payments: once straight away, and once after a pull
request that deprecates it has merged."""
import yaml

from lib import ci

entry = ('8 = { name = "card_number", classification = "sensitive", '
         'description = "The full card number, from a legacy export. Should not be stored at all." }\n')
deprecated_entry = entry.replace("all.", "all. Deprecated: reconcile payments by payment_id.").replace(" }", ", deprecated = true }")


def remove_card_number(base, line):
    """A pull request on top of `base` that drops the column and deletes its metadata entry."""
    branch = ci.checkout(base)
    (branch / "migrations" / "0001-drop-card-number.sql").write_text("alter table billing.payments drop column card_number;\n")
    ci.edit(branch, "governance/metadata.toml", line, "")
    return branch


main = ci.checkout()
deprecated = ci.checkout(main)            # the first pull request: mark the column deprecated, and say what replaces it
ci.edit(deprecated, "governance/metadata.toml", entry, deprecated_entry)

for base, line, name in ((main, entry, "straight away"), (deprecated, deprecated_entry, "after the deprecation merged")):
    report = ci.run(ci.build(base), ci.build(remove_card_number(base, line)), checks=["removals"])
    print(f"removal, {name}:", "passed" if report.checks[0].passed else "failed")
    for finding in report.checks[0].findings:
        print(f"  {finding.id}:", finding.message)

card_number = ci.contract(ci.build(deprecated), "billing.payments")["schema"][0]["properties"][7]
print("the deprecated column in its ODCS contract:")
print(yaml.safe_dump(card_number, sort_keys=False, width=200), end="")
