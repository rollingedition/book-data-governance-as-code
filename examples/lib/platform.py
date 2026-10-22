"""The platform's tables, as the book loads them (data/platform/README.md): an online learning platform's warehouse."""
from __future__ import annotations

import atexit
import shutil
import tempfile
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

DATA = Path(__file__).resolve().parent.parent / "data" / "platform"
TODAY = date(2026, 10, 1)            # the book's "today": the data stops on Sep 30, 2026

# Every table, under the namespace of the team that owns it.
TABLES = {
    "crm.learners": "learners", "crm.instructors": "instructors",
    "learning.courses": "courses", "learning.enrollments": "enrollments",
    "learning.events": "events", "learning.reviews": "reviews",
    "billing.payments": "payments", "support.tickets": "tickets",
}


def load(name: str) -> pa.Table:
    """One table, by its full name ("crm.learners") or its file's name ("learners")."""
    return pq.read_table(DATA / f"{TABLES.get(name, name)}.parquet")


def scratch(prefix: str = "scratch-") -> Path:
    """A new temporary folder, removed when the process exits, whether the run succeeded or failed. The examples' copies
    of the platform hold its personal data, so none of them may outlive the run that made it."""
    folder = Path(tempfile.mkdtemp(prefix=prefix))
    atexit.register(shutil.rmtree, folder, ignore_errors=True)
    return folder
