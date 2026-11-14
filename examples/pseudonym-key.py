"""Hashes a list of emails the way the layer hashes pseudonyms, with and without its key, then rotates the key and counts
the pseudonyms that change. The prose's points rest on the three count lines: the matches without the key and with it, and
the pseudonyms that change on rotation."""
from lib.catalog import open_catalog
from lib.enforce import Enforcer

# The pseudonyms analyst-1, the platform's data scientist, sees in crm.learners. Someone holding a list of the
# platform's emails hashes each one and looks for it among them, first without the key and then with it.
# an example key, not a secret: production keeps the real one where only the layer can read it
platform = Enforcer(open_catalog(["crm.learners"]), key="example-key-not-a-secret")
seen = platform.sql("select learner_id, email from crm.learners", "analyst-1").to_arrow_table()
platform.connection.register("seen", seen)
# a guess is an email's hash; the second also puts the key first, as the layer hashes a pseudonym
for name, guess in [("without the key", "sha256(email)"), ("with the key", "sha256($pseudonym_key || email)")]:
    matched = platform.run(f"select count(*) from crm.learners where {guess} in (select email from seen)").fetchall()[0][0]
    print(f"pseudonyms matched from a list of emails, {name}: {matched:,} of {seen.num_rows:,}")

# A new key gives every learner a new pseudonym, so data pseudonymized under the old key no longer joins.
platform.key = "example-key-rotated"
rotated = platform.sql("select learner_id, email from crm.learners", "analyst-1").to_arrow_table()
# pairs each learner's old and new pseudonym, row by row
changed = sum(a != b for a, b in zip(seen["email"].to_pylist(), rotated["email"].to_pylist()))
print("pseudonyms that change when the key is rotated:", f"{changed:,} of {seen.num_rows:,}")
