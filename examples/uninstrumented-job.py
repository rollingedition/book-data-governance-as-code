"""A Python step that copies marketing.audience for the email tool and tells OpenLineage nothing: impact analysis misses
the copy, and a check of the catalog against the lineage finds it."""

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
print("tables no job's lineage accounts for:", ", ".join(lineage.unaccounted(catalog, events)))
