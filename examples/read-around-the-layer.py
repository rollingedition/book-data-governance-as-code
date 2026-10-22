"""The same table, read three ways: through the enforcement layer, and around it with DuckDB and with PyIceberg."""
from collections import Counter

from lib import warehouse
from lib.catalog import open_catalog
from lib.enforce import Denied, Enforcer, answer

platform = open_catalog(["crm.learners"])
query = "select support_region, count(*) from crm.learners group by support_region order by support_region"


def show(rows):
    return ", ".join(f"{region} {learners:,}" for region, learners in rows)


layer = Enforcer(platform)
print("through the enforcement layer, as agent-a05:", show(answer(layer, query, "agent-a05")))

# A notebook with DuckDB on the same catalog: the same SQL, and no policy in the way.
print("DuckDB on the same catalog:", show(warehouse.connect(platform).sql(query).fetchall()))

# Any reader that holds the catalog's access, here PyIceberg's own scan: no SQL to rewrite at all.
scan = platform.load_table("crm.learners").scan(selected_fields=("support_region", "email")).to_arrow()
print("PyIceberg's scan of the table:", show(sorted(Counter(scan.column("support_region").to_pylist()).items())))
print("emails PyIceberg's scan returns:", f"{scan.num_rows - scan.column('email').null_count:,}")

# lib.warehouse builds each table's view on a scan it registers under a name of its own. Through the layer, it's denied.
try:
    answer(layer, "select count(*) from scan_crm_learners", "agent-a05")
except Denied as reason:
    print("the layer, asked for the raw scan:", reason)
