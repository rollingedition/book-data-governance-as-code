from lib.catalog import open_catalog
from lib.enforce import MUST_ALLOW, MUST_DENY, Enforcer, source_cases

platform = Enforcer(open_catalog(["crm.learners", "support.tickets"]))

# The allowlist's regression test, lib.enforce.source_cases, which chapter 10's CI runs too: queries that read data
# other than through a catalog table, a call to each function DuckDB marks as having side effects, and a query the
# layer must still allow. A case passes when the layer denies it exactly when it must, and its parse logs nothing.
cases = source_cases(platform)
for case in cases:
    if case["case"] in MUST_DENY:
        print(f"{case['case']}:", f"denied, {case['reason']}" if case["denied"] else "ALLOWED")
functions = [case for case in cases if case["must"] == "deny" and case["case"] not in MUST_DENY]
print("functions with side effects:", ", ".join(case["case"].removeprefix("the function ") for case in functions))
print("functions with side effects, denied:", f"{sum(case['denied'] for case in functions)} of {len(functions)}")
print("cases whose parse sqlglot logged, quoting the query:", f"{sum(case['logged'] for case in cases)} of {len(cases)}")
passing = [case for case in cases if case["denied"] == (case["must"] == "deny") and not case["logged"]]
print("cases that pass:", f"{len(passing)} of {len(cases)}")

# What two of the cases do on the layer's own connection, without the layer in front of it.
before_the_cte = platform.connection.sql(MUST_DENY["a CTE's name before the CTE"]).fetchall()[0][0]
print("a CTE's name before the CTE, run directly:", f"{before_the_cte:,} rows")
platform.connection.execute("CALL enable_logging('QueryLog')")
platform.connection.sql(MUST_DENY["a function that writes DuckDB's log"]).fetchall()
logged = [message for (message,) in platform.connection.sql("select message from duckdb_logs where type = 'QueryLog'").fetchall()]
print("the log function, run directly, query-log entries it leaves:", logged)

# The query the allowlist must let through, run through the layer.
for name, query in MUST_ALLOW.items():
    print(f"{name}, allowed:", f"{platform.sql(query, 'agent-a05').fetchall()[0][0]:,} rows")
