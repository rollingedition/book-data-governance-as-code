"""Runs every test in the platform's Rego test file and counts the passes. The point rests on the last line, the tests passed out of all of them."""
from collections import Counter

from lib import policy

# Every `test_` rule in governance/policies/access_test.rego, run by name: PASS, FAIL or ERROR, as `opa test` reports.
results = policy.run_tests()
for test, result in results.items():
    print(f"{result}: {test}")
# len(results) counts every test, so one that fails or errors still shows in the total
print("tests passed:", f"{Counter(results.values())['PASS']} of {len(results)}")
