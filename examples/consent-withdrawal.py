"""A learner withdraws marketing consent: one attribute changes, and marketing's next query returns one learner fewer.
No row is deleted. A table built with the consent filter keeps the learner until its pipeline runs again."""
import json

import pyarrow as pa
from pyiceberg.expressions import EqualTo

from lib import pipelines, policy, retention
from lib.catalog import open_catalog
from lib.enforce import Enforcer

LEARNER = 3590
catalog = open_catalog(["crm.learners", "learning.enrollments", "learning.courses"])
pipelines.run(catalog, "marketing_audience")
decision = policy.decide(policy.request("marketing-1", "crm.learners", ["learner_id"]))
print("marketing's row filters on crm.learners:", json.dumps(decision["row_filters"]))


def report(when: str) -> None:
    seen = [row[0] for row in Enforcer(catalog).sql("select learner_id from crm.learners", "marketing-1").fetchall()]
    print(f"{when}, learners marketing sees:", f"{len(seen):,}")
    print(f"{when}, marketing sees learner {LEARNER}:", LEARNER in seen)
    print(f"{when}, rows stored in crm.learners:", f"{retention.count(catalog.load_table('crm.learners')):,}")
    audience = retention.count(catalog.load_table("marketing.audience"), EqualTo("learner_id", LEARNER))
    print(f"{when}, learner {LEARNER}'s rows in marketing.audience:", audience)


report("before")
learners = catalog.load_table("crm.learners")
row = learners.scan(row_filter=EqualTo("learner_id", LEARNER)).to_arrow()
row = row.set_column(row.schema.get_field_index("marketing_consent"), "marketing_consent", pa.array([False]))
learners.overwrite(row, overwrite_filter=EqualTo("learner_id", LEARNER))     # what the CRM's sync writes
report("after the withdrawal")
pipelines.run(catalog, "marketing_audience")
report("after the audience is built again")
