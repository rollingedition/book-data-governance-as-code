from collections import Counter

from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import InvalidParamError, OperatorConfig
from sqlglot import exp

from lib import ai_data, enforce
from lib.catalog import open_catalog
from lib.enforce import Enforcer
from lib.platform import load

# Two of the corpus's reviews that name the same instructor, with what the detector found in each.
layer = Enforcer(open_catalog(["crm.learners", "learning.courses", "learning.reviews"]))
engine = ai_data.detector()
found = [(row["body"], engine.analyze(row["body"], language="en")) for row in ai_data.read(layer)]
named = [(text, spans) for text, spans in found if [text[s.start:s.end] for s in spans] == ["Patrick"]][:2]
print("reviews naming Patrick:", len(named))

def replaced(operator: OperatorConfig) -> list[str]:
    """The value that replaced the name in each of the two reviews."""
    results = [AnonymizerEngine().anonymize(text, spans, {"PERSON": operator}) for text, spans in named]
    return [result.text[item.start:item.end] for result in results for item in result.items]

hashed = replaced(OperatorConfig("hash"))
print("Presidio's hash, the same name in two reviews gets one value:", hashed[0] == hashed[1])
print("Presidio's hash, one review hashed twice gets one value:", replaced(OperatorConfig("hash"))[0] == hashed[0])
try:
    replaced(OperatorConfig("hash", {"salt": "ten bytes!"}))
except InvalidParamError as error:
    print("a fixed salt of 10 bytes:", error)

keyed = replaced(OperatorConfig("custom", {"lambda": lambda value: ai_data.pseudonym("PERSON", value)}))
print("keyed pseudonyms:", ", ".join(keyed))
print("the name typed in lowercase:", ai_data.pseudonym("PERSON", "patrick"))
first_names = Counter(name.split()[0] for name in load("crm.instructors").column("full_name").to_pylist())
print("instructors who share a first name with another:", sum(n for n in first_names.values() if n > 1))

# What the enforcement layer's pseudonymize mask makes of the same name, run by the layer under its own key.
mask = enforce.masked("pseudonymize", exp.Literal.string("Patrick"), "VARCHAR").sql(enforce.DIALECT)
print("the enforcement layer's pseudonymize mask, on the same name:", layer.run(f"select {mask}").fetchall()[0][0])
hmac = layer.connection.sql("select count(*) from duckdb_functions() where function_name ilike '%hmac%'")
print("HMAC functions in DuckDB:", hmac.fetchall()[0][0])
