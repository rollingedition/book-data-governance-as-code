import json

from regopy import Input, Interpreter


def ask(rules: str, query: str, request: dict) -> str:
    """One query's answer from regopy: the value bound to x, or "undefined"."""
    rego = Interpreter()
    rego.add_module("example", "package example\n" + rules)
    rego.set_input(Input(request))
    output = str(rego.query(query))
    return output if output == "undefined" else json.dumps(json.loads(output)["bindings"]["x"])


admins_only = 'default allow := false\nallow if input.user == "admin"\n'
print("a false decision, queried on its own:", ask(admins_only, "data.example.allow", {"user": "guest"}))
print("the same decision, bound to a variable:", ask(admins_only, "x := data.example.allow", {"user": "guest"}))

pre_1_0 = 'allow { input.user == "admin" }\n'                      # no `if`: OPA 1.x refuses to load this
print("a rule in pre-1.0 syntax:", ask(pre_1_0, "x := data.example.allow", {"user": "admin"}))

typo = 'region := "emea"\ndefault allow := false\nallow if not deny\ndeny if input.region != regoin\n'
print("a deny with a misspelt, unbound variable, for apac:", ask(typo, "x := data.example.allow", {"region": "apac"}))

mocks = """region := input.user.region
emea := {"region": "emea"}
with_a_literal := seen if { seen := region with input.user as {"region": "emea"} }
with_a_reference := seen if { seen := region with input.user as emea }
"""
print("region under a mock written as a literal:", ask(mocks, "x := data.example.with_a_literal", {"user": {"region": "apac"}}))
print("region under a mock that refers to a rule:", ask(mocks, "x := data.example.with_a_reference", {"user": {"region": "apac"}}))
