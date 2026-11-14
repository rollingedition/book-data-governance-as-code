"""An erased learner comes back: the nightly reload of crm.learners from the application's export brings the rows back, and
the pipelines rebuild from them. A check against the erasure register catches the rows."""
from datetime import timedelta

from lib import pipelines, retention

LEARNER, REQUEST = 3590, "erasure-0001"
catalog, graph = retention.open_platform()
retention.erase_everywhere(catalog, graph, LEARNER, REQUEST, min_age=timedelta(0))
register = [retention.register_entry(REQUEST, LEARNER)]      # the request's ID and a keyed hash of the learner's ID
print("the erasure register's entry:", register[0]["request"], register[0]["learner"])
learners = catalog.load_table("crm.learners")
print("the learner's rows in crm.learners after the erasure:", retention.count(learners, retention.rows_of([LEARNER])))

# the nightly reload: the application's export still holds the learner, since nobody erased it there
learners.overwrite(retention.crm_export())
# the pipelines that read crm.learners rebuild from the reloaded table
for name in ("learner_activity", "marketing_audience"):
    pipelines.run(catalog, name)
for table in ("crm.learners", "analytics.learner_activity", "marketing.audience"):
    print(f"the learner's rows in {table} after the nightly run:",
          retention.count(catalog.load_table(table), retention.rows_of([LEARNER])))

# hashes each learner_id in every table and looks it up in the register, so no learner's ID is printed
for table, rows in retention.erased_rows(catalog, register).items():
    print(f"the check against the register, rows of erased learners in {table}:", rows)
