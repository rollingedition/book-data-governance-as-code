from pyiceberg.types import StringType

from lib import metadata, pii
from lib.catalog import fill_column, open_catalog
from lib.platform import load
from lib.warehouse import connect

catalog = open_catalog(["support.tickets"])
tickets = catalog.load_table("support.tickets")
reviewed = metadata.load()["support.tickets"].columns           # field ID -> the column as people reviewed it
unclassified = lambda: {f.field_id for f in tickets.schema().fields if f.field_id not in reviewed}
print("schema ID at the start:", tickets.schema().schema_id)

with tickets.update_schema() as update:                         # a doc edit: a new schema, no new column
    update.update_column("body", doc="What the learner wrote, as they wrote it.")
print("schema ID after a doc edit:", tickets.schema().schema_id)
print("fields to classify after the doc edit:", len(unclassified()))

# The support team starts storing the number to call back for tickets opened by phone: the learner's phone number.
with tickets.update_schema() as update:
    update.add_column("callback_number", StringType(), doc="Where to call the learner back.")
phone_of = {row["learner_id"]: row["phone"] for row in load("crm.learners").to_pylist()}
fill_column(tickets, "callback_number", lambda row: phone_of[row["learner_id"]] if row["channel"] == "phone" else None)
print("schema ID after adding a column:", tickets.schema().schema_id)
print("fields to classify after adding a column:", len(unclassified()))

for f in pii.classify(catalog, connect(catalog), pii.platform_detector(), only={"support.tickets": unclassified()}):
    found = ", ".join(f"{entity} in {n} of {f.sampled}" for entity, n in f.found.items())
    print(f"{f.table}.{f.column}, field ID: {f.field_id}")
    print(f"{f.table}.{f.column}, entity its name suggests: {f.named}")
    print(f"{f.table}.{f.column}, found in its sampled values: {found}")
    print(f"{f.table}.{f.column}, proposed classification: {f.classification}")
