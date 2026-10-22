"""The support team's pull request adds two columns to support.tickets. Coverage judges them by their names; chapter
3's classification of the table's values, added to the job, reads a sample or, the slow version, every row. Counts of
values read stand in for time, which depends on the machine."""
import pyarrow as pa

from lib import ci, pii

main = ci.checkout()
branch = ci.checkout(main)
(branch / "migrations" / "0001-callback-and-reply.sql").write_text(
    "alter table support.tickets add column callback_number varchar;\n"
    "alter table support.tickets add column reply_email varchar;\n")
ci.edit(branch, "governance/metadata.toml", '8 = { name = "status"',
        '9 = { name = "callback_number", classification = "internal", description = "A number to call the learner back on." }\n'
        '10 = { name = "reply_email", classification = "public", description = "Where the reply goes." }\n8 = { name = "status"')
before, after = ci.build(main), ci.build(branch)

report = ci.run(before, after, checks=["coverage", "classify-sampled", "classify-every-row"])
for name in ("body", "callback_number", "reply_email"):
    values = after.catalog.load_table("support.tickets").scan(selected_fields=(name,)).to_arrow()[name]
    print(f"support.tickets.{name}, values on the branch:", f"{len(values) - values.null_count:,}")
    for check in report.checks:
        found = [f.message for f in check.findings if f.id == f"{check.name}/support.tickets.{name}"]
        print(f"support.tickets.{name}, {check.name}:", found[0] if found else "no finding")
for check in report.checks[1:]:
    print(f"{check.name}, values read:", f"{check.checked:,}")

text_columns, sampled, every_row = 0, 0, 0
for name in sorted(before.described):
    data = before.catalog.load_table(name).scan().to_arrow()
    filled = [len(column) - column.null_count for column in data.columns if pa.types.is_string(column.type)]
    text_columns += len(filled)
    sampled += sum(min(pii.ROWS, count) for count in filled)
    every_row += sum(filled)
print("text columns in the described tables:", text_columns)
print("values the sampled check would read in them:", f"{sampled:,}")
print("values the every-row check would read in them:", f"{every_row:,}")
