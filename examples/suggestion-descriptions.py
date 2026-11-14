"""Prints the recorded description beside the reviewed one for four columns, where the review knows what a name, a type
and five values can't say. The prose's point rests on the pairs themselves, read one after another."""
from lib import metadata

reviewed = metadata.load()
suggested = {(s.table, s.column): s for s in metadata.suggestions()}   # keyed by name, so the four columns below can be named

# Four columns where the platform knows more than a name, a type and five values say.
for table, column in [("crm.learners", "ai_training_consent"), ("crm.learners", "support_region"),
                      ("crm.learners", "notes"), ("billing.payments", "card_number")]:
    print(f"{table}.{column}, the model:", suggested[table, column].description)
    print(f"{table}.{column}, the review:", reviewed[table].column(column).description)   # looked up by name: the file keys columns by field ID, but `column()` finds one by name
