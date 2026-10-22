"""Run the marketing_audience pipeline with OpenLineage run events, then read its column lineage back from the events."""

from lib import lineage, metadata, warehouse
from lib.catalog import open_catalog
from lib.platform import scratch

platform = open_catalog(["crm.learners", "learning.courses", "learning.enrollments"])
events_file = scratch() / "events.jsonl"
lineage.run(platform, "marketing_audience", lineage.client(events_file))
events = lineage.read_events(events_file)

print("events:", ", ".join(f"{e['eventType']} of {e['job']['name']}" for e in events))
print("the run read:", ", ".join(dataset["name"] for dataset in events[0]["inputs"]))
print("the run wrote:", events[-1]["outputs"][0]["name"])

graph = lineage.graph(events)
for column in ("full_name", "email"):
    inputs = [e for e in graph.edges if e.target == f"marketing.audience.{column}"]
    print(f"marketing.audience.{column} comes from:", ", ".join(f"{e.source} ({e.type} {e.subtype})" for e in inputs))
for subtype in ("FILTER", "JOIN"):
    shaping = [e.source for e in graph.edges if e.target == "marketing.audience" and e.subtype == subtype]
    print(f"its rows are shaped by, INDIRECT {subtype}:", ", ".join(shaping))
duckdb = warehouse.connect(platform)                  # DuckDB on the catalog's tables, to count what the join dropped
consenting = "select count(*) from crm.learners where marketing_consent and status = 'active'"
print("consenting, active learners in crm.learners:", f"{duckdb.sql(consenting).fetchall()[0][0]:,}")
print("rows in marketing.audience:", f"{duckdb.sql('select count(*) from marketing.audience').fetchall()[0][0]:,}")
reached = lineage.columns_reached(graph, lineage.downstream(graph, "crm.learners.email"))
print("a change to crm.learners.email reaches:", ", ".join(reached))

# The catalog check, run on the catalog the pipeline wrote to.
for problem in metadata.problems(platform, metadata.load()):
    print("the catalog check finds:", problem)
