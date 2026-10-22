"""The platform's pipelines (pipelines/*.sql): each is one `create table ... as select ...`, run on DuckDB over the
catalog's tables, with its result written back to the catalog as an Iceberg table."""
from __future__ import annotations

from pathlib import Path

import sqlglot
from pyiceberg.catalog import Catalog
from sqlglot import exp

from lib.warehouse import connect

PIPELINES = Path(__file__).resolve().parent.parent / "pipelines"


def sql(name: str) -> str:
    """A pipeline's SQL, by name ("marketing_audience")."""
    return (PIPELINES / f"{name}.sql").read_text(encoding="utf-8")


def target(statement: str) -> str:
    """The table a pipeline creates, as "namespace.table"."""
    create = sqlglot.parse_one(statement, read="duckdb")
    table = create.find(exp.Table)
    return f"{table.db}.{table.name}"


def run(catalog: Catalog, name: str) -> str:
    """Run one pipeline and write its output to the catalog, replacing it if it exists. Returns the output's name."""
    statement = sql(name)
    output = target(statement)
    select = sqlglot.parse_one(statement, read="duckdb").expression
    result = connect(catalog).sql(select.sql(dialect="duckdb")).to_arrow_table()
    catalog.create_namespace_if_not_exists(output.split(".")[0])
    if catalog.table_exists(output):
        catalog.drop_table(output)
    catalog.create_table(output, schema=result.schema).append(result)
    return output
