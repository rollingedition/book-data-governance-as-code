"""The same export, now sending lineage, in a table its owner tagged internal: lineage finds the personal data in it."""

from lib import lineage
from lib.catalog import open_catalog
from lib.platform import scratch
from lib.warehouse import connect

catalog = open_catalog()
events_file = scratch() / "events.jsonl"
lineage.run_all(catalog, events_file)

select = "select a.learner_id, a.full_name, a.email from marketing.audience a"


def export() -> None:
    rows = connect(catalog).sql(select).to_arrow_table()
    catalog.create_table("marketing.campaign_list", schema=rows.schema).append(rows)


statement = f"create table marketing.campaign_list as {select}"
lineage.record(lineage.client(events_file), "campaign_export", statement, catalog, export)

# Its owner tagged every column internal: "only the marketing team reads it".
known = lineage.declared() | {f"marketing.campaign_list.{c}": "internal" for c in ("learner_id", "full_name", "email")}
graph = lineage.graph(lineage.read_events(events_file))
print("crm.learners.email reaches:", ", ".join(edge.target for edge in lineage.downstream(graph, "crm.learners.email")))
for problem in lineage.understated(lineage.propose(graph, known), known):
    print("understated:", problem)
