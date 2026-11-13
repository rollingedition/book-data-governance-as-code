"""Loads Presidio's predefined recognizers on an engine that loads no language model, and runs them on one support
ticket. The prose's point rests on the detections printed under the ticket, and on the assert that the model isn't
installed."""
import spacy

from lib import pii
from lib.platform import load

# AnalyzerEngine() with no arguments loads spaCy's en_core_web_lg, and downloads it when it isn't installed.
installed = spacy.util.is_package("en_core_web_lg")
assert installed is False       # the pipeline assumes no model: a machine that has one fails here, not silently
print("en_core_web_lg installed:", installed)
print("URLs tldextract would fetch the Public Suffix List from:", len(pii.suffix_list_urls()))

detector = pii.detector()              # every predefined recognizer, on an NLP engine that loads no model
names = sorted(r.name for r in detector.registry.recognizers)
print("recognizers loaded:", len(names))
print("recognizers:", ", ".join(names))
print("default score threshold:", detector.default_score_threshold)

# The ticket holds a phone number, which may be in service: masked() prints it with only its last four digits.
ticket = pii.texts("tickets")[1421]         # one ticket with a name, an email, a phone number and a date of the problem
found = sorted(pii.detect(detector, ticket))
print("ticket 1421:", pii.masked(ticket))
for start, end, entity, score in found:
    print(f"  {entity} on {pii.masked(ticket[start:end])}: {score:.2f}")

# Where is the phone number valid? The recognizer reads it in each of its regions.
phone = next(ticket[start:end] for start, end, entity, _ in found if entity == "PHONE_NUMBER")
[learner] = [row["learner_id"] for row in load("support.tickets").to_pylist() if row["ticket_id"] == 1421]
[country] = [row["country"] for row in load("crm.learners").to_pylist() if row["learner_id"] == learner]
print("the learner's country:", country)
# The recognizer's built-in regions, not the learner's country.
print("default regions where it's valid:",
      ", ".join(r for r in pii.PhoneRecognizer.DEFAULT_SUPPORTED_REGIONS if pii.valid_phone(phone, r)))
