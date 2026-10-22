from collections import Counter

from lib import policy

# Every `test_` rule in governance/policies/access_test.rego, run by name: PASS, FAIL or ERROR, as `opa test` reports.
results = policy.run_tests()
for test, result in results.items():
    print(f"{result}: {test}")
print("tests passed:", f"{Counter(results.values())['PASS']} of {len(results)}")
