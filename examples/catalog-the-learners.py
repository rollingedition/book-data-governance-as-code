"""Catalog crm.learners: its owner, domain and classifications come from governance/metadata.toml, the reviewed file."""
from collections import Counter

from lib import metadata
from lib.catalog import open_catalog

platform = open_catalog(["crm.learners"])            # a fresh Iceberg catalog holding the learners table
described = metadata.load()
learners = described["crm.learners"]
print("owner:", learners.owner)
print("domain:", learners.domain)
counts = Counter(column.classification for column in learners.columns.values())
print("columns classified personal:", counts["personal"])
print("personal columns:", ", ".join(c.name for c in learners.columns.values() if c.classification == "personal"))
print("columns classified internal:", counts["internal"])

# Write the descriptions into the table's schema, and the owner and domain into its properties.
metadata.apply(platform, described)
table = platform.load_table("crm.learners")
print("table properties:", ", ".join(f"{key}={value}" for key, value in sorted(table.properties.items())))
print("email's description, in the Iceberg schema:", table.schema().find_field("email").doc)

# The check: every table in the catalog has an owner, and every column one of the four classifications.
print("problems the check finds:", len(metadata.problems(platform, described)))
