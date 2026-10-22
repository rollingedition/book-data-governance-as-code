from collections import Counter

from lib import ai_data, pii
from lib.catalog import open_catalog
from lib.enforce import Enforcer

# Privacy Filter's eight labels, mapped to the labelled spans' entity types. A label with no type matches nothing.
MATCHES = {"private_person": {"PERSON"}, "private_email": {"EMAIL_ADDRESS"}, "private_phone": {"PHONE_NUMBER"},
           "private_address": {"LOCATION"}, "account_number": {"CREDIT_CARD", "IBAN_CODE"},
           "private_date": {"DATE_TIME"}, "private_url": set(), "secret": set()}

# The reviews the corpus job reads, with the model's recorded detections and the labelled spans in them. The model read
# each review as written, before the enforcement layer's mask, which changes no name.
corpus = {row["review_id"] for row in ai_data.read(Enforcer(open_catalog(["crm.learners", "learning.courses",
                                                                          "learning.reviews"])))}
model, manifest = pii.recorded("pii-entities")
found = [span for span in model if span.source == "reviews" and span.row_id in corpus]
labelled = [span for span in pii.truth() if span.source == "reviews" and span.row_id in corpus]
print("model:", manifest["model"])
print("reviews measured:", len(corpus))

scores = pii.score(found, labelled, MATCHES)
placed, exact, _ = scores.recall["PERSON"]
print("labelled names:", placed)
print("names the model finds, exactly:", exact)
missed = pii.missed([span for span in labelled if span.entity == "PERSON"], found, {"private_person"})
print("names the model misses:", len(missed))
often = Counter(map(pii.text_of, missed)).most_common(3)
print("names it misses most often:", ", ".join(f"{name} ({n})" for name, n in often))

# What the model finds where no labelled span of the type its label maps to is: text it would replace for nothing.
wrong = sum(n - overlap for n, _, overlap in scores.precision.values())
print("the model's detections:", len(found))
print("of them, overlapping no labelled span of their type:", wrong)
