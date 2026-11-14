"""Runs the platform's four pipelines on a fresh catalog, then checks every table in it against the metadata file. The
prose's point rests on the `problems found` line and the two `no metadata` lines that follow it."""
from lib import catalog, metadata, pipelines

platform = catalog.open_catalog()
for name in ("course_performance", "learner_activity", "marketing_audience", "review_corpus"):   # each one a file in pipelines/
    print("pipeline output:", pipelines.run(platform, name))

# The eight source tables plus the four outputs the pipelines just created.
tables = [f"{namespace}.{name}" for (namespace,) in platform.list_namespaces() for _, name in platform.list_tables(namespace)]
print("tables in the catalog:", len(tables))
found = metadata.problems(platform, metadata.load())
print("problems found:", len(found))
for problem in found:
    print("problem:", problem)
