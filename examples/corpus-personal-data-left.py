"""Counts the labelled names and emails still in the reviews after each step of the corpus job, and the false positives of the instructors' list and of a longer one. The prose's point rests on the "then the instructors' names, PERSON left" line and the false-positive lines."""
from lib import ai_data, pii
from lib.catalog import open_catalog
from lib.enforce import Enforcer

# The reviews the corpus job reads: consenting authors only, with emails and phone numbers already masked.
rows = ai_data.read(Enforcer(open_catalog(["crm.learners", "learning.courses", "learning.reviews"])))
texts = {row["review_id"]: row["body"] for row in rows}    # keyed by review ID, to match the labelled values row by row

# What's left of the labelled personal data after each step, matched by value against the generator's truth.
detectors = {"then the pattern recognizers": pii.platform_detector(), "then the instructors' names": ai_data.detector()}
# the texts as the enforcement layer leaves them, before any detector runs
stages = {"masked by the enforcement layer": texts}
# each detector runs alone on the masked texts; pseudonymize returns the text first
stages |= {name: {i: ai_data.pseudonymize(engine, text)[0] for i, text in texts.items()} for name, engine in detectors.items()}
for stage, done in stages.items():
    for entity, (placed, still) in ai_data.left(done).items():
        print(f"{stage}, {entity} left: {still} of {placed}")

# the instructors' words, from the governance team's list
names = ai_data.known_names()
print("words in the list of instructors' names:", len(names))
# short words are the ones ordinary text could contain
print("words in the list of 3 characters or fewer:", ", ".join(word for word in names if len(word) <= 3))
false_positives = sum(ai_data.unlabelled(ai_data.detector(), texts).values())
assert false_positives == 0
print("false positives, instructors' names:", false_positives)

# Every name the platform holds, learners' too, on the same reviews: in any case, then only as written.
# the union of the instructors' words and every learner's, which the table holds
longer = sorted(set(names) | set(ai_data.known_names("crm.learners")))
# matched in any case, as the platform's detector matches by default
everyone = ai_data.detector(longer)
wrong = ai_data.unlabelled(everyone, texts)
print("words in the list, with learners' names:", f"{len(longer):,}")
print("false positives, with learners' names:", sum(wrong.values()))
print("most common false positives:", ", ".join(f"{word} ({n})" for word, n in wrong.most_common(2)))
# review 24, which the longer list changes
print("a review after:", ai_data.pseudonymize(everyone, texts[24])[0])
# a word matches only as written, so a lowercase word no longer matches a capitalized name
cased = ai_data.detector(longer, match_case=True)
cased_wrong = sum(ai_data.unlabelled(cased, texts).values())
assert cased_wrong == 0
print("false positives, with learners' names, matching case:", cased_wrong)
# pseudonymize with the cased detector, then count what is left by value
left = ai_data.left({i: ai_data.pseudonymize(cased, text)[0] for i, text in texts.items()})
assert left["PERSON"][1] == 0
print("names left, with learners' names, matching case:", f"{left['PERSON'][1]} of {left['PERSON'][0]}")
