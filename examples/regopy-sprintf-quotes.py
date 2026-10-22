import json

from regopy import Input, Interpreter

# The same label, sprintf("%s.%s", [schema, input.table]), with the schema "crm" taken from nine places.
helpers = 'crm := "crm"\nschema_of(x) := "crm" if x == 1\n'
cases = [
    ("from input.schema", "input.schema", "true"),
    ("from a rule", "crm", "true"),
    ("from a function", "schema_of(1)", "true"),
    ("from iterating over a set", "s", 'some s in {"crm"}'),
    ("from object.get, the key found", 'object.get(input, "schema", "public")', "true"),
    ("from object.get, its default", 'object.get(input, "namespace", "crm")', "true"),
    ("from lower(input.schema)", "lower(input.schema)", "true"),
    ("from concat", 'concat("", ["c", "rm"])', "true"),
    ("from a local variable set to a literal", "v", 'v := "crm"'),
]
for name, schema, body in cases:
    rego = Interpreter()
    rego.add_module("example", f'package example\n{helpers}label := sprintf("%s.%s", [{schema}, input.table]) if {{ {body} }}\n')
    rego.set_input(Input({"schema": "crm", "table": "learners"}))
    try:
        print(f"{name}:", json.loads(str(rego.query("x := data.example.label")))["bindings"]["x"])
    except json.JSONDecodeError as error:
        print(f"{name}, regopy's invalid JSON:", error.doc)
