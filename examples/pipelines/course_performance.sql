-- How each course is doing: enrollments, completions, average rating and revenue. No personal data in the output.
create table analytics.course_performance as
select
    c.course_id,
    c.title,
    c.category,
    count(distinct e.enrollment_id) as enrollments,
    count(distinct e.enrollment_id) filter (where e.completed_at is not null) as completions,
    round(avg(r.rating), 2) as average_rating,
    coalesce(sum(p.amount) filter (where p.status = 'succeeded'), 0) as revenue_usd
from learning.courses c
left join learning.enrollments e on e.course_id = c.course_id
left join learning.reviews r on r.course_id = c.course_id and r.learner_id = e.learner_id
left join billing.payments p on p.enrollment_id = e.enrollment_id
group by c.course_id, c.title, c.category
