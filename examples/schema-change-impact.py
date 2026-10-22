"""What the learning team's rename of learning.courses.category reaches: Iceberg readers, SQL and lineage. Then the same
rename merged together with the fix of both pipelines that name the column."""
import pyarrow.compute as pc

from lib import ci, retention

main = ci.checkout()
branch = ci.checkout(main)
(branch / "migrations" / "0001-rename-category.sql").write_text(
    "alter table learning.courses rename column category to subject;\n")
ci.edit(branch, "governance/metadata.toml", '3 = { name = "category", classification = "public"',
        '3 = { name = "subject", classification = "public"')       # the same field ID, renamed

before, after = ci.build(main), ci.build(branch)
change = next(c for c in ci.changes(before, after) if c.table == "learning.courses")
print("change:", change.kind, "from", change.before, "to", change.after)
print("field ID, before and after:", change.field_id)
subjects = after.catalog.load_table("learning.courses").scan(selected_fields=("subject",)).to_arrow()
print("courses whose subject an Iceberg reader finds:", pc.count(subjects["subject"]).as_py())
print("pipelines whose SQL names it:", ", ".join(change.jobs))
for job, why in sorted(after.failed.items()):
    print(f"pipeline {job}, on the branch:", why)
print("columns whose values come from it:", len(change.values))
for column in change.values:
    print("  value of:", column)
print("columns it can change, DIRECT and INDIRECT:", len(change.reached))
print("tables those columns are in:", ", ".join(sorted({".".join(c.split(".")[:2]) for c in change.reached})))
print("owners the report goes to:", ", ".join(change.owners))
print("tables on main:", len(retention.tables(before.catalog)))
print("tables on the branch:", len(retention.tables(after.catalog)))
coverage = ci.run(before, after, checks=["coverage"]).checks[0]
print("coverage findings on the branch:", ", ".join(finding.id for finding in coverage.findings))

# The same pull request, with both pipelines changed to read the new name. Each side runs its own pipelines/.
ci.edit(branch, "pipelines/course_performance.sql", "    c.category,", "    c.subject as category,")
ci.edit(branch, "pipelines/course_performance.sql", "c.title, c.category", "c.title, c.subject")
sql = (branch / "pipelines" / "marketing_audience.sql").read_text(encoding="utf-8")
(branch / "pipelines" / "marketing_audience.sql").write_text(sql.replace("c.category", "c.subject").replace(
    "f.category", "f.subject"), encoding="utf-8")
fixed = ci.build(branch)
ci.generate(fixed)                        # and the contracts generated again, as the author's hook would
print("files the fixed pull request changes:", ", ".join(ci.changed(main, branch)))
for line in ci.lines(ci.run(before, fixed, checks=["pipelines", "coverage", "contracts", "lineage-classifications"])):
    print("with the fix,", line)
