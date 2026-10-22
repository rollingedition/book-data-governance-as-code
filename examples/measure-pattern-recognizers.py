import re

from lib import pii

labelled = pii.truth()
print("texts:", f"{sum(len(pii.texts(source)) for source in pii.SOURCES):,}")
print("labelled spans:", f"{len(labelled):,}")

detector = pii.detector()                 # Presidio's defaults: every recognizer, threshold 0
found = pii.find(detector)
scores, covered = pii.score(found, labelled), pii.coverage(found, labelled)
print("detections:", f"{len(found):,}")

for entity, (n, exact, overlap) in scores.recall.items():
    print(f"{entity}, labelled: {n:,}")
    print(f"{entity}, recall, exact: {exact / n:.2f}")
    print(f"{entity}, recall, overlap: {overlap / n:.2f}")
    print(f"{entity}, covered, any label: {covered[entity][1] / n:.2f}")

for label, (n, exact, overlap) in scores.precision.items():
    print(f"{label}, detections: {n:,}")
    print(f"{label}, precision, exact: {exact / n:.2f}")

print("precision of all detections, exact:", f"{pii.precision_overall(scores):.2f}")
print("texts holding personal data:", f"{scores.texts:,}")
print("texts with every labelled span masked:", f"{scores.masked:,}")

# The truth's limits. The generator builds every text from a few sentence templates, and writes every date of birth
# one way. Presidio's date patterns don't read every way of writing one.
print("sentence templates the texts are built from:", len(pii.templates()))
births = [pii.text_of(s) for s in labelled if s.entity == "DATE_TIME"]
print("dates of birth written like 1985-03-12:", sum(re.fullmatch(r"\d{4}-\d\d-\d\d", d) is not None for d in births))
for written in ("1985-03-12", "12 March 1985"):
    text = f"My date of birth on the account is wrong, it should be {written}."
    print(f"dates found in a date of birth written {written}:", sum(e == "DATE_TIME" for _, _, e, _ in pii.detect(detector, text)))
