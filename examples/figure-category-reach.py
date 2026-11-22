"""Draw what a change to learning.courses.category reaches: the columns its values are copied into, as solid edges, and
the tables whose rows it shapes, as dashed ones. The prose rests on the two counts printed at the end."""
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

from lib import lineage
from lib.catalog import open_catalog
from lib.figures import ACCENT, INK, MUTED, new_figure, save
from lib.platform import scratch

events_file = scratch() / "events.jsonl"
lineage.run_all(open_catalog(), events_file)
graph = lineage.graph(lineage.read_events(events_file))    # the same column graph column-impact walks
reached = lineage.downstream(graph, "learning.courses.category")

fig, ax = new_figure(height=4.6)
ax.set(xlim=(0, 10), ylim=(1, 9.6), xticks=[], yticks=[], frame_on=False)
ax.text(0.3, 6.6, "learning.courses\ncategory", va="center", color=INK)
ends, y, landed = {}, 9.2, {}                                # where each target meets the right side
for table in sorted({lineage.table_of(edge.target) for edge in reached}):
    names = sorted(graph.columns[table])
    height = 0.5 * len(names) + 0.5
    ax.add_patch(Rectangle((5.0, y - height), 4.9, height, fill=False, edgecolor=MUTED, linewidth=0.8))
    ax.text(5.15, y - 0.25, table, va="center", color=MUTED, fontweight="bold", fontsize=9)
    ends[table] = (5.0, y - 0.25)
    for i, name in enumerate(names):
        ends[f"{table}.{name}"] = (5.0, y - 0.75 - 0.5 * i)
        ax.text(5.3, y - 0.75 - 0.5 * i, name, va="center", color=INK)
    y -= height + 0.4
for edge in reached:
    x, y_end = ends[edge.target]
    y_end -= 0.2 * landed.get(edge.target, 0)                # two edges into one table land one under the other
    landed[edge.target] = landed.get(edge.target, 0) + 1
    direct = edge.type == "DIRECT"
    ax.plot([2.5, x], [6.6, y_end], color=ACCENT if direct else MUTED, linestyle="-" if direct else "--")
for target in sorted({edge.target for edge in reached}):     # each target carries the subtypes of the edges into it
    found = [edge for edge in reached if edge.target == target]
    ax.text(9.8, ends[target][1], ", ".join(sorted({e.subtype for e in found})), ha="right", va="center", fontsize=8,
            color=ACCENT if found[0].type == "DIRECT" else MUTED)
ax.legend(handles=[Line2D([], [], color=ACCENT, label="solid: DIRECT, the values are copied"),
                   Line2D([], [], color=MUTED, linestyle="--", label="dashed: INDIRECT, every row")], loc="lower left")
save(fig, "category-reach")
print("learning.courses.category, columns reached through values:",
      len(lineage.columns_reached(graph, [edge for edge in reached if edge.type == "DIRECT"])))
print("learning.courses.category, columns reached in all:", len(lineage.columns_reached(graph, reached)))
