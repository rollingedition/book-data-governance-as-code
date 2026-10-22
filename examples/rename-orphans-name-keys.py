from lib import catalog, metadata, policy

platform = catalog.open_catalog(["crm.learners"])
described = metadata.load()
by_id = described["crm.learners"].columns                                    # the book's metadata: keyed by field ID
by_name = {column.name: column.classification for column in by_id.values()}  # a spreadsheet's: keyed by name

learners = platform.load_table("crm.learners")
with learners.update_schema() as update:
    update.rename_column("email", "contact_email")
field = learners.schema().find_field("contact_email")
print("contact_email field ID:", field.field_id)
values = learners.scan(selected_fields=("contact_email",)).to_arrow().column("contact_email")
print("values read under the new name:", f"{len(values) - values.null_count:,} of {len(values):,}")

print("classification of contact_email, looked up by name:", by_name.get("contact_email"))
print("classification of contact_email, looked up by field ID:", by_id[field.field_id].classification)
names = {f.name for f in learners.schema().fields}
print("name-keyed entries that match no column:", ", ".join(sorted(set(by_name) - names)))
for problem in metadata.problems(platform, described):
    print("check:", problem)
# The policies read classifications by name, generated from the file, which still says email.
decision = policy.decide(policy.request("agent-a01", "crm.learners", ["contact_email"]))
print("a support agent reading contact_email sees it as:", decision["column_masks"].get("contact_email", "shown"))
