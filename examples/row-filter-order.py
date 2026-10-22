from lib.enforce import order_cases

# The evaluation-order regression test, lib.enforce.order_cases, which chapter 10's CI runs on every DuckDB upgrade.
# For each shape of row filter the layer writes, it takes a real user's filter and a copy of the filtered table whose
# `body` holds digits in the rows the filter keeps and text in the rows it drops. A query that casts `body` to a number
# fails only if the cast sees a dropped row's value. It runs over the filter in a WHERE alone, and over the layer's
# subquery.
cases = order_cases()
for case in cases:
    for form, safe in [("with the filter in WHERE only", case["safe_with_where_only"]),
                       ("as the layer writes it", case["safe_as_written"])]:
        print(f"{case['shape']}, {form}: the cast {'never saw' if safe else 'saw'} a dropped row's value")
print("filter shapes the layer's subquery keeps safe:", f"{sum(case['safe_as_written'] for case in cases)} of {len(cases)}")
