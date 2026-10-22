import duckdb
from sqlglot import exp

from lib.enforce import masked
from lib.platform import load

# The redact_contacts mask, run over every ticket and review, against the personal data the generator placed in them
# (pii_spans, labelled by construction).
con = duckdb.connect()
for name in ["support.tickets", "learning.reviews", "pii_spans"]:
    con.register(name.split(".")[-1], load(name))
mask = masked("redact_contacts", exp.column("body"), "VARCHAR").sql("duckdb")
counts = con.sql(f"""
    with masked as (select 'tickets' as source, ticket_id as row_id, {mask} as body from tickets
                    union all select 'reviews', review_id, {mask} from reviews)
    select source, entity, count(*), count(*) filter (where contains(masked.body, pii_spans.text))
    from pii_spans join masked using (source, row_id) group by all order by all""").fetchall()
for source, entity, placed, left in counts:
    print(f"{source}, {entity}, left in the text: {left:,} of {placed:,}")
# The phone pattern also takes card numbers, IBANs and dates, which are digits too.
replaced = con.sql(f"select sum(len(regexp_extract_all({mask}, '\\[phone\\]'))) from tickets").fetchall()[0][0]
print("[phone] replacements in tickets:", f"{replaced:,}")
