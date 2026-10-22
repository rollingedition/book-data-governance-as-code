from itertools import combinations

from datasketch import MinHashLSH

from lib import ai_data
from lib.catalog import open_catalog
from lib.enforce import Enforcer

rows, _ = ai_data.read_pseudonymized(Enforcer(open_catalog(["crm.learners", "learning.courses", "learning.reviews"])))
texts = [row["body"] for row in ai_data.exact_distinct(rows)]
threshold = ai_data.DUPLICATES["threshold"]

# Every pair compared exactly: possible at this size, and the truth LSH is measured against.
pairs = {(i, j) for i, j in combinations(range(len(texts)), 2) if ai_data.jaccard(texts[i], texts[j]) >= threshold}
print("pairs compared exactly:", f"{len(texts) * (len(texts) - 1) // 2:,}")
print(f"pairs at a Jaccard similarity of {threshold} or more:", len(pairs))

found = {}
for scheme in ["affine32", "legacy"]:
    for tuned_for in [threshold, ai_data.DUPLICATES["candidate_threshold"]]:
        found[scheme, tuned_for] = result = ai_data.near_duplicates(texts, threshold, tuned_for, scheme)
        setting = f"{scheme}, LSH tuned for {tuned_for}"
        print(f"{setting}, bands:", result.bands)
        print(f"{setting}, rows per band:", result.rows)
        print(f"{setting}, candidate pairs:", len(result.candidates))
        print(f"{setting}, confirmed:", len(result.confirmed))
        print(f"{setting}, missed:", len(pairs - result.candidates))

# The chance that a pair of similarity s becomes a candidate, with b bands of r rows: 1 - (1 - s^r)^b.
b, r = found["affine32", threshold].bands, found["affine32", threshold].rows
for s in [0.8, 0.9]:
    print(f"chance of a candidate at similarity {s}, LSH tuned for {threshold}:", f"{1 - (1 - s ** r) ** b:.2f}")
print("the approximate threshold, (1/b)^(1/r):", f"{(1 / b) ** (1 / r):.2f}")

# What datasketch does with a sketch made under the other scheme.
index = MinHashLSH(threshold=threshold, num_perm=ai_data.DUPLICATES["num_perm"])
index.insert(0, ai_data.minhash(texts[0], "affine32"))
try:
    index.insert(1, ai_data.minhash(texts[1], "legacy"))
except ValueError as error:
    print("a legacy sketch in an index of affine32 sketches:", error)
