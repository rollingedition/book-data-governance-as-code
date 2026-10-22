import tempfile
from pathlib import Path

from lib import policy
from lib.platform import load

# A support agent added to users.toml with their group, before anyone has set their region.
data = policy.data_document()
data["platform"]["users"]["agent-new"] = {"groups": ["support"]}
request = policy.request("agent-new", "support.tickets", ["ticket_id", "body"])
regions = sorted(set(load("support.tickets").column("support_region").to_pylist()))

# A copy of the platform's policy and tests, with one line taken out of `allow`: the missing-attribute guard.
with tempfile.TemporaryDirectory() as name:
    folder = Path(name)
    for path in policy.POLICIES.glob("*.rego"):
        (folder / path.name).write_text(path.read_text(encoding="utf-8").replace("\tnot missing_attribute\n", ""), encoding="utf-8")

    for label, policies in [("with the guard", policy.POLICIES), ("without the guard", folder)]:
        decision = policy.decide(request, policy.interpreter(policies, data=data))
        seen = [r for r in regions if decision["allow"] and all(f["equals"] == r for f in decision["row_filters"])]
        print(f"{label}, allowed:", decision["allow"])
        print(f"{label}, reasons:", "; ".join(decision["reasons"]))
        print(f"{label}, row filters:", decision["row_filters"])
        print(f"{label}, regions whose tickets the agent sees:", ", ".join(seen) or "none")

    failing = [test for test, result in policy.run_tests(folder).items() if result != "PASS"]
print("tests that fail without the guard:", ", ".join(failing))
