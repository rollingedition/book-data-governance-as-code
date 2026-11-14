"""Builds the corpus and follows one record back to its source row, its job and the run that wrote it, then checks that run's lineage against the query as written. The prose's point rests on the "rows filtered by" and "body" lines."""
from lib import ai_data, lineage, pipelines
from lib.catalog import open_catalog

catalog = open_catalog(["crm.learners", "learning.courses", "learning.reviews"])
built = ai_data.build(catalog)
# the second record, whose text holds a pseudonym
record = built.records.to_pylist()[1]
print("a record's text:", record["body"])
for column in ["source_table", "review_id", "job"]:
    print(f"its {column}:", record[column])

# The run that wrote it, from the lineage events: the query as the enforcement layer ran it.
# the job's COMPLETE event, which carries the run's lineage
complete = [e for e in lineage.read_events(built.folder / "events.jsonl") if e["eventType"] == "COMPLETE"][0]
same_run = record["run_id"] == complete["run"]["runId"]
assert same_run
print("its run_id is the COMPLETE event's run:", same_run)
print("the run's inputs:", ", ".join(dataset["name"] for dataset in complete["inputs"]))
# column lineage: the inputs that filter the rows, and how each field is transformed
facet = complete["outputs"][0]["facets"]["columnLineage"]
# a FILTER transformation marks an input whose values decide which rows the output holds
filters = [f"{i['name']}.{i['field']}" for i in facet["dataset"] if any(t["subtype"] == "FILTER" for t in i["transformations"])]
print("rows filtered by, as run:", ", ".join(filters))
# the lineage of the review text, from the reviews table
text = [i for i in facet["fields"]["body"]["inputFields"] if (i["name"], i["field"]) == ("learning.reviews", "body")]
# IDENTITY means copied unchanged; TRANSFORMATION means the value was changed on its way
print("body, as run:", text[0]["transformations"][0]["subtype"])
# the same lineage parsed from the SQL as written, with no enforcement rewrite
fields, rows = lineage.column_lineage(pipelines.sql(ai_data.BUILD["job"]), lineage.schema(catalog))
print("rows filtered by, as written:", ", ".join(i.source for i in rows if i.subtype == "FILTER") or "nothing")
print("body, as written:", fields["body"][0].subtype)

# Back from every record to its review's author, with the platform's own access, against today's answers.
# each record's review ID, checked against the author's consent today, not at build time
ids = built.records.column("review_id").to_pylist()
print("records whose author consented:", f"{ai_data.consenting(catalog, ids)} of {len(ids)}")
