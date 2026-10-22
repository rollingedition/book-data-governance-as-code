from lib import pii

labelled = pii.truth()
print("recognizers kept:", len(pii.THRESHOLDS))
print("recognizers kept, with their thresholds:", ", ".join(f"{name} {t}" for name, t in pii.THRESHOLDS.items()))
print("phone regions, Presidio's default:", ", ".join(pii.PhoneRecognizer.DEFAULT_SUPPORTED_REGIONS))
print("phone regions, the learners' countries:", ", ".join(pii.countries()))

settings = {
    "Presidio's defaults": pii.detector(),
    "one threshold of 0.5": pii.detector(default_threshold=0.5),
    "kept recognizers, own thresholds": pii.detector(pii.THRESHOLDS),
    "kept recognizers, learners' phone regions": pii.platform_detector(),
}
for name, detector in settings.items():
    found = pii.find(detector)
    scores, covered = pii.score(found, labelled), pii.coverage(found, labelled)
    n, exact, _ = scores.recall["PHONE_NUMBER"]
    print(f"{name}, detections: {len(found):,}")
    print(f"{name}, precision, exact: {pii.precision_overall(scores):.2f}")
    print(f"{name}, phone recall, exact: {exact / n:.2f}")
    for entity, short in (("PHONE_NUMBER", "phones"), ("CREDIT_CARD", "cards")):
        print(f"{name}, {short} covered, any label: {covered[entity][1] / covered[entity][0]:.2f}")
    print(f"{name}, texts with every labelled span masked: {scores.masked:,}")
