-- Learners marketing may email about new courses: those who consented, with their name, email and favourite category.
create table marketing.audience as
with favourite as (
    select e.learner_id, c.category, count(*) as enrollments,
           row_number() over (partition by e.learner_id order by count(*) desc, c.category) as rank
    from learning.enrollments e
    join learning.courses c on c.course_id = e.course_id
    group by e.learner_id, c.category
)
select
    l.learner_id,
    l.full_name,
    l.email,
    l.country,
    f.category as favourite_category
from crm.learners l
join favourite f on f.learner_id = l.learner_id and f.rank = 1
where l.marketing_consent and l.status = 'active'
