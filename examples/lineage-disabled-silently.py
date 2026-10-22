"""With OPENLINEAGE_DISABLED=true in the environment, an explicit file transport writes nothing and raises nothing: its
only notice is one INFO log line, which Python's default logging level hides."""
import os

from lib import lineage
from lib.catalog import open_catalog
from lib.platform import scratch

os.environ["OPENLINEAGE_DISABLED"] = "true"        # say, left in a shared environment file after a debugging session

events_file = scratch() / "events.jsonl"
client = lineage.client(events_file)                # a file transport, passed in explicitly
catalog = open_catalog(["learning.courses", "learning.reviews"])
output = lineage.run(catalog, "review_corpus", client)

print("transport the client chose:", client.transport.kind)
print("pipeline wrote:", output)
print("rows written:", f"{len(catalog.load_table(output).scan().to_arrow()):,}")
print("events file exists:", events_file.exists())
missing = lineage.missing_runs(["review_corpus"], lineage.read_events(events_file))
print("jobs that ran with no COMPLETE event:", ", ".join(missing))
