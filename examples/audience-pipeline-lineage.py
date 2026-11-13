"""Run the marketing_audience pipeline with OpenLineage events, then read its column lineage back from the events alone.
The point rests on the two INDIRECT lines: a filter and a join decide which rows the audience table holds."""

from lib import lineage, metadata, warehouse
from lib.catalog import open_catalog
from lib.platform import scratch

platform = open_catalog(["crm.learners", "learning.courses", "learning.enrollments"])
events_file = scratch() / "events.jsonl"                 # the file that stands in for a lineage server: one event per line
lineage.run(platform, "marketing_audience", lineage.client(events_file))
events = lineage.read_events(events_file)

print("events:", ", ".join(f"{e['eventType']} of {e['job']['name']}" for e in events))
print("the run read:", ", ".join(dataset["name"] for dataset in events[0]["inputs"]))  # the START event lists the inputs
print("the run wrote:", events[-1]["outputs"][0]["name"])                             # the last event, COMPLETE, names the output

graph = lineage.graph(events)                            # built from the COMPLETE events' column lineage, not from the SQL
for column in ("full_name", "email"):
    inputs = [e for e in graph.edges if e.target == f"marketing.audience.{column}"]
    print(f"marketing.audience.{column} comes from:", ", ".join(f"{e.source} ({e.type} {e.subtype})" for e in inputs))
# The INDIRECT inputs of each kind: the inputs that decide which rows exist, not the ones copied into them.
for subtype in ("FILTER", "JOIN"):
    shaping = [e.source for e in graph.edges if e.target == "marketing.audience" and e.subtype == subtype]
    print(f"its rows are shaped by, INDIRECT {subtype}:", ", ".join(shaping))
duckdb = warehouse.connect(platform)                     # DuckDB on the catalog's tables, to count what the join dropped
# The learners the filter keeps, counted on crm.learners before any join drops one.
consenting = "select count(*) from crm.learners where marketing_consent and status = 'active'"
print("consenting, active learners in crm.learners:", f"{duckdb.sql(consenting).fetchall()[0][0]:,}")  # one row, one column
print("rows in marketing.audience:", f"{duckdb.sql('select count(*) from marketing.audience').fetchall()[0][0]:,}")  # one row, one column
# Every column a change to email can reach, at any depth of the graph.
reached = lineage.columns_reached(graph, lineage.downstream(graph, "crm.learners.email"))
print("a change to crm.learners.email reaches:", ", ".join(reached))

# The catalog check, run on the catalog the pipeline wrote to.
for problem in metadata.problems(platform, metadata.load()):
    print("the catalog check finds:", problem)
