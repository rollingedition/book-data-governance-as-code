"""The data the policy reads besides the request, built from the files that own each fact. The point rests on the last two lines: instructor 17's courses come from learning.courses, not from anything typed."""
from lib import policy

# What the policy reads besides the request, built from the files that own each fact.
# the document's one top-level key, which the policy reads as data.platform
data = policy.data_document()["platform"]

print("users, from governance/users.toml:", len(data["users"]))
print("agent-a05:", data["users"]["agent-a05"])
print("instructor-17:", data["users"]["instructor-17"])

classifications = data["classifications"]
print("tables, from governance/metadata.toml:", len(classifications))
print("columns classified:", sum(len(columns) for columns in classifications.values()))
print("crm.learners.email:", classifications["crm.learners"]["email"])
print("billing.payments.card_number:", classifications["billing.payments"]["card_number"])

courses = data["instructor_courses"]
print("instructors with courses, from learning.courses:", len(courses))
# instructor IDs are string keys in the document, so the lookup is "17"
print("instructor 17's courses:", courses["17"])
