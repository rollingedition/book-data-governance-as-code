"""The platform's Iceberg catalog: a SQL catalog on SQLite and a warehouse of Parquet files, built fresh in a temporary
folder each time, so every example starts from the same state, and removed when the process exits."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.table import Table

from lib.platform import TABLES, load, scratch


def open_catalog(tables: list[str] | None = None, folder: Path | None = None) -> SqlCatalog:
    """A catalog holding the platform's tables (all of them, or the ones named), each loaded from its Parquet file. With
    no folder given, it's built in a temporary one that is removed when the process exits; a folder the caller gives is
    the caller's to remove."""
    folder = Path(folder) if folder else scratch("platform-")
    catalog = SqlCatalog("platform", uri=f"sqlite:///{folder}/catalog.db", warehouse=f"file://{folder}/warehouse")
    for name in tables or TABLES:
        namespace = name.split(".")[0]
        catalog.create_namespace_if_not_exists(namespace)
        data = load(name)
        catalog.create_table(name, schema=data.schema).append(data)
    return catalog


def fill_column(table: Table, column: str, value: Callable[[dict], str | None]) -> None:
    """Fill a string column, such as one just added, row by row, and overwrite the table with the result: `value`
    gets each row as a dict and returns the column's value, or None."""
    rows = table.scan().to_arrow()
    values = pa.array([value(row) for row in rows.to_pylist()], pa.string())
    table.overwrite(rows.set_column(rows.schema.get_field_index(column), column, values))
