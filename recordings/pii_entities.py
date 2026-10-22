"""Record what chapter 3 reads: OpenAI Privacy Filter's detections in every support ticket and course review.

    python recordings/pii_entities.py        (from the book's folder, in the environment of recordings/requirements.txt)

The model is downloaded at a pinned revision and its weights checked against a pinned SHA-256, then run once on the
CPU through its reference decoder, `opf`, with Viterbi decoding, which returns whole spans. The script saves only what
the model found: each span's source, row, offsets, label and text. The examples do every measurement. Writes
examples/data/recorded/pii-entities/entities.parquet and its manifest.json.
"""
import datetime
import hashlib
import json
import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import torch
from huggingface_hub import snapshot_download
from opf import OPF

BOOK = Path(__file__).resolve().parent.parent
DATA = BOOK / "examples" / "data" / "platform"
OUT = BOOK / "examples" / "data" / "recorded" / "pii-entities"
SCRIPT_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
MODEL = "openai/privacy-filter"
REVISION = "7ffa9a043d54d1be65afb281eddf0ffbe629385b"
WEIGHTS_SHA256 = "9c262cbe68a0c8a50590a648ef8341a2b7d3be1fa11dfb79893fe0b03ce57b5c"
CODE = "https://github.com/openai/privacy-filter, commit f7f00ca7fb869683eb732c010299d901457f19c3 (opf 0.1.0)"
SETTINGS = {"decode_mode": "viterbi", "output_mode": "typed", "device": "cpu", "trim_whitespace": True,
            "sources": {"tickets": "body", "reviews": "body"}}

folder = Path(snapshot_download(MODEL, revision=REVISION, allow_patterns=["original/*"])) / "original"
digest = hashlib.sha256((folder / "model.safetensors").read_bytes()).hexdigest()
if digest != WEIGHTS_SHA256:
    sys.exit(f"the downloaded weights' SHA-256 is {digest}, not the pinned {WEIGHTS_SHA256}")
torch.manual_seed(0)
detector = OPF(model=folder, device=SETTINGS["device"], decode_mode=SETTINGS["decode_mode"],
               output_mode=SETTINGS["output_mode"], trim_whitespace=SETTINGS["trim_whitespace"])

rows, started = [], time.perf_counter()
for source, key in (("tickets", "ticket_id"), ("reviews", "review_id")):
    texts = pq.read_table(DATA / f"{source}.parquet", columns=[key, "body"]).to_pylist()
    for n, record in enumerate(texts, 1):
        for span in detector.redact(record["body"]).detected_spans:
            rows.append({"source": source, "row_id": record[key], "start": span.start, "end": span.end,
                         "label": span.label, "text": span.text})
        if n % 250 == 0:
            print(f"{source}: {n:,} of {len(texts):,}, {time.perf_counter() - started:,.0f} s", flush=True)

OUT.mkdir(parents=True, exist_ok=True)
schema = pa.schema([("source", pa.string()), ("row_id", pa.int64()), ("start", pa.int32()), ("end", pa.int32()),
                    ("label", pa.string()), ("text", pa.string())])
pq.write_table(pa.Table.from_pylist(rows, schema=schema), OUT / "entities.parquet", compression="zstd")
(OUT / "manifest.json").write_text(json.dumps({
    "script": "recordings/pii_entities.py", "model": MODEL, "model_version": REVISION, "licence": "Apache-2.0",
    "weights_sha256": WEIGHTS_SHA256, "code": CODE, "script_sha256": SCRIPT_SHA256,
    "prompt_sha256": hashlib.sha256(json.dumps(SETTINGS, sort_keys=True).encode()).hexdigest(),
    "settings": SETTINGS, "torch": torch.__version__, "created": datetime.date.today().isoformat(),
    "outputs": ["entities.parquet"],
}, indent=2) + "\n", encoding="utf-8")
print(f"recorded {len(rows):,} spans in {time.perf_counter() - started:,.0f} s")
