import duckdb
from sqlglot import exp

from lib.enforce import masked

# The four masks the policy names, as the SQL the enforcement layer writes for them.
for kind, column, type_ in [("redact", "date_of_birth", "DATE"), ("last4", "card_number", "VARCHAR"),
                            ("pseudonymize", "email", "VARCHAR"), ("redact_contacts", "body", "VARCHAR")]:
    print(f"{kind}:", masked(kind, exp.column(column), type_).sql("duckdb"))

# The pseudonymize mask hashes the key followed by the value, because DuckDB has no HMAC function to call.
hmac = duckdb.sql("select count(*) from duckdb_functions() where function_name ilike '%hmac%'").fetchall()[0][0]
print("DuckDB functions named like HMAC:", hmac)
