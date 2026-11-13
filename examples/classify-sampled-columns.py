"""Proposes a classification for every column, the strictest of what its name and its sampled values support, and compares
each proposal with the reviewed metadata. The prose's table rests on the proposed and found lines of each column, and
its counts on the totals printed after them."""
from lib import metadata, pii
from lib.catalog import open_catalog
from lib.warehouse import connect

catalog = open_catalog()
findings = pii.classify(catalog, connect(catalog), pii.platform_detector())    # the platform's tuned detector, not the defaults
print("columns classified:", len(findings))
print("columns sampled:", sum(f.sampled > 0 for f in findings))

holding = [f for f in findings if f.found]          # columns where sampled values hold personal data
print("columns whose sampled values hold personal data:", len(holding))
# A column counts when its values hold an entity other than the one its name suggests (named is None when no rule matches).
print("of them, holding something their name doesn't suggest:", sum(set(f.found) != {f.named} for f in holding))
for f in holding:
    print(f"{f.table}.{f.column}, proposed: {f.classification}")
    print(f"{f.table}.{f.column}, found: " + ", ".join(f"{e} in {n} of {f.sampled}" for e, n in f.found.most_common()))
[cards] = [f for f in findings if f.column == "card_number"]     # the one card_number column in the catalog
# The card recognizer's count: values that match its pattern and pass its Luhn check, out of the values sampled.
print("sampled card numbers the card recognizer accepts:", f"{cards.found['CREDIT_CARD']} of {cards.sampled}")

# Classifications the values raise above what the name alone gives: the floor is used for a name no rule matches.
named_only = [f for f in findings if pii.CLASSIFICATION.get(f.named, pii.FLOOR) != f.classification]
print("columns the values classify more strictly than the name:", ", ".join(f"{f.table}.{f.column}" for f in named_only))

# Compare with the metadata people reviewed. A column the detector found nothing in proposes only the floor, internal.
described = metadata.load()
evidenced = [f for f in findings if f.named or f.found]
print("columns with evidence:", len(evidenced))
print("of them, equal to the metadata:",
      sum(f.classification == described[f.table].columns[f.field_id].classification for f in evidenced))
for f in evidenced:
    reviewed = described[f.table].columns[f.field_id].classification
    if metadata.stricter(f.classification, reviewed) != reviewed:     # the stricter of the two: a lower proposal prints nothing
        print(f"stricter than the metadata, {f.table}.{f.column}: {f.classification}, reviewed as {reviewed}")
