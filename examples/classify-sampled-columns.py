from lib import metadata, pii
from lib.catalog import open_catalog
from lib.warehouse import connect

catalog = open_catalog()
findings = pii.classify(catalog, connect(catalog), pii.platform_detector())
print("columns classified:", len(findings))
print("columns sampled:", sum(f.sampled > 0 for f in findings))

holding = [f for f in findings if f.found]          # columns where sampled values hold personal data
print("columns whose sampled values hold personal data:", len(holding))
print("of them, holding something their name doesn't suggest:", sum(set(f.found) != {f.named} for f in holding))
for f in holding:
    print(f"{f.table}.{f.column}, proposed: {f.classification}")
    print(f"{f.table}.{f.column}, found: " + ", ".join(f"{e} in {n} of {f.sampled}" for e, n in f.found.most_common()))
[cards] = [f for f in findings if f.column == "card_number"]
print("sampled card numbers the card recognizer accepts:", f"{cards.found['CREDIT_CARD']} of {cards.sampled}")

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
    if metadata.stricter(f.classification, reviewed) != reviewed:
        print(f"stricter than the metadata, {f.table}.{f.column}: {f.classification}, reviewed as {reviewed}")
