from collections import Counter

from lib import ai_data, pii
from lib.catalog import open_catalog
from lib.enforce import Enforcer

platform = Enforcer(open_catalog(["crm.learners", "learning.courses", "learning.reviews"]))
layer = platform.connection           # the enforcement layer's own access, for counting what each build would hold

# The plain pipeline copies every review, with its text as written.
plain = layer.sql("select count(*) from learning.reviews").fetchall()[0][0]
print("reviews the plain pipeline copies:", f"{plain:,}")

# The governed build: the same SQL, through the enforcement layer, as the job's principal, for its purpose.
governed = ai_data.read(platform)
print(f"reviews the governed build reads, as {ai_data.BUILD['principal']} for {ai_data.BUILD['purpose']}:", f"{len(governed):,}")
without = ai_data.read(platform, purpose=None)
print("reviews read without the purpose:", f"{len(without):,}")
print("of them, with their text:", sum(r["body"] is not None for r in without))

# The filter that looks right: leave out the learners who said no.
opt_out = layer.sql("""
    select count(*), count(*) filter (where l.ai_training_consent is null)
    from learning.reviews r join crm.learners l using (learner_id)
    where r.learner_id not in (select learner_id from crm.learners where ai_training_consent = false)""").fetchall()[0]
print("reviews a filter on refusals keeps:", f"{opt_out[0]:,}")
print("of them, by learners never asked:", f"{opt_out[1]:,}")
print("share by learners never asked:", f"{opt_out[1] / opt_out[0]:.0%}")
never = layer.sql("""select min(signup_at)::date, max(signup_at)::date from crm.learners
                     where ai_training_consent is null""").fetchall()[0]
print("learners never asked signed up:", " to ".join(f"{day:%b} {day.day}, {day.year}" for day in never))

# Personal data the plain pipeline's corpus would hold, from the labelled truth, and how often each email is written.
raw = dict(layer.sql("select review_id, body from learning.reviews").fetchall())
for entity, (placed, _) in ai_data.left(raw).items():
    print(f"plain pipeline, labelled {entity} values:", f"{placed:,}")
emails = Counter(pii.text_of(s) for s in pii.truth() if s.source == "reviews" and s.entity == "EMAIL_ADDRESS")
print("plain pipeline, distinct labelled EMAIL_ADDRESS values:", len(emails))
print("of them, written once:", sum(n == 1 for n in emails.values()))
