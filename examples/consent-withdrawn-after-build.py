"""Withdraws one author's consent after a build, rebuilds the corpus in the same folder, and checks both builds against today's consent and the card against the file. The prose's point rests on the "records whose author consents today" and "problems" lines."""
from datetime import timedelta

import pyarrow.compute as pc
import pyarrow.parquet as pq

from lib import ai_data, lineage
from lib.catalog import open_catalog
from lib.platform import load

catalog = open_catalog(["crm.learners", "learning.courses", "learning.reviews"])
first = ai_data.build(catalog)                        # the nightly run of Oct 1, and its card
card, _ = ai_data.card(first)
ai_data.write_card(card, first.folder)

# The next day, the author of one of the corpus's records withdraws their consent to AI training.
# review ID to the learner who wrote it, from the reviews table
authors = dict(zip(*load("learning.reviews").select(["review_id", "learner_id"]).to_pydict().values()))
# the author of the Oct 1 build's second record
learner = authors[first.records.column("review_id")[1].as_py()]
learners = catalog.load_table("crm.learners")
rows = learners.scan().to_arrow()
# that learner's answer becomes False; every other row keeps its answer
consent = pc.if_else(pc.equal(rows["learner_id"], learner), False, rows["ai_training_consent"])
learners.overwrite(rows.set_column(rows.schema.get_field_index("ai_training_consent"), "ai_training_consent", consent))

# The next nightly run rebuilds the corpus in the same folder, and nobody generates the card again. A training run
# checks its build against today's consent before it starts.
# the same folder: the corpus file is replaced, so the first card no longer matches it
second = ai_data.build(catalog, first.folder, lineage.NIGHTLY + timedelta(days=1))
for name, built in [("Oct 1 build", first), ("Oct 2 build", second)]:
    ids = built.records.column("review_id").to_pylist()
    # records in each build whose author is the learner who withdrew
    print(f"{name}, records from the learner who withdrew:", sum(authors[i] == learner for i in ids))
    print(f"{name}, records whose author consents today:", f"{ai_data.consenting(catalog, ids)} of {len(ids)}")
different_runs = first.run_id != second.run_id
assert different_runs
print("the two builds are different runs:", different_runs)
# the legal team's placeholder answer, from governance/ai_data.toml
print("a model made from the Oct 1 build, the legal team's answer:", ai_data.LEGAL["withdrawal"]["models_made_before"])

# the card's last preprocessing step, which states the Oct 1 counts
print("the Oct 1 card says:", card["rai:dataPreprocessingProtocol"][-1])
print("records in the corpus file now:", pq.read_metadata(first.folder / ai_data.CORPUS_FILE).num_rows)
# the platform's check of the Oct 1 card against the corpus file as it is now
problems = ai_data.card_problems(card, first.folder)
print("problems with the Oct 1 card, checked against the corpus now:", len(problems))
for problem in problems:
    print("-", problem)
