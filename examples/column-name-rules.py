"""Runs the name rules over every column of the catalog and compares each column they mark with the reviewed metadata.
The prose rests on the count of marked columns that agree, and on the personal columns no rule marks."""
from pyiceberg.types import StringType

from lib import metadata, pii
from lib.catalog import open_catalog

catalog = open_catalog()
described = metadata.load()                   # the reviewed metadata, keyed by table and then by field ID
# every column as (table, field): the field carries the ID the metadata is keyed by, so a renamed column still matches
columns = [(table, f) for (namespace,) in sorted(catalog.list_namespaces())
           for _, name in sorted(catalog.list_tables(namespace))
           for table in [f"{namespace}.{name}"] for f in catalog.load_table(table).schema().fields]
print("columns in the catalog:", len(columns))
print("name rules:", len(pii.NAME_RULES))

named = [(table, f, pii.by_name(f.name)) for table, f in columns if pii.by_name(f.name)]
print("columns a name rule marks:", len(named))
for table, f, entity in named:
    print(f"  {table}.{f.name}: {entity}, so {pii.CLASSIFICATION[entity]}")
agree = sum(pii.CLASSIFICATION[entity] == described[table].columns[f.field_id].classification
            for table, f, entity in named)
print("of them, agreeing with the metadata:", agree)

# Only text is sampled: numbers, dates, timestamps and booleans are classified by name alone.
text = [(table, f) for table, f in columns if isinstance(f.field_type, StringType)]
print("columns of text, whose values can be sampled:", len(text))

# Columns the metadata, reviewed by people, calls personal or sensitive, that no name rule marks.
missed = [f"{table}.{f.name}" for table, f in columns if not pii.by_name(f.name)
          and described[table].columns[f.field_id].classification in ("personal", "sensitive")]
print("personal columns no name rule marks:", ", ".join(missed))
