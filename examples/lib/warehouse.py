"""DuckDB over the catalog's tables: queries name tables as the platform does ("crm.learners"), and read what an Iceberg
scan returns. DuckDB's own Iceberg extension would need a download, so the scan is PyIceberg's."""
from __future__ import annotations

import duckdb
from pyiceberg.catalog import Catalog


def connect(catalog: Catalog) -> duckdb.DuckDBPyConnection:
    """A DuckDB connection with one view per catalog table, under a schema per namespace."""
    con = duckdb.connect()
    con.execute("set TimeZone = 'UTC'")        # never the machine's own zone, in results or in the tables written back
    for (namespace,) in catalog.list_namespaces():
        con.execute(f"create schema if not exists {namespace}")
        for _, name in catalog.list_tables(namespace):
            arrow = catalog.load_table(f"{namespace}.{name}").scan().to_arrow()
            con.register(f"scan_{namespace}_{name}", arrow)
            con.execute(f"create view {namespace}.{name} as select * from scan_{namespace}_{name}")
    return con
