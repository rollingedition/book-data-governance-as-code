from itertools import combinations

from lib import ai_data
from lib.catalog import open_catalog
from lib.enforce import Enforcer

# The reviews the corpus job reads, three ways: names as written, keyed pseudonyms, and plain placeholders.
layer = Enforcer(open_catalog(["crm.learners", "learning.courses", "learning.reviews"]))
keyed, _ = ai_data.read_pseudonymized(layer)
ways = {"names as written": ai_data.read(layer), "keyed pseudonyms": keyed,
        "placeholders": [row | {"body": ai_data.placeholders(row["body"])} for row in keyed]}
threshold = ai_data.DUPLICATES["threshold"]
for way, rows in ways.items():
    texts = [row["body"] for row in ai_data.exact_distinct(rows)]
    print(f"{way}, distinct texts:", len(texts))
    print(f"{way}, pairs at {threshold} or more:", len(ai_data.near_duplicates(texts).confirmed))

# Every pair of distinct pseudonymized texts, split by whether the pair differs only in a name.
texts = [row["body"] for row in ai_data.exact_distinct(keyed)]
name_only, other = [], []
for a, b in combinations(texts, 2):
    pair = (ai_data.jaccard(a, b), len(a), a, b)
    (name_only if ai_data.placeholders(a) == ai_data.placeholders(b) else other).append(pair)
print("pairs that differ only in a name:", len(name_only))
print("their Jaccard similarity:", f"{min(name_only)[0]:.2f} to {max(name_only)[0]:.2f}")
for label, lengths in [(f"below {threshold}", [n for s, n, _, _ in name_only if s < threshold]),
                       (f"at {threshold} or more", [n for s, n, _, _ in name_only if s >= threshold])]:
    print(f"their length in characters, {label}:", f"{min(lengths)} to {max(lengths)}")
similarity, _, a, b = max(other)
print("the most similar pair that differs in more than a name:", f"{similarity:.2f}")
print("that pair:", a, "|", b)
