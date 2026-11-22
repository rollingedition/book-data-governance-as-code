"""Draws, for each reviewed classification, one bar split by the model's answer: the columns it protected less than the review,
the ones it agreed with, and the ones it protected more. The prose's point rests on the personal and sensitive lines, which
hold no other kind of answer, and on the internal agreement count."""
from collections import Counter

from lib import metadata
from lib.figures import ACCENT, INK, MUTED, RULE, new_figure, save
from matplotlib.patches import Patch

reviewed = metadata.load()                                   # governance/metadata.toml, reviewed by people
suggested = metadata.suggestions()                           # the recorded answers under the book's prompt: no model runs here
assert all(s.classification in metadata.LEVELS for s in suggested)   # every answer is one of the four, so each bar is whole
rank = metadata.LEVELS.index                                 # a larger rank is more protected
pairs = Counter((reviewed[s.table].columns[s.field_id].classification, s.classification) for s in suggested)
total = {was: sum(n for (w, _), n in pairs.items() if w == was) for was in metadata.LEVELS}


def part(was, said):                                         # where the model's answer puts a column against the review
    return "protected less" if rank(said) < rank(was) else "protected more" if rank(said) > rank(was) else "agrees"


split = Counter()                                            # (reviewed classification, part) -> columns
for (was, said), n in pairs.items():                         # several answers can fall in one part
    split[was, part(was, said)] += n
assert all(part(w, s) == "protected less" for w, s in pairs if w in ("personal", "sensitive"))   # no other answer for these

print("reviewed personal columns:", total["personal"])
print("personal columns protected less than the review:", split["personal", "protected less"])
print("reviewed sensitive columns:", total["sensitive"])
print("sensitive columns protected less than the review:", split["sensitive", "protected less"])
print("reviewed internal columns:", total["internal"])
print("internal columns the model agreed with:", split["internal", "agrees"])
assert 2 * split["internal", "agrees"] > total["internal"]   # the prose says the internal bar is mostly agreement

# One bar per reviewed classification, public at the top. Parts run left to right in legend order. Each carries its count,
# the first is dark, the middle light and the last hatched, so colour never carries the meaning alone.
parts = {"protected less": (ACCENT, RULE, None), "agrees": (RULE, INK, None), "protected more": (RULE, INK, "///")}
fig, ax = new_figure(height=2.9)
for row, was in enumerate(reversed(metadata.LEVELS)):
    left = 0.0
    for name, (fill, text, hatch) in parts.items():
        n = split[was, name]
        if n:                                                # a classification with no answer of this kind gets no segment
            share = n / total[was]
            ax.barh(row, share, left=left, height=0.6, color=fill, edgecolor=MUTED, linewidth=0.6, hatch=hatch)
            ax.text(left + share / 2, row, str(n), ha="center", va="center", color=text, fontsize=9)
            left += share
ax.set_yticks(range(len(metadata.LEVELS)), list(reversed(metadata.LEVELS)))
ax.set_xticks([0, 0.25, 0.5, 0.75, 1], ["0%", "25%", "50%", "75%", "100%"])
ax.set(xlim=(0, 1), xlabel="share of the reviewed classification's columns", ylabel="reviewed classification")
ax.grid(False)
ax.legend(handles=[Patch(facecolor=f, edgecolor=MUTED, hatch=h, label=name) for name, (f, _, h) in parts.items()], loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=3)
save(fig, "model-against-review")
