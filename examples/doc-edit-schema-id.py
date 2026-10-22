from lib import catalog, metadata

platform = catalog.open_catalog(["learning.reviews"])
metadata.apply(platform, metadata.load())
reviews = platform.load_table("learning.reviews")
before = reviews.schema()
print("schema ID before the edit:", before.schema_id)

with reviews.update_schema() as update:                                        # change one description, nothing else
    update.update_column("body", doc="The review's text, as the learner wrote it. May hold names and emails.")
after = reviews.schema()
print("schema ID after the edit:", after.schema_id)
print("schemas the table keeps:", len(reviews.metadata.schemas))
print("schema ID changed:", before.schema_id != after.schema_id)
print("field IDs, names or types changed:", metadata.shape(before) != metadata.shape(after))
