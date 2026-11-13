"""The columns four queries read from each table, and how each dialect normalizes the quoted name "LEARNERS". The
prose's point rests on the query with a CTE and on the normalization lines at the end."""
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from lib.catalog import open_catalog
from lib.enforce import Enforcer

# only these two tables are loaded, so the layer's schema holds nothing else
platform = Enforcer(open_catalog(["crm.learners", "support.tickets"]))

# What each query reads, found by sqlglot's qualify against the catalog's schema, with `*` expanded.
queries = {
    "every column": "select * from crm.learners",
    "a join": """select l.country, count(*) as tickets
                 from crm.learners as l join support.tickets as t using (learner_id) group by all""",
    # exclude drops notes from the CTE, so the reads name every other column of crm.learners
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
    # the name the dialect's parser resolves the quoted identifier to
    table = normalize_identifiers(sqlglot.parse_one('select * from "LEARNERS"', read=dialect), dialect=dialect).find(exp.Table)
    print(f'{dialect} normalizes "LEARNERS" to:', table.name)
