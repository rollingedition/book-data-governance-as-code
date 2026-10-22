from lib import catalog, metadata, pipelines

platform = catalog.open_catalog()
for name in ("course_performance", "learner_activity", "marketing_audience", "review_corpus"):
    print("pipeline output:", pipelines.run(platform, name))

tables = [f"{namespace}.{name}" for (namespace,) in platform.list_namespaces() for _, name in platform.list_tables(namespace)]
print("tables in the catalog:", len(tables))
found = metadata.problems(platform, metadata.load())
print("problems found:", len(found))
for problem in found:
    print("problem:", problem)
