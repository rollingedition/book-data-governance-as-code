"""Draws the chance LSH makes a pair a candidate against its Jaccard similarity, for the bands and rows datasketch chooses
when tuned for 0.8 and for 0.6. The printed lines give each setting's bands, rows and chance at 0.8, which the prose rests on."""
from lib import ai_data
from lib.catalog import open_catalog
from lib.enforce import Enforcer
from lib.figures import ACCENT, INK, MUTED, new_figure, save

import numpy as np

rows, _ = ai_data.read_pseudonymized(Enforcer(open_catalog(["crm.learners", "learning.courses", "learning.reviews"])))
texts = [row["body"] for row in ai_data.exact_distinct(rows)]
threshold = ai_data.DUPLICATES["threshold"]                 # the confirmation threshold, drawn as a dotted line
candidate = ai_data.DUPLICATES["candidate_threshold"]       # the lower threshold LSH is tuned for
scheme = ai_data.DUPLICATES["scheme"]

x = np.linspace(0, 1, 201)                                  # every 0.005, fine enough for a smooth S
fig, ax = new_figure(height=3.4)
for tuned_for, color, style in ((candidate, ACCENT, "-"), (threshold, INK, "--")):
    # datasketch's MinHashLSH picks b and r for the threshold it's tuned for; near_duplicates returns them
    result = ai_data.near_duplicates(texts, threshold, tuned_for, scheme)
    b, r = result.bands, result.rows
    chance = 1 - (1 - x ** r) ** b                          # P(candidate) = 1 - (1 - s^r)^b, for each similarity s
    print(f"tuned for {tuned_for}, bands:", b)
    print(f"tuned for {tuned_for}, rows per band:", r)
    print(f"tuned for {tuned_for}, chance of a candidate at similarity {threshold}:", f"{1 - (1 - threshold ** r) ** b:.2f}")
    ax.plot(x, chance, color=color, linestyle=style, label=f"tuned for {tuned_for}: {b} bands of {r} rows")
ax.axvline(threshold, linestyle=":", color=INK, linewidth=1)
ax.text(threshold + 0.015, 0.08, f"confirmed at {threshold}", color=INK, ha="left", va="center")
ax.set_xlim(0, 1)
ax.set_ylim(0, 1.05)
ax.set_xlabel("Jaccard similarity of the pair")
ax.set_ylabel("chance of a candidate")
ax.legend(loc="upper left")
save(fig, "lsh-candidate-chance")
