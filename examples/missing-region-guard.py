"""The support policy with and without its guard, asked for a support agent's tickets. The prose's points rest on the printed reasons, row filters and regions seen with each version, and on the test the copy without the guard fails."""
import tempfile
from pathlib import Path

from lib import policy
from lib.platform import load

# A support agent added to users.toml with their group, before anyone has set their region.
data = policy.data_document()
data["platform"]["users"]["agent-new"] = {"groups": ["support"]}
request = policy.request("agent-new", "support.tickets", ["ticket_id", "body"])
# every region with a ticket, the candidates for what the agent may see
regions = sorted(set(load("support.tickets").column("support_region").to_pylist()))

# A copy of the platform's policy and tests, with one line taken out of `allow`: the missing-attribute guard.
with tempfile.TemporaryDirectory() as name:
    folder = Path(name)
    for path in policy.POLICIES.glob("*.rego"):
        # the guard's line in allow is indented with a tab, and this removes it
        (folder / path.name).write_text(path.read_text(encoding="utf-8").replace("\tnot missing_attribute\n", ""), encoding="utf-8")

    for label, policies in [("with the guard", policy.POLICIES), ("without the guard", folder)]:
        decision = policy.decide(request, policy.interpreter(policies, data=data))
        # a region counts when the decision allows the read and every row filter names it, so no filters means every region
        seen = [r for r in regions if decision["allow"] and all(f["equals"] == r for f in decision["row_filters"])]
        print(f"{label}, allowed:", decision["allow"])
        print(f"{label}, reasons:", "; ".join(decision["reasons"]))
        print(f"{label}, row filters:", decision["row_filters"])
        print(f"{label}, regions whose tickets the agent sees:", ", ".join(seen) or "none")

    failing = [test for test, result in policy.run_tests(folder).items() if result != "PASS"]
print("tests that fail without the guard:", ", ".join(failing))
