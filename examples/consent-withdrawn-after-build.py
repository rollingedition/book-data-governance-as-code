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
authors = dict(zip(*load("learning.reviews").select(["review_id", "learner_id"]).to_pydict().values()))
learner = authors[first.records.column("review_id")[1].as_py()]
learners = catalog.load_table("crm.learners")
rows = learners.scan().to_arrow()
consent = pc.if_else(pc.equal(rows["learner_id"], learner), False, rows["ai_training_consent"])
learners.overwrite(rows.set_column(rows.schema.get_field_index("ai_training_consent"), "ai_training_consent", consent))

# The next nightly run rebuilds the corpus in the same folder, and nobody generates the card again. A training run
# checks its build against today's consent before it starts.
second = ai_data.build(catalog, first.folder, lineage.NIGHTLY + timedelta(days=1))
for name, built in [("Oct 1 build", first), ("Oct 2 build", second)]:
    ids = built.records.column("review_id").to_pylist()
    print(f"{name}, records from the learner who withdrew:", sum(authors[i] == learner for i in ids))
    print(f"{name}, records whose author consents today:", f"{ai_data.consenting(catalog, ids)} of {len(ids)}")
print("the two builds are different runs:", first.run_id != second.run_id)
print("a model made from the Oct 1 build, the legal team's answer:", ai_data.LEGAL["withdrawal"]["models_made_before"])

print("the Oct 1 card says:", card["rai:dataPreprocessingProtocol"][-1])
print("records in the corpus file now:", pq.read_metadata(first.folder / ai_data.CORPUS_FILE).num_rows)
problems = ai_data.card_problems(card, first.folder)
print("problems with the Oct 1 card, checked against the corpus now:", len(problems))
for problem in problems:
    print("-", problem)
