"""Compares the recorded model's classification of each source column with the reviewed one, and counts how many agree.
The prose's point rests on the agreement line and on the two lines counting personal and sensitive columns."""
from collections import Counter

from lib import metadata

reviewed = metadata.load()                                   # governance/metadata.toml, reviewed by people
suggested = metadata.suggestions()                           # the recorded answers under the book's prompt: no model runs here
assert all(reviewed[s.table].columns[s.field_id].name == s.column for s in suggested)   # each answer names the file's column at its field ID
print("columns the model was asked about:", len(suggested))
print("answers that parse as the JSON asked for:", sum(s.description is not None for s in suggested))   # None when the answer isn't that JSON
print("answers with one of the four classifications:", sum(s.classification in metadata.LEVELS for s in suggested))

pairs = Counter((reviewed[s.table].columns[s.field_id].classification, s.classification) for s in suggested)
agree = sum(n for (was, said), n in pairs.items() if was == said)
print("classifications that agree with the review:", f"{agree} of {len(suggested)} ({agree / len(suggested):.0%})")
for level in metadata.LEVELS:
    said = sorted(((s, n) for (was, s), n in pairs.items() if was == level), key=lambda p: -p[1])
    print(f"reviewed {level}, the model said:", ", ".join(f"{s} {n}" for s, n in said))
for level in ("personal", "sensitive"):
    reviewed_as = sum(n for (was, _), n in pairs.items() if was == level)
    print(f"{level} columns the model called {level}:", f"{pairs[level, level]} of {reviewed_as}")
