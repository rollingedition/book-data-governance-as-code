"""Propose classifications for the pipeline outputs that have no metadata, from their lineage, and check the rule
against the outputs people did classify."""

from lib import lineage, metadata
from lib.catalog import open_catalog
from lib.platform import scratch

catalog = open_catalog()
events_file = scratch() / "events.jsonl"
lineage.run_all(catalog, events_file)
for problem in metadata.problems(catalog, metadata.load()):
    print("metadata problem:", problem)

graph, known = lineage.graph(lineage.read_events(events_file)), lineage.declared()
print("propagated through:", " and ".join(lineage.rules()["propagate_through"]))
proposed = lineage.propose(graph, known)
for column, (classification, decided_by) in proposed.items():
    if column not in known:
        first = decided_by[0]
        print(f"{column}: {classification}, from {first.source} ({first.type} {first.subtype})")
print("personal columns proposed:", sum(1 for column, (classification, _) in proposed.items()
                                        if column not in known and classification == "personal"))

classified = [column for column in proposed if column in known]
print("declared classifications checked:", len(classified))
print("classes declared for them:", ", ".join(sorted({known[column] for column in classified})))
for through in (["DIRECT", "INDIRECT"], ["DIRECT"]):
    proposals = lineage.propose(graph, known, through)
    agree = sum(1 for column in classified if proposals[column][0] == known[column])
    print(f"declared classifications reproduced, through {' and '.join(through)}:", f"{agree} of {len(classified)}")
missed = [column for column in classified if proposals[column][0] != known[column]]    # with DIRECT inputs only
print("missed with DIRECT inputs only:", len(missed))
for column in missed:
    print(f"  {column}: declared {known[column]}, DIRECT inputs propose {proposals[column][0]}")
print("declared less protected than lineage proposes:", len(lineage.understated(proposed, known)))
