from pyiceberg.types import StringType

from lib import catalog, metadata

platform = catalog.open_catalog(["crm.learners"])
described = metadata.load()
learners = platform.load_table("crm.learners")
written = learners.current_snapshot()                                        # the snapshot the rows were written in
print("country field ID, before it was dropped:", learners.schema().find_field("country").field_id)

with learners.update_schema() as update:                                     # a column nobody has reviewed
    update.add_column("preferred_name", StringType(), doc="What the learner asks to be called.")
with learners.update_schema() as update:                                     # drop a column, then add it back
    update.delete_column("country")
with learners.update_schema() as update:
    update.add_column("country", StringType())

country = learners.schema().find_field("country")
print("country field ID, after it was added back:", country.field_id)
values = learners.scan(selected_fields=("country",)).to_arrow().column("country")
print("country values after it was added back:", f"{values.null_count:,} nulls of {len(values):,}")
print("snapshots the schema changes wrote:", len(learners.metadata.snapshots) - 1)
old = learners.scan(snapshot_id=written.snapshot_id, selected_fields=("country",)).to_arrow().column("country")
print("country values, read by time travel to that snapshot:", f"{len(old) - old.null_count:,} of {len(old):,}")
for problem in metadata.problems(platform, described):
    print("problems():", problem)
for problem in metadata.dropped(platform, described):
    print("dropped():", problem)
