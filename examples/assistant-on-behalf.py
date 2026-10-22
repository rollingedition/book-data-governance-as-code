from lib import policy
from lib.catalog import open_catalog
from lib.enforce import Enforcer

platform = Enforcer(open_catalog(["crm.learners", "learning.reviews"]))
courses = set(policy.data_document()["platform"]["instructor_courses"]["17"])     # instructor-17's own courses


def query_tool(session_user: str):
    """The tool the course assistant calls to query the warehouse. The model writes the SQL; the user it runs as comes
    from the signed-in session, never from the model or the conversation."""
    return lambda sql: platform.sql(sql, user=session_user).fetchall()


# instructor-17 asks the assistant how their courses' reviews look. The assistant writes this query.
question = "select course_id, count(*) as reviews, round(avg(rating), 2) as rating from learning.reviews group by all"
for runs_as in ["instructor-17", "assistant-1"]:
    answer = query_tool(runs_as)(question)
    print(f"run as {runs_as}, courses in the answer:", len(answer))
    print(f"run as {runs_as}, reviews behind it:", sum(reviews for _, reviews, _ in answer))
    mine = [row for row in answer if row[0] in courses]
    print(f"run as {runs_as}, instructor-17's reviews behind it:", sum(reviews for _, reviews, _ in mine))
