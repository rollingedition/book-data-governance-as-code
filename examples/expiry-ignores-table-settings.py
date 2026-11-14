"""PyIceberg's snapshot expiry ignores two of the table's own settings, gc.enabled and min-snapshots-to-keep, so it expires
snapshots they would keep. The book's expiry reads both first, and the `left by PyIceberg's expiry` lines show the difference."""
from datetime import datetime, timezone

from pyiceberg.expressions import EqualTo

from lib import retention
from lib.catalog import open_catalog


def learners_with(settings: dict):
    """crm.learners with three snapshots: the load, then two deletes."""
    table = open_catalog(["crm.learners"]).load_table("crm.learners")
    with table.transaction() as transaction:
        transaction.set_properties(settings)
    # two deletes, so there are snapshots from before them for the expiry to consider
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
    # PyIceberg's expiry by age, as of now: it reads neither setting
    table.maintenance.expire_snapshots().older_than(datetime.now(timezone.utc)).commit()
    print(f"{setting}, snapshots left by PyIceberg's expiry:", len(table.snapshots()))
