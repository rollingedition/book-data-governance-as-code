from lib import catalog, metadata

platform = catalog.open_catalog()
described = metadata.load()
print("crm.learners schema ID before:", platform.load_table("crm.learners").schema().schema_id)
print("tables changed:", len(metadata.apply(platform, described)))

learners = platform.load_table("crm.learners")
print("crm.learners schema ID after:", learners.schema().schema_id)
print("email's doc, in the Iceberg schema:", learners.schema().find_field("email").doc)
scanned = learners.scan(selected_fields=("email",), limit=1).to_arrow()
print("email's doc, in a scan's Arrow schema:", scanned.schema.field("email").metadata[b"doc"].decode())
print("crm.learners properties:", ", ".join(f"{k}={v}" for k, v in sorted(learners.properties.items())))
print("tables changed by applying again:", len(metadata.apply(platform, described)))
