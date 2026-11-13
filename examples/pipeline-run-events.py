"""Runs the four pipelines with lineage twice, into two files, and reads back what the first file's events say. The
prose rests on the counts of events and run IDs, and on the same-bytes check, which the example asserts."""

from openlineage.client.uuid import generate_new_uuid, generate_static_uuid

from lib import lineage
from lib.catalog import open_catalog
from lib.platform import scratch

folder = scratch()
catalog = open_catalog()
lineage.run_all(catalog, folder / "first.jsonl")
lineage.run_all(catalog, folder / "second.jsonl")          # the same nightly run, sent again
# A fixed event time and run IDs derived from it make the two files identical, byte for byte.
assert (folder / "first.jsonl").read_bytes() == (folder / "second.jsonl").read_bytes()
events = lineage.read_events(folder / "first.jsonl")

print("events:", len(events))
for event in events:
    job, state = event["job"]["name"], event["eventType"]
    if state == "START":
        print(f"{job} START, reads:", ", ".join(dataset["name"] for dataset in event["inputs"]))
    else:
        output = event["outputs"][0]
        lineage_fields = output["facets"]["columnLineage"]["fields"]
        print(f"{job} {state}, writes:", output["name"], f"({len(lineage_fields)} columns with lineage)")

print("event times:", ", ".join(sorted({event["eventTime"] for event in events})))
print("distinct run IDs:", len({event["run"]["runId"] for event in events}))
print("run facets the client added:", ", ".join(events[0]["run"]["facets"]))
print("same bytes when sent again:", (folder / "first.jsonl").read_bytes() == (folder / "second.jsonl").read_bytes())
# generate_new_uuid draws a random UUID each time; generate_static_uuid derives one from its arguments.
print("two random run IDs equal:", generate_new_uuid() == generate_new_uuid())
print("two static run IDs equal:", generate_static_uuid(lineage.NIGHTLY, b"marketing_audience")
      == generate_static_uuid(lineage.NIGHTLY, b"marketing_audience"))
