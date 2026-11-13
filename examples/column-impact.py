"""What a change to one source column can break, walked over the column graph that the nightly run's events describe.
The prose rests on the reach counts printed for each column: through values, and in all."""

from lib import lineage
from lib.catalog import open_catalog
from lib.platform import scratch

events_file = scratch() / "events.jsonl"
lineage.run_all(open_catalog(), events_file)
graph = lineage.graph(lineage.read_events(events_file))    # built from the events alone, as a lineage server would
print("edges in the column graph:", len(graph.edges))

for column in ("crm.learners.email", "crm.learners.marketing_consent", "learning.courses.category"):
    reached = lineage.downstream(graph, column)
    print(f"{column} reaches:")
    # dict.fromkeys drops repeated targets and keeps the order they were found in
    for target in dict.fromkeys(edge.target for edge in reached):
        how = ", ".join(f"{edge.type} {edge.subtype}" for edge in reached if edge.target == target)
        # a table's name has one dot and a column's two, so a table target means every row of it
        print(f"  {target}{', every row' if target.count('.') == 1 else ''}:", how)
    values = [edge for edge in reached if edge.type == "DIRECT"]    # only DIRECT edges copy values
    print(f"{column}, columns reached through values:", len(lineage.columns_reached(graph, values)))
    print(f"{column}, columns reached in all:", len(lineage.columns_reached(graph, reached)))
