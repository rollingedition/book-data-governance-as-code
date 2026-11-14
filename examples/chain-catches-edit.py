"""Replays the day's queries into a chained log, writes the head down at noon, then tampers with copies of the log five
ways and checks each against the chain alone and against the noon head. The point rests on the two checks of each
change: the chain finds the plain edit and the plain removal on its own, and only the head finds the rebuilt chain."""
import json

from lib import audit
from lib.catalog import open_catalog
from lib.platform import scratch

log = audit.Log(scratch() / "audit.jsonl")
platform = audit.Audited(open_catalog(["crm.learners", "learning.reviews", "billing.payments", "support.tickets"]), log)
audit.run_day(platform, [query for query in audit.DAY if query[0] < "12:00"])     # the morning; times compare as strings
anchor = log.head()                               # the head, written down at noon somewhere the log's writer can't reach
audit.run_day(platform, [query for query in audit.DAY if query[0] >= "12:00"])    # the afternoon
lines = log.lines()
print("records:", len(lines))
print("head written down at noon, at record:", anchor["records"])
print("record 3:", json.loads(lines[2])["user"], "reads", json.loads(lines[2])["query"])     # record 3 is index 2


def rechained(lines: list[str]) -> list[str]:
    """The chain built again, every record renumbered and every prev_hash recomputed: what anyone who can rewrite the
    log's file can do."""
    rebuilt, previous = [], audit.GENESIS          # the first record points at 64 zeros, not at a record
    for seq, line in enumerate(lines, start=1):
        rebuilt.append(audit.canonical(json.loads(line) | {"seq": seq, "prev_hash": previous}))
        previous = audit.sha256(rebuilt[-1])
    return rebuilt


# Before noon, record 3 handed to another agent. After noon, record 12 (someone-else's denied read) removed, and the
# day's last two records cut off.
edited = lines[:2] + [audit.canonical(json.loads(lines[2]) | {"user": "agent-a01"})] + lines[3:]     # index 2 is record 3
removed = lines[:11] + lines[12:]                                                                    # index 11 is record 12
for name, version in [("untouched", lines), ("record 3 edited", edited), ("record 3 edited, chain rebuilt", rechained(edited)),
                      ("record 12 removed", removed), ("record 12 removed, chain rebuilt", rechained(removed)),
                      ("last 2 records removed", lines[:-2])]:      # the day's last two records, cut off
    print(f"{name}, checked alone:", "; ".join(audit.check(version)) or "no problem found")
    print(f"{name}, checked against the noon head:", "; ".join(audit.check(version, anchor)) or "no problem found")
