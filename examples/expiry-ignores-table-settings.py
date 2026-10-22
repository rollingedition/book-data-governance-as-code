"""PyIceberg's snapshot expiry ignores two of the table's own settings, gc.enabled and min-snapshots-to-keep. The
book's expiry reads them first."""
from datetime import datetime, timezone

from pyiceberg.expressions import EqualTo

from lib import retention
from lib.catalog import open_catalog


def learners_with(settings: dict):
    """crm.learners with three snapshots: the load, then two deletes."""
    table = open_catalog(["crm.learners"]).load_table("crm.learners")
    with table.transaction() as transaction:
        transaction.set_properties(settings)
    for learner in (3590, 3591):
        table.delete(EqualTo("learner_id", learner))
    return table


for settings in ({"gc.enabled": "false"}, {"history.expire.min-snapshots-to-keep": "3"}):
    setting = ", ".join(f"{key}={value}" for key, value in settings.items())
    table = learners_with(settings)
    print(f"{setting}, snapshots:", len(table.snapshots()))
    try:
        print(f"{setting}, the book's expiry expires:", retention.expire(table))
    except retention.RetentionError as error:
        print(f"{setting}, the book's expiry refuses:", error)
    table.maintenance.expire_snapshots().older_than(datetime.now(timezone.utc)).commit()     # by age, as of now
    print(f"{setting}, snapshots left by PyIceberg's expiry:", len(table.snapshots()))
