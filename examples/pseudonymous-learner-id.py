from lib import catalog, metadata, policy, warehouse

described = metadata.load()
carrying = {name: table.column("learner_id") for name, table in described.items() if table.column("learner_id")}
print("tables with a learner_id column:", len(carrying))
print("learner_id classified as:", ", ".join(sorted({column.classification for column in carrying.values()})))
personal = [c.name for c in described["crm.learners"].columns.values() if c.classification == "personal"]
print("personal columns in crm.learners:", ", ".join(personal))

con = warehouse.connect(catalog.open_catalog(["crm.learners", "learning.enrollments"]))
found, total = con.execute("""
    select count(l.email), count(*)
    from learning.enrollments e left join crm.learners l on l.learner_id = e.learner_id
""").fetchall()[0]
print("enrollments whose learner_id leads to an email:", f"{found:,} of {total:,}")

# Who can put a name on another table's learner_id: users who read that table and see crm.learners' names unmasked.
rego, seeing, naming = policy.interpreter(), [], []
for user in policy.data_document()["platform"]["users"]:
    names = policy.decide(policy.request(user, "crm.learners", ["full_name"]), rego)
    if names["allow"] and "full_name" not in names["column_masks"]:
        seeing.append(user)
        tables = [t for t in carrying if t != "crm.learners"
                  and policy.decide(policy.request(user, t, ["learner_id"]), rego)["allow"]]
        naming += [f"{user} ({', '.join(tables)})"] if tables else []
print("users who see learners' names in crm.learners:", ", ".join(seeing))
print("of them, users who read learner_id in another table:", ", ".join(naming))
