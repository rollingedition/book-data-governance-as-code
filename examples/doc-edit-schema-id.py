"""Changes one column's description after the metadata is applied, then compares schema IDs with field IDs, names and
types. The prose's point rests on the schema ID changing while the shape stays the same, which the assert checks."""
from lib import catalog, metadata

platform = catalog.open_catalog(["learning.reviews"])
metadata.apply(platform, metadata.load())    # the platform's descriptions go in first, so the edit below is the only change
reviews = platform.load_table("learning.reviews")
before = reviews.schema()
print("schema ID before the edit:", before.schema_id)

with reviews.update_schema() as update:                                        # change one description, nothing else
    update.update_column("body", doc="The review's text, as the learner wrote it. May hold names and emails.")
after = reviews.schema()
assert metadata.shape(before) == metadata.shape(after), "a doc edit changed a field ID, name or type"
print("schema ID after the edit:", after.schema_id)
print("schemas the table keeps:", len(reviews.metadata.schemas))
print("schema ID changed:", before.schema_id != after.schema_id)
print("field IDs, names or types changed:", metadata.shape(before) != metadata.shape(after))
