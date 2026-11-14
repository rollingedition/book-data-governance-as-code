"""Prints the SQL the enforcement layer writes for each of the four masks the policy names, and counts DuckDB's functions
named like HMAC. The prose's points rest on the pseudonymize line and on the HMAC count."""
import duckdb
from sqlglot import exp

from lib.enforce import masked

# The four masks the policy names, as the SQL the enforcement layer writes for them.
# the column's type, which the redact mask casts its NULL to
for kind, column, type_ in [("redact", "date_of_birth", "DATE"), ("last4", "card_number", "VARCHAR"),
                            ("pseudonymize", "email", "VARCHAR"), ("redact_contacts", "body", "VARCHAR")]:
    # redact is a typed NULL; the other masks' SQL comes from lib.enforce's MASKS table
    print(f"{kind}:", masked(kind, exp.column(column), type_).sql("duckdb"))

# The pseudonymize mask hashes the key followed by the value, because DuckDB has no HMAC function to call.
# DuckDB's function catalog, searched for any name containing hmac, whatever its case
hmac = duckdb.sql("select count(*) from duckdb_functions() where function_name ilike '%hmac%'").fetchall()[0][0]
print("DuckDB functions named like HMAC:", hmac)
