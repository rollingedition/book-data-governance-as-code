"""Counts each hard case the tickets and reviews plant, and each look-alike number or date the platform's detector flags,
then checks the card and phone numbers it misses. The prose rests on the caught counts for names and invoice numbers,
and on the misses' lengths, first digits and validity."""
from collections import Counter

from lib import pii
from lib.platform import load

labelled = pii.truth()
found = pii.find(pii.platform_detector())

cases = pii.hard_cases(labelled)               # a case is caught when some detection overlaps it, whatever its label
for case, spans in cases.items():
    print(f"{case}, labelled: {len(spans)}")
    print(f"{case}, caught: {pii.caught(spans, found)}")
print("hard cases of a name, caught:", pii.caught([s for spans in cases.values() for s in spans if s.entity == "PERSON"], found))

# Look-alikes are numbers and dates that aren't personal data, so any detection on one is a false alarm.
for kind, spans in pii.lookalikes().items():
    print(f"{kind}, in the tickets: {len(spans)}")
    print(f"{kind}, flagged as personal data: {pii.caught(spans, found)}")

# Card numbers the card pattern misses: too short or long for its 13 to 17 digits, or a first digit it doesn't accept.
# Out of range, the length is the reason; in range, the first digit is, since the pattern accepts only some of them.
missed = pii.missed([s for s in labelled if s.entity == "CREDIT_CARD"], found, {"CREDIT_CARD"})
why = lambda digits: f"{len(digits)} digits" if not 13 <= len(digits) <= 17 else f"{len(digits)} digits starting with {digits[0]}"
print("CREDIT_CARD, missed:", len(missed))
for reason, n in Counter(why(pii.text_of(s)) for s in missed).most_common():
    print(f"CREDIT_CARD, missed, {reason}: {n}")
print("IBAN_CODE, missed:", len(pii.missed([s for s in labelled if s.entity == "IBAN_CODE"], found, {"IBAN_CODE"})))

# Are the phone numbers the patterns miss valid where the ticket's learner lives? Phones appear only in tickets,
# so each phone's learner comes from its ticket.
learner_of = {row["ticket_id"]: row["learner_id"] for row in load("support.tickets").to_pylist()}
country_of = {row["learner_id"]: row["country"] for row in load("crm.learners").to_pylist()}
phones = pii.missed([s for s in labelled if s.entity == "PHONE_NUMBER"], found, {"PHONE_NUMBER"})
print("phone numbers missed:", len(phones))
print("of them, invalid in the learner's country:",
      sum(not pii.valid_phone(pii.text_of(s), country_of[learner_of[s.row_id]]) for s in phones))
