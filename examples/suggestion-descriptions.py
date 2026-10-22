from lib import metadata

reviewed = metadata.load()
suggested = {(s.table, s.column): s for s in metadata.suggestions()}

# Four columns where the platform knows more than a name, a type and five values say.
for table, column in [("crm.learners", "ai_training_consent"), ("crm.learners", "support_region"),
                      ("crm.learners", "notes"), ("billing.payments", "card_number")]:
    print(f"{table}.{column}, the model:", suggested[table, column].description)
    print(f"{table}.{column}, the review:", reviewed[table].column(column).description)
