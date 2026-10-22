import sqlglot

from lib.catalog import open_catalog
from lib.enforce import Enforcer

platform = Enforcer(open_catalog(["learning.reviews"]))

# instructor-17 reads the low ratings of their courses' reviews. The policy filters reviews to their six courses and
# shows `body` with contact details removed.
query = "select course_id, rating, body from learning.reviews where rating <= 2"
rewritten = platform.rewrite(query, "instructor-17")
print(sqlglot.transpile(rewritten, read="duckdb", write="duckdb", pretty=True)[0])
print("reviews returned:", len(platform.run(rewritten).fetchall()))
print("of them holding an email:", platform.run(f"select count(*) from ({rewritten}) where body like '%@%'").fetchall()[0][0])
