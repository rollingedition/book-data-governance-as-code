from collections import Counter

from lib import metadata

described = metadata.load()                      # governance/metadata.toml
columns = [column for table in described.values() for column in table.columns.values()]
print("tables described:", len(described))
print("columns described:", len(columns))
counts = Counter(column.classification for column in columns)
for level in metadata.LEVELS:
    print(f"columns classified {level}:", counts[level])
owners = sorted({table.owner for table in described.values()})
print("teams that own tables:", len(owners))
print("owners:", ", ".join(owners))

learners = described["crm.learners"]
print("crm.learners owner:", learners.owner)
print("crm.learners domain:", learners.domain)
for field_id in (1, 3, 5, 6):
    column = learners.columns[field_id]
    print(f"crm.learners field {field_id}:", column.name, "|", column.classification, "|", column.description)
