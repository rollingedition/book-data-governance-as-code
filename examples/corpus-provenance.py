from lib import ai_data, lineage, pipelines
from lib.catalog import open_catalog

catalog = open_catalog(["crm.learners", "learning.courses", "learning.reviews"])
built = ai_data.build(catalog)
record = built.records.to_pylist()[1]
print("a record's text:", record["body"])
for column in ["source_table", "review_id", "job"]:
    print(f"its {column}:", record[column])

# The run that wrote it, from the lineage events: the query as the enforcement layer ran it.
complete = [e for e in lineage.read_events(built.folder / "events.jsonl") if e["eventType"] == "COMPLETE"][0]
print("its run_id is the COMPLETE event's run:", record["run_id"] == complete["run"]["runId"])
print("the run's inputs:", ", ".join(dataset["name"] for dataset in complete["inputs"]))
facet = complete["outputs"][0]["facets"]["columnLineage"]
filters = [f"{i['name']}.{i['field']}" for i in facet["dataset"] if any(t["subtype"] == "FILTER" for t in i["transformations"])]
print("rows filtered by, as run:", ", ".join(filters))
text = [i for i in facet["fields"]["body"]["inputFields"] if (i["name"], i["field"]) == ("learning.reviews", "body")]
print("body, as run:", text[0]["transformations"][0]["subtype"])
fields, rows = lineage.column_lineage(pipelines.sql(ai_data.BUILD["job"]), lineage.schema(catalog))
print("rows filtered by, as written:", ", ".join(i.source for i in rows if i.subtype == "FILTER") or "nothing")
print("body, as written:", fields["body"][0].subtype)

# Back from every record to its review's author, with the platform's own access, against today's answers.
ids = built.records.column("review_id").to_pylist()
print("records whose author consented:", f"{ai_data.consenting(catalog, ids)} of {len(ids)}")
