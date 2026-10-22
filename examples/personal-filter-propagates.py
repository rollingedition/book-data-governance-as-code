"""A table chosen by a personal column: the learners with a birthday this month, for a birthday offer. None of its
values is personal, yet each row reveals the month of a learner's birth. Only counting INDIRECT inputs proposes
personal."""

from lib import lineage
from lib.catalog import open_catalog
from lib.platform import scratch
from lib.warehouse import connect

catalog = open_catalog(["crm.learners"])
# The consenting learners born in the month of the nightly run, on the book's "today".
select = ("select l.learner_id, l.country from crm.learners l "
          f"where l.marketing_consent and month(l.date_of_birth) = {lineage.NIGHTLY.month}")


def offers() -> None:
    rows = connect(catalog).sql(select).to_arrow_table()
    catalog.create_namespace_if_not_exists("marketing")
    catalog.create_table("marketing.birthday_offers", schema=rows.schema).append(rows)


events_file = scratch() / "events.jsonl"
lineage.record(lineage.client(events_file), "birthday_offers", f"create table marketing.birthday_offers as {select}",
               catalog, offers)
offers_table = catalog.load_table("marketing.birthday_offers").scan().to_arrow()
print("learners in marketing.birthday_offers:", f"{len(offers_table):,}")

graph, known = lineage.graph(lineage.read_events(events_file)), lineage.declared()
print("crm.learners.date_of_birth declared:", known["crm.learners.date_of_birth"])
for through in (["DIRECT", "INDIRECT"], ["DIRECT"]):
    for column, (classification, decided_by) in lineage.propose(graph, known, through).items():
        label = f"{column}, through {' and '.join(through)}"
        print(f"{label}:", classification)
        print(f"{label}, decided by:", f"{decided_by[0].source} ({decided_by[0].type} {decided_by[0].subtype})")
