"""What a change to a source column can break downstream, read from the events the nightly run sent."""

from lib import lineage
from lib.catalog import open_catalog
from lib.platform import scratch

events_file = scratch() / "events.jsonl"
lineage.run_all(open_catalog(), events_file)
graph = lineage.graph(lineage.read_events(events_file))
print("edges in the column graph:", len(graph.edges))

for column in ("crm.learners.email", "crm.learners.marketing_consent", "learning.courses.category"):
    reached = lineage.downstream(graph, column)
    print(f"{column} reaches:")
    for target in dict.fromkeys(edge.target for edge in reached):
        how = ", ".join(f"{edge.type} {edge.subtype}" for edge in reached if edge.target == target)
        print(f"  {target}{', every row' if target.count('.') == 1 else ''}:", how)
    values = [edge for edge in reached if edge.type == "DIRECT"]
    print(f"{column}, columns reached through values:", len(lineage.columns_reached(graph, values)))
    print(f"{column}, columns reached in all:", len(lineage.columns_reached(graph, reached)))
