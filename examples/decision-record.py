from lib import audit
from lib.catalog import open_catalog
from lib.enforce import Denied
from lib.platform import scratch

# Chapter 6's enforcement layer, wrapped so that every query it's given is recorded before DuckDB runs anything.
log = audit.Log(scratch() / "audit.jsonl")
platform = audit.Audited(open_catalog(["crm.learners", "learning.reviews"]), log)

# instructor-17 asks the course assistant how their reviews look. The assistant writes the SQL; the query runs as the
# instructor, with the assistant recorded as the one that sent it.
question = "select course_id, count(*) as reviews, round(avg(rating), 2) as rating from learning.reviews group by all"
platform.sql(question, user="instructor-17", via="assistant-1").fetchall()

# Then the assistant tries to put names to the ratings, which the instructor may not see.
try:
    platform.sql("select l.full_name, r.rating from learning.reviews as r join crm.learners as l using (learner_id)",
                 user="instructor-17", via="assistant-1").fetchall()
except Denied:
    pass

for record in log.records():
    print(f"record {record['seq']}:")
    print(audit.show(record))
