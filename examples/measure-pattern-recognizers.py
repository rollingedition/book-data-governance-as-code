"""Runs Presidio with its defaults over every ticket and review, and scores its detections against the truth's labelled
spans. The prose rests on the recall and precision lines, the count of texts left with nothing personal, and the two
date lines that test the truth's limits."""
import re

from lib import pii

labelled = pii.truth()
print("texts:", f"{sum(len(pii.texts(source)) for source in pii.SOURCES):,}")
print("labelled spans:", f"{len(labelled):,}")

detector = pii.detector()                 # Presidio's defaults: every recognizer, threshold 0
found = pii.find(detector)
# scoring needs a label of the span's type; coverage doesn't, and masking needs coverage
scores, covered = pii.score(found, labelled), pii.coverage(found, labelled)
print("detections:", f"{len(found):,}")

for entity, (n, exact, overlap) in scores.recall.items():
    print(f"{entity}, labelled: {n:,}")
    print(f"{entity}, recall, exact: {exact / n:.2f}")
    print(f"{entity}, recall, overlap: {overlap / n:.2f}")
    print(f"{entity}, covered, any label: {covered[entity][1] / n:.2f}")    # covered[entity] is (spans, covered), so n is the same count

for label, (n, exact, overlap) in scores.precision.items():     # precision is per label: what each detection was called
    print(f"{label}, detections: {n:,}")
    print(f"{label}, precision, exact: {exact / n:.2f}")

print("precision of all detections, exact:", f"{pii.precision_overall(scores):.2f}")
print("texts holding personal data:", f"{scores.texts:,}")
print("texts with every labelled span masked:", f"{scores.masked:,}")

# The truth's limits. The generator builds every text from a few sentence templates, and writes every date of birth
# one way. Presidio's date patterns don't read every way of writing one.
print("sentence templates the texts are built from:", len(pii.templates()))
births = [pii.text_of(s) for s in labelled if s.entity == "DATE_TIME"]    # the truth's dates are the dates of birth: dates of a problem are unlabelled
print("dates of birth written like 1985-03-12:", sum(re.fullmatch(r"\d{4}-\d\d-\d\d", d) is not None for d in births))   # year first, the generator's one form
for written in ("1985-03-12", "12 March 1985"):
    text = f"My date of birth on the account is wrong, it should be {written}."     # a sentence in the generator's shape
    print(f"dates found in a date of birth written {written}:", sum(e == "DATE_TIME" for _, _, e, _ in pii.detect(detector, text)))
