"""Counts the tables that carry learner_id and how many enrollments one join resolves to an email, then asks the access
policy who sees learners' names and also reads another table's learner_id. The prose's point rests on the enrollments
line and the last line's list of users."""
from lib import catalog, metadata, policy, warehouse

described = metadata.load()
carrying = {name: table.column("learner_id") for name, table in described.items() if table.column("learner_id")}   # column() gives None when absent
print("tables with a learner_id column:", len(carrying))
print("learner_id classified as:", ", ".join(sorted({column.classification for column in carrying.values()})))
personal = [c.name for c in described["crm.learners"].columns.values() if c.classification == "personal"]
print("personal columns in crm.learners:", ", ".join(personal))

con = warehouse.connect(catalog.open_catalog(["crm.learners", "learning.enrollments"]))
found, total = con.execute("""
    -- the left join keeps every enrollment; count(l.email) counts only those whose learner has an email
    select count(l.email), count(*)
    from learning.enrollments e left join crm.learners l on l.learner_id = e.learner_id
""").fetchall()[0]                              # fetchall returns a list of rows, and this query has exactly one
print("enrollments whose learner_id leads to an email:", f"{found:,} of {total:,}")

# Who can put a name on another table's learner_id: users who read that table and see crm.learners' names unmasked.
rego, seeing, naming = policy.interpreter(), [], []
for user in policy.data_document()["platform"]["users"]:
    names = policy.decide(policy.request(user, "crm.learners", ["full_name"]), rego)
    if names["allow"] and "full_name" not in names["column_masks"]:          # allowed, and not masked: the name shows in clear
        seeing.append(user)
        tables = [t for t in carrying if t != "crm.learners"                 # the other tables with learner_id this user may read
                  and policy.decide(policy.request(user, t, ["learner_id"]), rego)["allow"]]
        naming += [f"{user} ({', '.join(tables)})"] if tables else []
print("users who see learners' names in crm.learners:", ", ".join(seeing))
print("of them, users who read learner_id in another table:", ", ".join(naming))
