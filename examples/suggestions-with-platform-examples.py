"""Scores the recorded answers under two prompts against the reviewed classifications: the book's prompt, whose examples
come from outside the platform, and one whose examples are the platform's own columns. The prose's point rests on
the sensitive-column lines and the agreement counts."""
from lib import metadata

reviewed = metadata.load()
prompts = {"the book's prompt": metadata.SUGGESTIONS,
           "the prompt with the platform's columns as examples": metadata.SUGGESTIONS_WITH_PLATFORM_EXAMPLES}
for name, path in prompts.items():
    # Each recorded answer, paired with the classification the file reviewed for its column.
    suggested = [(s, reviewed[s.table].columns[s.field_id].classification) for s in metadata.suggestions(path)]
    agree = sum(s.classification == was for s, was in suggested)
    print(f"{name}, classifications that agree with the review:", f"{agree} of {len(suggested)}")
    for level in ("personal", "sensitive"):
        reviewed_as = [s for s, was in suggested if was == level]
        print(f"{name}, {level} columns called {level}:",
              f"{sum(s.classification == level for s in reviewed_as)} of {len(reviewed_as)}")
    for s, was in suggested:
        if was == "sensitive":
            print(f"{name}, {s.table}.{s.column}:", s.classification)
