import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from lib.catalog import open_catalog
from lib.enforce import Enforcer

platform = Enforcer(open_catalog(["crm.learners", "support.tickets"]))

# What each query reads, found by sqlglot's qualify against the catalog's schema, with `*` expanded.
queries = {
    "every column": "select * from crm.learners",
    "a join": """select l.country, count(*) as tickets
                 from crm.learners as l join support.tickets as t using (learner_id) group by all""",
    "a CTE": """with emea as (select * exclude (notes) from crm.learners where support_region = 'emea')
                select email from emea""",
    "names in capitals": 'select EMAIL from "CRM"."LEARNERS"',
}
for name, sql in queries.items():
    for table, columns in platform.reads(sql).items():
        print(f"{name}, {table}: {', '.join(columns)}")

# DuckDB resolves quoted capitals to the lowercase table. Whether sqlglot lowercases them depends on the dialect.
capitals = 'select count(*) from "CRM"."LEARNERS"'
print('DuckDB, "CRM"."LEARNERS":', f"{platform.connection.sql(capitals).fetchall()[0][0]:,} rows")
for dialect in ["duckdb", "trino", "postgres"]:
    table = normalize_identifiers(sqlglot.parse_one('select * from "LEARNERS"', read=dialect), dialect=dialect).find(exp.Table)
    print(f'{dialect} normalizes "LEARNERS" to:', table.name)
