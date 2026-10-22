from lib import pii

# Privacy Filter's eight labels, mapped to the truth's entity types. A label the truth has no type for matches nothing.
MATCHES = {"private_person": {"PERSON"}, "private_email": {"EMAIL_ADDRESS"}, "private_phone": {"PHONE_NUMBER"},
           "private_address": {"LOCATION"}, "account_number": {"CREDIT_CARD", "IBAN_CODE"},
           "private_date": {"DATE_TIME"}, "private_url": set(), "secret": set()}

model, manifest = pii.recorded("pii-entities")
print("model:", manifest["model"], "decoded with", manifest["settings"]["decode_mode"])
labelled = pii.truth()
detectors = {"patterns": pii.find(pii.platform_detector()), "model": model}
detectors["both"] = detectors["patterns"] + model          # mask whatever either one finds

for name, found in detectors.items():
    scores = pii.score(found, labelled, MATCHES)           # Presidio's labels aren't in MATCHES, so match themselves
    print(f"{name}, detections: {len(found):,}")
    for entity, (n, exact, overlap) in scores.recall.items():
        print(f"{name}, {entity}, recall, exact: {exact / n:.2f}")
        print(f"{name}, {entity}, recall, overlap: {overlap / n:.2f}")
    print(f"{name}, precision, exact: {pii.precision_overall(scores):.2f}")
    print(f"{name}, texts with every labelled span masked: {scores.masked:,} of {scores.texts:,}")

for label, (n, exact, overlap) in pii.score(model, labelled, MATCHES).precision.items():
    print(f"model, {label}, detections: {n:,}")
    print(f"model, {label}, precision, exact: {exact / n:.2f}")
    print(f"model, {label}, precision, overlap: {overlap / n:.2f}")
