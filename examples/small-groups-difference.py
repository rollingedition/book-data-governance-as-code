from lib.catalog import open_catalog
from lib.enforce import Enforcer, minimum_group_size

platform = Enforcer(open_catalog(["crm.learners", "learning.courses", "learning.enrollments"]))
minimum = minimum_group_size()
print("minimum group size:", minimum)
count = lambda sql: platform.connection.sql(f"select count(*) from ({sql})").fetchall()[0][0]

# The platform's daily course report for instructor-17: learners and average progress, per course and learner country.
# The report job reads learners' countries, which instructors may not read.
platform.connection.execute("""create view mine as
    select e.course_id, e.progress_pct, e.enrolled_at::date as day, l.country
    from learning.enrollments e join crm.learners l using (learner_id)
    where e.course_id in (select course_id from learning.courses where instructor_id = 17)""")
platform.connection.execute("""create view report as
    select course_id, country, count(*) as learners, avg(progress_pct) as progress from mine group by all""")
print("cells in the report:", count("from report"))
print("cells under the minimum:", count(f"from report where learners < {minimum}"))

# instructor-17 may read their courses' enrollments, with each one's progress. A cell of one learner, matched on its
# progress, names that learner's country.
seen = platform.sql("select course_id, progress_pct from learning.enrollments", "instructor-17").to_arrow_table()
platform.connection.register("seen", seen)
print("cells of one learner:", count("from report where learners = 1"))
print("learners a cell of one names:", count("""from report as cell where learners = 1 and (select count(*) from seen
    where seen.course_id = cell.course_id and seen.progress_pct = cell.progress) = 1"""))
print("cells shown with the minimum:", count(f"from report where learners >= {minimum}"))

# The minimum doesn't stop differencing. An enrollment that arrives alone in its course on its day, in a cell already
# shown, raises that cell by one in the next day's report, and the instructor sees which enrollment arrived.
revealed = count(f"""from mine as m
    where (select count(*) from mine as o where o.course_id = m.course_id and o.day = m.day) = 1
      and (select count(*) from mine as o where o.course_id = m.course_id and o.country = m.country and o.day < m.day) >= {minimum}""")
print("learners whose country the daily report reveals anyway:", f"{revealed} of {count('from mine')}")
