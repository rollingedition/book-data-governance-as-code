"""A Python step copies marketing.audience for the email tool and sends no lineage. The prose rests on the walk from the
email column, which misses the copy, and on the catalog check against the lineage, which finds it."""

from lib import lineage
from lib.catalog import open_catalog
from lib.platform import scratch
from lib.warehouse import connect

catalog = open_catalog()
events_file = scratch() / "events.jsonl"
lineage.run_all(catalog, events_file)

# The marketing team's weekly export, written straight to the catalog: no lineage event.
export = connect(catalog).sql("select learner_id, full_name, email from marketing.audience").to_arrow_table()
catalog.create_table("marketing.campaign_list", schema=export.schema).append(export)
print("rows exported:", f"{len(export):,}")

events = lineage.read_events(events_file)
reached = lineage.downstream(lineage.graph(events), "crm.learners.email")
print("crm.learners.email reaches:", ", ".join(edge.target for edge in reached))
# the catalog's tables, less the platform's sources and the outputs the events name
print("tables no job's lineage accounts for:", ", ".join(lineage.unaccounted(catalog, events)))
