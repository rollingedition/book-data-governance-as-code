"""A governance inventory kept as a spreadsheet, written before a schema change, against the check on the live schema."""
import csv
import io

from lib import ci, metadata
from lib.catalog import open_catalog

platform = open_catalog(["crm.learners"])
described = metadata.load()

# The inventory: one row per column of crm.learners, exported on the day someone was asked for one.
inventory = io.StringIO()
writer = csv.writer(inventory)
writer.writerow(["table", "column", "classification"])
for field in platform.load_table("crm.learners").schema().fields:
    writer.writerow(["crm.learners", field.name, described["crm.learners"].columns[field.field_id].classification])

# Later, the CRM team's migration adds a column for learners' work email.
ci.migrate(platform, "alter table crm.learners add column work_email varchar;")

rows = list(csv.DictReader(io.StringIO(inventory.getvalue())))
print("columns the spreadsheet lists:", len(rows))
print("of them without a classification, by the spreadsheet:", sum(not row["classification"] for row in rows))
live = [field.name for field in platform.load_table("crm.learners").schema().fields]
print("columns crm.learners has:", len(live))
print("columns the spreadsheet doesn't list:", ", ".join(sorted(set(live) - {row["column"] for row in rows})))
for problem in metadata.problems(platform, described):
    print("the check against the catalog finds:", problem)
