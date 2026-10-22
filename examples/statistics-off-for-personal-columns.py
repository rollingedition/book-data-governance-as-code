"""Generate each table's properties from the classifications, so manifests keep no values of personal columns, then
erase the learner whose email is the platform's largest on a platform with them and on one without."""
from datetime import timedelta

from lib import retention

LEARNER = 4998
for statistics_off in (False, True):
    catalog, graph = retention.open_platform(statistics_off)
    label = "statistics off for protected columns" if statistics_off else "default statistics"
    if statistics_off:
        generated = retention.properties(catalog)
        turned_off = [key for wanted in generated.values() for key in wanted if key.startswith(retention.METRICS)]
        print("columns with statistics turned off:", len(turned_off))
        unclassified = [name for name in generated if name not in retention.metadata.load()]
        print("of them in tables nobody has classified:",
              sum(key.startswith(retention.METRICS) for name in unclassified for key in generated[name]))
        print("crm.learners, properties set:", len(generated["crm.learners"]))
        for key, value in sorted(generated["crm.learners"].items()):
            print(f"  {key} = {value}")
    entry = catalog.load_table("crm.learners").inspect.entries()["readable_metrics"].to_pylist()[0]
    print(f"{label}, crm.learners email bounds:", entry["email"]["lower_bound"], "to", entry["email"]["upper_bound"])
    columns = retention.metadata.load()["crm.learners"].columns.values()
    personal = [column.name for column in columns if column.classification == "personal"]
    print(f"{label}, crm.learners personal columns:", len(personal))
    with_bounds = sum(entry[name]["lower_bound"] is not None for name in personal)
    print(f"{label}, of them with bounds in the manifest:", with_bounds)
    steps, found, _ = retention.erase_everywhere(catalog, graph, LEARNER, "erasure-0002", min_age=timedelta(0))
    print(f"{label}, files holding learner {LEARNER} after erasure, expiry and sweep:",
          sum(held for _, held in retention.scan(catalog, found).values()))
