from lib import pii

# What the model's labels cost. A span is covered when every character of it is inside some detection, whatever its
# label: that's what masking needs. Exact and overlap matching also need a label of the span's type.
model, _ = pii.recorded("pii-entities")
labelled = pii.truth()
covered = pii.coverage(model, labelled)
for entity in ("CREDIT_CARD", "PHONE_NUMBER", "LOCATION"):
    n, fully = covered[entity]
    print(f"{entity}, covered by the model, any label: {fully / n:.2f}")

spans = {entity: [s for s in labelled if s.entity == entity] for entity in ("CREDIT_CARD", "PHONE_NUMBER", "LOCATION")}
print("card numbers labelled private_phone:", pii.caught(spans["CREDIT_CARD"], model, {"private_phone"}))
print("phone numbers labelled account_number:", pii.caught(spans["PHONE_NUMBER"], model, {"account_number"}))

# For each address, the labels of the detections that overlap it, and the characters no detection covers.
on = pii.labels_on(spans["LOCATION"], model)
print("addresses with no detection at all:", sum(not labels for labels in on))
print("addresses read, at least in part, as a person:", sum("private_person" in labels for labels in on))
print("of them, with no address label:", sum({"private_person"} <= labels and "private_address" not in labels for labels in on))
left = [pii.uncovered(address, model) for address in spans["LOCATION"]]
print("addresses not fully covered:", sum(bool(chars) for chars in left))
print("of them, with only spaces and punctuation left:", sum(bool(chars) and not any(c.isalnum() for c in chars) for chars in left))
