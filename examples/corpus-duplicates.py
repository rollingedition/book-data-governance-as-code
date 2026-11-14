"""Runs the corpus's duplicate step on the pseudonymized reviews: exact copies, then near-duplicates by MinHash LSH, each confirmed exactly, and the records kept. The prose's point rests on the "of them, differing only in a name" and "near-duplicates removed" lines."""
from collections import Counter

from lib import ai_data
from lib.catalog import open_catalog
from lib.enforce import Enforcer

# The reviews the corpus job reads, pseudonymized, then deduplicated: exact copies, then near-duplicates.
# the reviews read, each text pseudonymized; the second value counts the replacements
rows, _ = ai_data.read_pseudonymized(Enforcer(open_catalog(["crm.learners", "learning.courses", "learning.reviews"])))
# groups of reviews that stand for one record, and how many each step removed
groups, removed = ai_data.duplicate_groups(rows)
print("reviews read:", len(rows))
# one review per distinct normalized text: the one with the lowest review ID
print("distinct texts:", len(ai_data.exact_distinct(rows)))
for step, count in removed.items():
    print(f"{step}:", count)
print("records kept:", len(groups))
print("share of the reviews read that duplicate another:", f"{1 - len(groups) / len(rows):.0%}")

# identical texts only, before normalization
text, copies = Counter(row["body"] for row in rows).most_common(1)[0]
print("the most repeated text:", text)
print("its copies:", copies)

# A pair MinHash finds and the exact comparison confirms, and whether it differs only in a name.
distinct = [row["body"] for row in ai_data.exact_distinct(rows)]
# the first confirmed pair, by position among the distinct texts
i, j = sorted(ai_data.near_duplicates(distinct).confirmed)[0]
print("a near-duplicate pair:", distinct[i], "|", distinct[j])
print("its Jaccard similarity:", f"{ai_data.jaccard(distinct[i], distinct[j]):.2f}")
# equal once each pseudonym is a placeholder: the pair differs only in which names it holds
print("it differs only in a name:", ai_data.placeholders(distinct[i]) == ai_data.placeholders(distinct[j]))

# Each record kept stands for its group's reviews, under the review ID and course of the first.
# a group whose reviews come from more than one course
several = sum(len({row["course_id"] for row in group}) > 1 for group in groups)
print("records that stand for reviews of several courses:", several)
