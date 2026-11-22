"""Draws what a change to learning.courses.category reaches: a solid line to each column its values are copied into, and a
dashed line to each table whose rows it shapes. The prose rests on the per-table counts printed at the end."""
from lib import lineage
from lib.catalog import open_catalog
from lib.figures import ACCENT, INK, MUTED, new_figure, save
from lib.platform import scratch
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

events_file = scratch() / "events.jsonl"
lineage.run_all(open_catalog(), events_file)
graph = lineage.graph(lineage.read_events(events_file))    # the same column graph column-impact walks
reached = lineage.downstream(graph, "learning.courses.category")
assert all(edge.source == "learning.courses.category" for edge in reached)    # one hop only: a deeper walk fails here

fig, ax = new_figure(height=4.6)
ax.set(xlim=(0, 10), ylim=(1, 9.6), xticks=[], yticks=[], frame_on=False)
ax.text(0.3, 6.6, "learning.courses.category", va="center", color=INK)
ends, y = {}, 9.2                                    # where each line lands: a table's title or a column's row, at the box's left edge
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
for target in sorted({edge.target for edge in reached}):    # one line per target, labelled with the subtypes into it
    found = [edge for edge in reached if edge.target == target]
    direct = found[0].type == "DIRECT"
    x, y_end = ends[target]
    ax.plot([4.3, x], [6.6, y_end], color=ACCENT if direct else MUTED, linestyle="-" if direct else "--")
    ax.text(9.8, y_end, ", ".join(sorted({e.subtype for e in found})), ha="right", va="center", fontsize=8,
            color=ACCENT if direct else MUTED)
ax.legend(handles=[Line2D([], [], color=ACCENT, label="DIRECT, the values are copied"),
                   Line2D([], [], color=MUTED, linestyle="--", label="INDIRECT, every row")], loc="lower left")
save(fig, "category-reach")
for table in sorted({edge.target for edge in reached if edge.type == "INDIRECT"}):
    rows = [edge for edge in reached if edge.target == table]
    # a table target counts every one of its columns, not only the ones a value is copied into
    print(f"{table}, columns reached through its rows:", len(lineage.columns_reached(graph, rows)))
