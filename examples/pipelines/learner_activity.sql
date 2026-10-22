-- Each learner's activity: when they were last active, how many events and courses. Keyed by learner, with country.
create table analytics.learner_activity as
select
    l.learner_id,
    l.country,
    l.signup_at,
    max(ev.occurred_at) as last_active_at,
    count(ev.event_id) as events,
    count(distinct ev.course_id) as courses_active
from crm.learners l
left join learning.events ev on ev.learner_id = l.learner_id
group by l.learner_id, l.country, l.signup_at
