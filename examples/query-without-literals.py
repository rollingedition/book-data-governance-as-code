"""A record keeps a query's shape and none of its values, while DuckDB's own query log keeps the email as it was typed.
The printed lines the prose rests on are the email's two checks and the three digest counts."""
from lib import audit
from lib.catalog import open_catalog
from lib.platform import load, scratch

log = audit.Log(scratch() / "audit.jsonl")
platform = audit.Audited(open_catalog(["crm.learners"]), log)

# agent-a05 looks up an EMEA learner by the email in a ticket, then by their ID. Neither value is ever printed.
learners = load("crm.learners").to_pylist()
learner = next(row for row in learners if row["support_region"] == "emea")      # the first EMEA learner in the table
email, learner_id = learner["email"], learner["learner_id"]
platform.sql(f"select learner_id, full_name from crm.learners where email = '{email}'", "agent-a05").fetchall()
platform.sql(f"select email, phone from crm.learners where learner_id = {learner_id}", "agent-a05").fetchall()
platform.sql("select learner_id, email from crm.learners", "analyst-1").fetchall()      # pseudonymized with the key

for record in log.records():
    print("query in the record:", record["query"])
engine = audit.engine_queries(platform.enforcer.connection)      # DuckDB's own query log, literals and all
print("the email, in the audit log:", email in log.path.read_text())
assert email not in log.path.read_text()        # the record keeps `?` where the email was, so nothing matches
print("the email, in DuckDB's query log:", any(email in query for query in engine))
assert any(email in query for query in engine)  # DuckDB logs the SQL text it runs, literals included
print("the pseudonymization key, in DuckDB's query log:", any(platform.enforcer.key in query for query in engine))
assert not any(platform.enforcer.key in query for query in engine)
print("its placeholder, in DuckDB's query log:", any("$pseudonym_key" in query for query in engine))

# Guessing which learner the agent looked up, from a list of the platform's emails: rebuild the text DuckDB ran with
# each email, digest it, and compare. First with a plain SHA-256 of the query, as a log might keep, then with the
# record's keyed digest, by someone without the layer's key and by someone who holds it.
ran = next(query for query in engine if email in query)         # the first query DuckDB logged with the email in it
guesses = [ran.replace(email, row["email"]) for row in learners]    # the same text, with each learner's email in its place
logged = log.records()[0]["rewritten_digest"]                    # record 1 is the agent's lookup by email
print("emails tried:", f"{len(guesses):,}")
print("emails a plain SHA-256 of the query confirms:", sum(audit.sha256(g) == audit.sha256(ran) for g in guesses))
print("emails the keyed digest confirms, without the layer's key:",
      sum(audit.digest(g, "a-guessed-key") == logged for g in guesses))     # a wrong key, the only kind a guesser has
print("emails the keyed digest confirms, with the layer's key:", sum(audit.digest(g, platform.key) == logged for g in guesses))
