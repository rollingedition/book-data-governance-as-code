"""Measures the pairs LSH misses against every pair compared exactly, under two tunings and two hashing schemes, and shows what datasketch does with sketches of two schemes. The prose's point rests on the "missed" lines."""
from itertools import combinations

from datasketch import MinHashLSH

from lib import ai_data
from lib.catalog import open_catalog
from lib.enforce import Enforcer

rows, _ = ai_data.read_pseudonymized(Enforcer(open_catalog(["crm.learners", "learning.courses", "learning.reviews"])))
texts = [row["body"] for row in ai_data.exact_distinct(rows)]
# the similarity at which a pair is a near-duplicate
threshold = ai_data.DUPLICATES["threshold"]

# Every pair compared exactly: possible at this size, and the truth LSH is measured against.
pairs = {(i, j) for i, j in combinations(range(len(texts)), 2) if ai_data.jaccard(texts[i], texts[j]) >= threshold}
print("pairs compared exactly:", f"{len(texts) * (len(texts) - 1) // 2:,}")
print(f"pairs at a Jaccard similarity of {threshold} or more:", len(pairs))

found = {}
for scheme in ["affine32", "legacy"]:
    # LSH tuned for the threshold, and for the lower candidate threshold
    for tuned_for in [threshold, ai_data.DUPLICATES["candidate_threshold"]]:
        # the bands and rows LSH chose, its candidates, and the candidates the exact check confirms
        found[scheme, tuned_for] = result = ai_data.near_duplicates(texts, threshold, tuned_for, scheme)
        setting = f"{scheme}, LSH tuned for {tuned_for}"
        print(f"{setting}, bands:", result.bands)
        print(f"{setting}, rows per band:", result.rows)
        print(f"{setting}, candidate pairs:", len(result.candidates))
        print(f"{setting}, confirmed:", len(result.confirmed))
        # pairs the exact comparison found that LSH never proposed as candidates
        print(f"{setting}, missed:", len(pairs - result.candidates))

# The chance that a pair of similarity s becomes a candidate, with b bands of r rows: 1 - (1 - s^r)^b.
# the bands and rows LSH chose for the threshold, as the formula uses them
b, r = found["affine32", threshold].bands, found["affine32", threshold].rows
for s in [0.8, 0.9]:
    print(f"chance of a candidate at similarity {s}, LSH tuned for {threshold}:", f"{1 - (1 - s ** r) ** b:.2f}")
# the S-curve's approximate threshold, which the prose compares with the threshold asked for
print("the approximate threshold, (1/b)^(1/r):", f"{(1 / b) ** (1 / r):.2f}")

# What datasketch does with a sketch made under the other scheme.
# an index takes sketches of one scheme only: the first insert fixes it
index = MinHashLSH(threshold=threshold, num_perm=ai_data.DUPLICATES["num_perm"])
index.insert(0, ai_data.minhash(texts[0], "affine32"))
try:
    index.insert(1, ai_data.minhash(texts[1], "legacy"))  # datasketch refuses a sketch of another scheme
except ValueError as error:
    print("a legacy sketch in an index of affine32 sketches:", error)
