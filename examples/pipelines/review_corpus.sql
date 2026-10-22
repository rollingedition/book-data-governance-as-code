-- The text the platform's course assistant is grounded on: course reviews with their course and rating.
create table ai.review_corpus as
select
    r.review_id,
    r.course_id,
    c.title as course_title,
    r.rating,
    r.body
from learning.reviews r
join learning.courses c on c.course_id = r.course_id
