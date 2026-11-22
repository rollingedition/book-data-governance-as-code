"""Draws, for each reviewed classification, how the recorded model answered for its columns, as one bar split by the answer.
The prose's point rests on the internal shares and on the two counts of columns protected less and more than the review."""
from lib import metadata
from lib.figures import ACCENT, INK, MUTED, RULE, new_figure, save
from collections import Counter

reviewed = metadata.load()                                   # governance/metadata.toml, reviewed by people
suggested = metadata.suggestions()                           # the recorded answers under the book's prompt: no model runs here
assert all(s.classification in metadata.LEVELS for s in suggested)   # every answer is one of the four, so each bar is whole
pairs = Counter((reviewed[s.table].columns[s.field_id].classification, s.classification) for s in suggested)
total = {was: sum(n for (w, _), n in pairs.items() if w == was) for was in metadata.LEVELS}
rank = metadata.LEVELS.index                                 # a larger rank is more protected

print("columns the model was asked about:", len(suggested))
for was in metadata.LEVELS:
    print(f"reviewed {was} columns:", total[was])
    print(f"{was} columns the model called internal:", pairs[was, "internal"])
    print(f"share of {was} columns the model called internal:", f"{pairs[was, 'internal'] / total[was]:.0%}")
print("columns protected less than the review:", sum(n for (w, s), n in pairs.items() if rank(s) < rank(w)))
print("columns protected more than the review:", sum(n for (w, s), n in pairs.items() if rank(s) > rank(w)))

# One bar per reviewed classification, public at the top. Each segment is the share of that classification's columns
# the model answered with, from least to most protected, labelled with its count.
style = {"public": (RULE, INK), "internal": (ACCENT, RULE), "personal": (MUTED, RULE), "sensitive": (INK, RULE)}
fig, ax = new_figure(height=2.9)
handles = {}
for row, was in enumerate(reversed(metadata.LEVELS)):
    left = 0.0
    for said in metadata.LEVELS:
        n = pairs[was, said]
        if n:
            share, (fill, text) = n / total[was], style[said]
            handles.setdefault(said, ax.barh(row, share, left=left, height=0.6, color=fill, edgecolor=MUTED, linewidth=0.6))
            ax.text(left + share / 2, row, str(n), ha="center", va="center", color=text, fontsize=9)
            left += share
ax.set_yticks(range(len(metadata.LEVELS)), list(reversed(metadata.LEVELS)))
ax.set_xlim(0, 1)
ax.set_xticks([0, 0.25, 0.5, 0.75, 1], ["0%", "25%", "50%", "75%", "100%"])
ax.set_xlabel("share of the reviewed classification's columns, by the model's answer")
ax.set_ylabel("reviewed classification")
ax.grid(False)
ax.legend(list(handles.values()), [f"model said {s}" for s in handles], loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=3)
save(fig, "model-against-review")
