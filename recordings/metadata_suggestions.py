"""Record what chapter 2 reviews: a local open model's suggested description and classification for every column of the
platform's eight source tables, under two prompts.

    python recordings/metadata_suggestions.py        (from the book's folder, in the environment of recordings/requirements.txt)

For each column the model sees what a catalog tool would show it: the table's name, the column's name and Iceberg type,
and five of its values, drawn with a fixed seed from the rows where it isn't null. It is asked for a one-sentence
description and one of the book's four classifications, as JSON. The two prompts differ only in their examples of the
classifications. The book's prompt gives every classification two examples, none of them a column the platform has. The
earlier prompt gave examples for two classifications, and they were the platform's own columns: "a card number or a
bank account" are its only two sensitive ones. Recording both measures how much those examples decided.

Qwen3-1.7B runs at a pinned revision on the CPU, in float32, with greedy decoding and its chat template with thinking
turned off. The script saves what the model was shown and what it answered, word for word; the examples parse the
answers and do every measurement. Writes examples/data/recorded/metadata-suggestions/suggestions.parquet (the book's
prompt), suggestions-platform-examples.parquet (the earlier prompt) and their manifest.json.
"""
import datetime
import hashlib
import json
import random
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import torch
import transformers
from huggingface_hub import snapshot_download
from transformers import AutoModelForCausalLM, AutoTokenizer

BOOK = Path(__file__).resolve().parent.parent
DATA = BOOK / "examples" / "data" / "platform"
OUT = BOOK / "examples" / "data" / "recorded" / "metadata-suggestions"
SCRIPT_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
MODEL = "Qwen/Qwen3-1.7B"
REVISION = "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"
TABLES = {"crm.learners": "learners", "crm.instructors": "instructors", "learning.courses": "courses",
          "learning.enrollments": "enrollments", "learning.events": "events", "learning.reviews": "reviews",
          "billing.payments": "payments", "support.tickets": "tickets"}
# Arrow's types as the book's Iceberg catalog stores them, so the model sees the type a catalog would show.
ICEBERG_TYPES = {"int32": "int", "int64": "long", "double": "double", "bool": "boolean", "string": "string",
                 "date32[day]": "date", "timestamp[ms, tz=UTC]": "timestamptz"}
TEMPLATE = """You are documenting the tables of an online learning platform's data warehouse.

Table: {table}
Column: {column}
Type: {type}
Five sample values:
{samples}

Write a one-sentence description of the column, and classify it as one of:
CLASSES

Reply with JSON only, in this form: {{"description": "...", "classification": "..."}}"""
PROMPTS = {
    # The book's prompt: every classification has examples, and no example is a column the platform has.
    "suggestions.parquet": TEMPLATE.replace("CLASSES", """\
- public: may be shown to anyone outside the platform, such as a published blog post or a list of job openings
- internal: for the platform's staff, and doesn't identify a person by itself, such as a server's hostname or a campaign's budget
- personal: identifies a person or tells something about one, such as a photo of their face or their car's licence plate
- sensitive: data whose leak would directly harm someone, such as a government ID number or a health condition"""),
    # The earlier prompt, kept to measure what its examples decided: they name the platform's own columns.
    "suggestions-platform-examples.parquet": TEMPLATE.replace("CLASSES", """\
- public: may be shown to anyone outside the platform
- internal: for the platform's staff, and doesn't identify a person by itself
- personal: identifies a person or tells something about one, such as a name, contact details, an address or text they wrote
- sensitive: data whose leak would directly harm someone, such as a card number or a bank account"""),
}
SETTINGS = {"device": "cpu", "dtype": "float32", "threads": 8, "do_sample": False, "max_new_tokens": 128,
            "enable_thinking": False, "samples_per_column": 5, "sample_seed": 2026, "samples_from": "non-null values"}


def shown(value) -> str:
    """A sample value as the prompt shows it: JSON, with dates and times in ISO 8601."""
    return json.dumps(value.isoformat() if hasattr(value, "isoformat") else value, ensure_ascii=False)


torch.set_num_threads(SETTINGS["threads"])
folder = Path(snapshot_download(MODEL, revision=REVISION))
weights = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob("*.safetensors"))}
tokenizer = AutoTokenizer.from_pretrained(folder)
model = AutoModelForCausalLM.from_pretrained(folder, dtype=torch.float32).eval()

recorded, started = {output: [] for output in PROMPTS}, time.perf_counter()
for table, file in TABLES.items():
    data = pq.read_table(DATA / f"{file}.parquet")
    for field_id, field in enumerate(data.schema, 1):         # the catalog numbers a new table's columns from 1
        values = data.column(field.name).drop_null().to_pylist()
        rng = random.Random(f"{SETTINGS['sample_seed']}:{table}.{field.name}")
        samples = [shown(values[i]) for i in rng.sample(range(len(values)), SETTINGS["samples_per_column"])]
        for output, template in PROMPTS.items():
            prompt = template.format(table=table, column=field.name, type=ICEBERG_TYPES[str(field.type)],
                                     samples="\n".join(f"- {s}" for s in samples))
            text = tokenizer.apply_chat_template([{"role": "user", "content": prompt}], tokenize=False,
                                                 add_generation_prompt=True, enable_thinking=SETTINGS["enable_thinking"])
            inputs = tokenizer(text, return_tensors="pt")
            with torch.inference_mode():
                output_ids = model.generate(**inputs, do_sample=SETTINGS["do_sample"], temperature=None, top_p=None,
                                            top_k=None, max_new_tokens=SETTINGS["max_new_tokens"])
            response = tokenizer.decode(output_ids[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()
            recorded[output].append({"table": table, "field_id": field_id, "column": field.name,
                                     "type": ICEBERG_TYPES[str(field.type)], "samples": samples, "response": response})
        print(f"{table}.{field.name}: {time.perf_counter() - started:,.0f} s", flush=True)

OUT.mkdir(parents=True, exist_ok=True)
schema = pa.schema([("table", pa.string()), ("field_id", pa.int32()), ("column", pa.string()), ("type", pa.string()),
                    ("samples", pa.list_(pa.string())), ("response", pa.string())])
for output, rows in recorded.items():
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), OUT / output, compression="zstd")
(OUT / "manifest.json").write_text(json.dumps({
    "script": "recordings/metadata_suggestions.py", "model": MODEL, "model_version": REVISION, "licence": "Apache-2.0",
    "weights_sha256": weights, "script_sha256": SCRIPT_SHA256,
    "prompt_sha256": hashlib.sha256(json.dumps({"prompts": PROMPTS, "settings": SETTINGS}, sort_keys=True).encode()).hexdigest(),
    "prompts": PROMPTS, "settings": SETTINGS, "torch": torch.__version__, "transformers": transformers.__version__,
    "created": datetime.datetime.now(datetime.UTC).date().isoformat(), "outputs": list(PROMPTS),
}, indent=2) + "\n", encoding="utf-8")
print(f"recorded {len(rows)} columns under {len(PROMPTS)} prompts in {time.perf_counter() - started:,.0f} s")
