from collections import Counter

from lib import pii

model, _ = pii.recorded("pii-entities")
labelled = pii.truth()
cases = pii.hard_cases(labelled)

for case, spans in cases.items():
    print(f"{case}, labelled: {len(spans)}")
    print(f"{case}, caught by the model: {pii.caught(spans, model)}")
for kind, spans in pii.lookalikes().items():
    print(f"{kind}, flagged by the model: {pii.caught(spans, model)}")

# The names the model misses, by case: read them before trusting a recall figure.
missed = pii.missed([s for s in labelled if s.entity == "PERSON"], model, {"private_person"})
print("names the model misses:", len(missed))
for case in ("first names that are words", "instructors named in reviews"):
    often = Counter(pii.text_of(s) for s in pii.missed(cases[case], model, {"private_person"})).most_common(3)
    print(f"{case}, missed most often:", ", ".join(name for name, _ in often))

# A first name on its own says less than a full name: how many of the misses are one word?
print("missed names that are one word:", sum(len(pii.text_of(s).split()) == 1 for s in missed))
print("missed names of two words or more:", sum(len(pii.text_of(s).split()) > 1 for s in missed))
print("of them, typed in lowercase:", sum(len(pii.text_of(s).split()) > 1 and pii.text_of(s).islower() for s in missed))

# A lowercase name that neither the model nor the platform's patterns cover any part of, in the first ticket with one.
first = min(pii.missed(cases["names in lowercase"], model + pii.find(pii.platform_detector())), key=lambda s: s.row_id)
print("first ticket with a lowercase name both detectors miss:", first.row_id)
print("the name, as typed:", pii.text_of(first))
