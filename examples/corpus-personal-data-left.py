from lib import ai_data, pii
from lib.catalog import open_catalog
from lib.enforce import Enforcer

# The reviews the corpus job reads: consenting authors only, with emails and phone numbers already masked.
rows = ai_data.read(Enforcer(open_catalog(["crm.learners", "learning.courses", "learning.reviews"])))
texts = {row["review_id"]: row["body"] for row in rows}

# What's left of the labelled personal data after each step, matched by value against the generator's truth.
detectors = {"then the pattern recognizers": pii.platform_detector(), "then the instructors' names": ai_data.detector()}
stages = {"masked by the enforcement layer": texts}
stages |= {name: {i: ai_data.pseudonymize(engine, text)[0] for i, text in texts.items()} for name, engine in detectors.items()}
for stage, done in stages.items():
    for entity, (placed, still) in ai_data.left(done).items():
        print(f"{stage}, {entity} left: {still} of {placed}")

names = ai_data.known_names()
print("words in the list of instructors' names:", len(names))
print("words in the list of 3 characters or fewer:", ", ".join(word for word in names if len(word) <= 3))
print("false positives, instructors' names:", sum(ai_data.unlabelled(ai_data.detector(), texts).values()))

# Every name the platform holds, learners' too, on the same reviews: in any case, then only as written.
longer = sorted(set(names) | set(ai_data.known_names("crm.learners")))
everyone = ai_data.detector(longer)
wrong = ai_data.unlabelled(everyone, texts)
print("words in the list, with learners' names:", f"{len(longer):,}")
print("false positives, with learners' names:", sum(wrong.values()))
print("most common false positives:", ", ".join(f"{word} ({n})" for word, n in wrong.most_common(2)))
print("a review after:", ai_data.pseudonymize(everyone, texts[24])[0])
cased = ai_data.detector(longer, match_case=True)
print("false positives, with learners' names, matching case:", sum(ai_data.unlabelled(cased, texts).values()))
left = ai_data.left({i: ai_data.pseudonymize(cased, text)[0] for i, text in texts.items()})
print("names left, with learners' names, matching case:", f"{left['PERSON'][1]} of {left['PERSON'][0]}")
