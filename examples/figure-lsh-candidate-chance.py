"""Draws the chance LSH makes a pair a candidate against its Jaccard similarity, for the bands and rows datasketch chooses
when tuned for 0.8 and for 0.6. The printed lines give each setting's bands, rows and chance at 0.8, which the prose rests on."""
from itertools import combinations

import numpy as np

from lib import ai_data
from lib.catalog import open_catalog
from lib.enforce import Enforcer
from lib.figures import ACCENT, INK, MUTED, new_figure, save

rows, _ = ai_data.read_pseudonymized(Enforcer(open_catalog(["crm.learners", "learning.courses", "learning.reviews"])))
texts = [row["body"] for row in ai_data.exact_distinct(rows)]
threshold = ai_data.DUPLICATES["threshold"]                 # the confirmation threshold, drawn as a dashed line
candidate = ai_data.DUPLICATES["candidate_threshold"]       # the lower threshold LSH is tuned for
scheme = ai_data.DUPLICATES["scheme"]
# the exact similarity of each pair at the threshold or more, drawn as a tick along the bottom
similar = [ai_data.jaccard(texts[i], texts[j]) for i, j in combinations(range(len(texts)), 2)]
similar = [s for s in similar if s >= threshold]
print(f"pairs at a Jaccard similarity of {threshold} or more:", len(similar))

x = np.linspace(0, 1, 201)                                  # Jaccard similarity from 0 to 1
fig, ax = new_figure(height=3.4)
for tuned_for, color, style in ((candidate, ACCENT, "-"), (threshold, INK, "--")):
    # the bands and rows LSH chose for this setting, as the existing LSH example gets them
    result = ai_data.near_duplicates(texts, threshold, tuned_for, scheme)
    b, r = result.bands, result.rows
    chance = 1 - (1 - x ** r) ** b                          # P(candidate) = 1 - (1 - s^r)^b, for each similarity s
    print(f"tuned for {tuned_for}, bands:", b)
    print(f"tuned for {tuned_for}, rows per band:", r)
    print(f"tuned for {tuned_for}, chance of a candidate at similarity {threshold}:", f"{1 - (1 - threshold ** r) ** b:.2f}")
    ax.plot(x, chance, color=color, linestyle=style, label=f"tuned for {tuned_for}: {b} bands of {r} rows")
ax.axvline(threshold, linestyle=":", color=MUTED, linewidth=1)
ax.text(threshold + 0.015, 0.2, f"threshold {threshold}", color=MUTED, ha="left", va="center")
ax.plot(similar, [0] * len(similar), "|", color=INK, markersize=9, clip_on=False, label=f"pairs at {threshold} or more")
ax.set_xlim(0, 1)
ax.set_ylim(0, 1.05)
ax.set_xlabel("Jaccard similarity of the pair")
ax.set_ylabel("chance of a candidate")
ax.legend(loc="upper left")
save(fig, "lsh-candidate-chance")
