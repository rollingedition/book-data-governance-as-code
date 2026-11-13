"""One test that replaces the whole input, and what that does to the interpreter: every query fails, and printing a failed output ends Python. The points rest on the printed error count, the assert that true does not succeed, and the signal the crash ends with."""
import signal
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from lib import policy

# The platform's tests, plus one that replaces the whole input at once rather than one path at a time.
whole_input = """
test_finance_reads_payments if {
	access.decision.allow with input as {"context": {"identity": {"user": "finance-1"}}, "action": {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "billing", "tableName": "payments", "columns": ["amount"]}}}}
}
"""
with tempfile.TemporaryDirectory() as name:
    folder = Path(name)
    for path in policy.POLICIES.glob("*.rego"):
        (folder / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    with open(folder / "access_test.rego", "a", encoding="utf-8") as tests:
        tests.write(whole_input)
    results = policy.run_tests(folder)
    print("tests that end in ERROR:", f"{Counter(results.values())['ERROR']} of {len(results)}")
    rego = policy.interpreter(folder, tests=True)
    succeeded = rego.query("true").ok()
    # no query succeeds here, not even true: the interpreter loads the file with the broken test
    assert not succeeded
    print("the query true succeeds:", succeeded)

    # lib.policy checks each output before reading it, so a decision asked of this interpreter raises an error.
    try:
        policy.decide(policy.request("finance-1", "billing.payments", ["amount"]), rego)
    except RuntimeError as error:
        print("lib.policy.decide raises:", error)

    # Printing one of those failed outputs, in a separate Python process.
    crash = "import sys; from pathlib import Path; from lib import policy; print(policy.interpreter(Path(sys.argv[1]), tests=True).query('true'))"
    ended = subprocess.run([sys.executable, "-c", crash, str(folder)], capture_output=True)
    # a negative return code names the signal that ended the process
    print("printing the failed output ends Python with:", signal.Signals(-ended.returncode).name)
