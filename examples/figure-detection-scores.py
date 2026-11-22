"""Draws the score of every detection Presidio's defaults make on the tickets and reviews, one row per entity type,
against the 0.5 threshold. The point rests on `PHONE_NUMBER, at or above 0.5: 0`: no phone detection reaches the threshold."""
from collections import defaultdict

from lib import pii
from lib.figures import ACCENT, INK, MUTED, new_figure, save

detector = pii.detector()                       # Presidio's defaults: every recognizer at threshold 0, as measure-pattern-recognizers runs them
scores = defaultdict(list)                      # entity type -> the score of each detection
for source in pii.SOURCES:
    for text in pii.texts(source).values():
        # each detection is (start, end, entity type, score); the span doesn't matter here
        for _, _, entity, score in pii.detect(detector, text):
            scores[entity].append(score)
assert len(set(scores["PHONE_NUMBER"])) == 1    # every phone detection has the one score the prose quotes

THRESHOLD = 0.5                                 # the single threshold the tune example tries; a detection at or above it is kept
types = sorted(scores)
fig, ax = new_figure(height=4.4)
for row, entity in enumerate(types):
    found = scores[entity]
    print(f"{entity}, detections: {len(found):,}")
    print(f"{entity}, lowest score: {min(found):.2f}")
    print(f"{entity}, highest score: {max(found):.2f}")
    print(f"{entity}, at or above {THRESHOLD}: {sum(s >= THRESHOLD for s in found):,}")
    for value in sorted(set(found)):            # each distinct score is one dot; the count sits beside the type's name
        ax.scatter(value, row, s=44, color=ACCENT if entity == "PHONE_NUMBER" else MUTED, zorder=3)
ax.axvline(THRESHOLD, linestyle=":", color=INK, linewidth=1, zorder=4)
ax.text(THRESHOLD + 0.015, len(types) - 0.45, f"threshold {THRESHOLD}", color=INK, va="center")
ax.set_yticks(range(len(types)), labels=[f"{t} ({len(scores[t]):,})" for t in types])
ax.set_ylim(-0.6, len(types) - 0.2)
ax.set_xlim(0, 1.05)
ax.set_xlabel("detection score")
ax.set_ylabel("entity type (detections)")
ax.grid(axis="y", visible=False)
save(fig, "detection-scores")
