from lib.catalog import open_catalog
from lib.enforce import Enforcer, row_filter

platform = Enforcer(open_catalog(["crm.learners", "learning.reviews", "support.tickets"]))

# Each row filter in a decision, as the condition the enforcement layer writes for it.
for user, table in [("agent-a05", "support.tickets"), ("instructor-17", "learning.reviews"), ("assistant-1", "learning.reviews")]:
    decision = platform.decide(user, {table: set()})[table]
    for rule in decision["row_filters"]:
        print(f"{user}, {table}: {row_filter(rule, platform.columns(table)).sql('duckdb')}")

# A filter's values come from data, so a value that looks like SQL stays a value.
odd = {"column": "support_region", "equals": "emea' or '1' = '1"}
print("a region that looks like SQL:", row_filter(odd, platform.columns("support.tickets")).sql("duckdb"))

# The assistant's consent filter reads crm.learners, which the assistant itself may not read.
total = platform.connection.sql("select count(*) from learning.reviews").fetchall()[0][0]
read = platform.sql("select count(*) from learning.reviews", "assistant-1").fetchall()[0][0]
print("reviews the assistant reads:", f"{read:,} of {total:,}")
by_consent = platform.connection.sql("""select coalesce(l.ai_training_consent::varchar, 'never asked'), count(*)
    from learning.reviews r join crm.learners l using (learner_id) group by all order by all""").fetchall()
for consent, reviews in by_consent:
    print(f"reviews whose author's AI-training consent is {consent}: {reviews:,}")
try:
    platform.sql("select count(*) from crm.learners", "assistant-1")
except Exception as reason:
    print("the assistant reading crm.learners:", reason)
