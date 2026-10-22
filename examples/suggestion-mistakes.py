from lib import metadata

reviewed = metadata.load()
rank = metadata.LEVELS.index
weaker, stronger = [], []
for s in metadata.suggestions():
    if s.classification not in metadata.LEVELS:                    # an answer nobody can act on goes to a person
        continue
    column = reviewed[s.table].columns[s.field_id]
    if rank(s.classification) < rank(column.classification):
        weaker.append((s, column.classification))
    elif rank(s.classification) > rank(column.classification):
        stronger.append((s, column.classification))
print("columns the model protects less than the review:", len(weaker))
print("columns the model protects more than the review:", len(stronger))

# Each kind of miss, the most protected review first.
for misses in (weaker, stronger):
    for was, said in sorted({(was, s.classification) for s, was in misses}, key=lambda p: (-rank(p[0]), -rank(p[1]))):
        names = [f"{s.table}.{s.column}" for s, w in misses if (w, s.classification) == (was, said)]
        print(f"{was} columns the model called {said}:", len(names))
        print(f"which, {was} called {said}:", ", ".join(names))
