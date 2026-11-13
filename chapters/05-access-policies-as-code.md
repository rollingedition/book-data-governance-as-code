---
chapter_id: access-policies-as-code
---

# Access policies as code

By the end of this chapter you can write a data platform's access rules as one policy that denies by default and returns row filters and column masks as data. The policy allows by role and narrows by attribute, and an engine can apply its whole decision. You'll write it in Rego, the policy language of Open Policy Agent (OPA), test it the way `opa test`, OPA's test runner, does, check it against a who-sees-what table that the data's owners can read, know where the in-process engine this book uses differs from OPA, and see the same rules in Cedar, a narrower policy language.

Six groups read the warehouse of the online learning platform this book follows, which sells professional courses to adult learners, and each group's access is a rule someone has to state and test. Support agents each serve one region, `americas`, `emea` or `apac`, and need learners' contact details to answer tickets. Instructors see the learners and reviews of their own courses, without contact details. Marketing may reach only learners who agreed to marketing email. Finance reconciles payments, and the legacy `card_number` column of `billing.payments` must never show more than its last four digits. Data scientists see `learner_id` as stored, to join tables on it, with personal columns pseudonymized and free text redacted. The platform's course assistant, its AI, reads only the reviews whose authors agreed to AI training. [Chapter 1](#governance-end-to-end) showed one rule, support agents see only their own region, and enforced it on one query. Rules for every group need to live in one place, stated exactly, and tested before anyone relies on them.

A **policy** is code that answers whether a request may go ahead; its answer is a **decision**. The **principal** is who asks, a **role** is a group of principals with the same job, and an **attribute** is any other fact a rule reads, about the principal, the data or the request. A **row filter** limits which rows a query returns, and a **column mask** changes how a column's values are shown, such as **pseudonymization**, which replaces an identifier with a consistent stand-in.

The platform's policy is `governance/policies/access.rego`, and its tests are in `governance/policies/access_test.rego`. The book's shared module `lib.policy` loads both into regopy {{version:regopy}}, the Python binding of rego-cpp, Microsoft's C++ implementation of Rego, which evaluates policies inside the Python process with no server to run.

## Shape the request like Trino's {#shape-the-request-like-trinos}

A policy engine answers questions about requests, so the shape of a request decides which systems can ask it. OPA's premise is that policy is taken out of the service that enforces it: OPA "lets you decouple policy from that software service", so that it can be read, versioned and tested on its own ([OPA philosophy](https://www.openpolicyagent.org/docs/philosophy)). The service sends a JSON document describing the request, and the policy reads it as `input`. Trino, an open-source SQL engine, does exactly this: for each access check a query needs, its OPA plugin sends a document with `context.identity`, the user and their groups, and `action`, the operation and the resource, down to the catalog, schema, table and columns ([Trino 484, OPA access control](https://trino.io/docs/484/security/opa-access-control.html)).

The platform's policy reads the same fields, so it decides Trino's request to read columns, `SelectFromColumns`, as Trino sends it. Trino needs more than that one decision. It wants each row filter and column mask as SQL, a row filter as `{"expression": "clause"}`, where the policy returns a column and a value ([Trino 484, OPA access control](https://trino.io/docs/484/security/opa-access-control.html)). It also asks about other checks, such as `ExecuteQuery` and `AccessCatalog`, which this policy's `allow` denies ([`OpaAccessControl.java` at Trino 484](https://github.com/trinodb/trino/blob/484/plugin/trino-opa/src/main/java/io/trino/plugin/opa/OpaAccessControl.java)). [Chapter 6](#engine-enforcement) shows what a module beside the policy has to return for them. `lib.policy.request` builds the platform's requests in Trino's shape, and the next example uses it for an `emea` support agent, `agent-a05`, reading three columns of `support.tickets`:

[Example: trino-shaped-request](../examples/trino-shaped-request.py)

The operation is `SelectFromColumns`, Trino's name for a read, and the table sits under `action.resource.table`, with the columns the query touches. The platform's request differs from Trino's in two places that matter. It leaves out `context.identity.groups`, because the policy looks a user's groups up in the same file as their attributes, `governance/users.toml`, so the tests and the policy read one source of truth. And it may carry a top-level `purpose`, such as `ai-training`, which Trino doesn't send. Behind Trino, we recommend deriving the purpose from the identity, for instance by giving the training pipeline a service user of its own, rather than trusting a value the client sets.

**Working rule:** Shape the policy's input like the requests of the engine that will enforce it, field for field, and add only what that engine can't send, at the top level where a reviewer sees it. If your engine isn't Trino, start from its request format instead, and keep the shape in one function, as `lib.policy.request` does, so tests and callers can't drift apart.

## Generate the data the policy reads {#generate-the-data-the-policy-reads}

The [request](#shape-the-request-like-trinos) says who asks for what. Everything else a rule needs, such as each user's groups and each column's classification, comes from a second document, which OPA calls `data`. A fact in that document is only as current as its source, and every fact the platform's policy reads already has an owner. Users and their attributes live in `governance/users.toml`. Each column's classification lives in `governance/metadata.toml`, the reviewed metadata file that [chapter 2](#key-column-metadata-by-field-id) keys by Iceberg field ID. And which instructor teaches which course is in the `learning.courses` table itself. Typing any of these into the policy, or copying them into a second file, makes a copy that someone has to remember to update.

`lib.policy.data_document` builds the document from those three sources each time a policy is loaded, and never from anything typed:

[Example: generated-policy-data](../examples/generated-policy-data.py)

The output counts the users, classified columns, tables and instructors the document holds. `agent-a05` has a group and a region, and `instructor-17` a group and an instructor ID, but nothing in `users.toml` says which courses instructor 17 teaches. The document finds {{out:generated-policy-data:instructor 17's courses}} in `learning.courses`. When the platform publishes another of their courses, the instructor's row filter includes it the next time the policy's data is generated, with no edit anywhere. A classification works the same way: changing `card_number` from `sensitive` would take one reviewed change to `metadata.toml`, and the policy would follow.

**Working rule:** Generate the policy's data from the file or table that owns each fact, every time you load the policy, and never copy a fact into the policy or into a second file. When a fact has no owner yet, such as a support agent's region, give it one file, reviewed like code, as the platform does with `users.toml`.

## Deny by default, then allow by role {#deny-by-default-then-allow-by-role}

With the [request](#shape-the-request-like-trinos) and its [data](#generate-the-data-the-policy-reads) in place, the first thing a policy must settle is what happens to a request no rule anticipated. Saltzer and Schroeder's principle of fail-safe defaults gives the answer: "Base access decisions on permission rather than exclusion" ([Saltzer and Schroeder, 1975](https://web.mit.edu/Saltzer/www/publications/protection/Basic.html)). In Rego, that is a default value. A rule whose body never evaluates to true has no value, which Rego calls **undefined**, and OPA uses the default when every rule sharing a name is undefined ([OPA policy language](https://www.openpolicyagent.org/docs/policy-language)). So with `default allow := false`, a request that no `allow` rule matches is denied, whoever sends it and whatever it asks for.

Role-based access control, first formalized by Ferraiolo and Kuhn ([Ferraiolo and Kuhn, 1992](https://csrc.nist.gov/files/pubs/conference/1992/10/13/rolebased-access-controls/final/docs/ferraiolo-kuhn-92.pdf)), grants access by the job a principal holds rather than by who they are. On the platform, a role is a group in `users.toml`, and the policy's `readable` object maps each group to the tables it may read. A request is allowed when it's a read, some group of the user may read the table, and nothing the group's row filters depend on is missing:

```rego
default allow := false

allow if {
	input.action.operation == "SelectFromColumns"
	readable_by_a_group
	not missing_attribute
}
```

The last line guards against a failure that [the section on production](#policies-in-production) shows by removing it. Print the map and decide four requests to see the default at work:

[Example: allow-by-role](../examples/allow-by-role.py)

Only what a rule grants is allowed. Finance may read `billing.payments`, and the reason names the group that allowed it. Marketing may not, and neither may `someone-else`, a user the policy has never heard of, because nothing grants either of them anything. The fourth request is finance again, asking to delete rather than read. No rule mentions `DeleteFromTable`, so it's denied too, and the reason says why. A policy that tried to list what is forbidden would have needed a rule for that operation; this one needs none. Every decision carries its reasons, so a denial can be explained to the person who got it, and logged.

**Working rule:** Start every policy with `default allow := false`, and allow only through rules that name the role and the resource. Keep the map from roles to tables in one place, where a reviewer can read all of it in one sitting, and give every decision a reason. When a request is denied that should have been allowed, add a rule that allows it; never weaken the default.

## Narrow by attribute: region, consent and purpose {#narrow-by-attribute}

[Roles](#deny-by-default-then-allow-by-role) say which tables a group may read; they can't say which rows. NIST's guide to attribute-based access control defines the general approach, in which access is decided by attributes of the subject, the object, the requested operations and, sometimes, the environment. It treats a role as one such attribute ([NIST SP 800-162](https://doi.org/10.6028/NIST.SP.800-162)). The alternative, a role for every combination, is what the same guide calls "role explosion".

The platform shows why. Support by region would need three support roles, and adding marketing's consent and a purpose for AI training would multiply the roles again. Instead, the policy keeps one role per job and reads three attributes. The **region** is an attribute of the principal, from `users.toml`. **Consent** is an attribute of the data: the learner's `marketing_consent` and `ai_training_consent` columns in `crm.learners`. The **purpose** is an attribute of the request. Each one becomes a row filter, a structured condition on a column that the engine adds to the query. The support agent's filter reads the agent's region:

```rego
row_filters contains {"column": "support_region", "equals": user.region} if {
	"support" in groups
	table in {"crm.learners", "support.tickets"}
}
```

Seven reads show what each attribute does: three support agents, an instructor, marketing, and a data scientist who reads reviews twice, once with no purpose and once for `ai-training`:

[Example: attributes-as-row-filters](../examples/attributes-as-row-filters.py)

The purpose is what changes a data scientist's view of review text. Without one, every review is visible, but the text, `body`, is shown as {{out:attributes-as-row-filters:analyst-1 reads learning.reviews, body shown as}}. For `ai-training`, only the reviews of learners who agreed to AI training remain, and the text comes back as {{out:attributes-as-row-filters:analyst-1 for ai-training reads learning.reviews, body shown as}}, with contact details taken out. The three agents get the same rule with one value per region, from one role, and instructor 17's filter lists their courses, generated from `learning.courses`. Marketing gets two filters, consent given and account active, and an engine applies both. The consent filter tests the review's author, not the review, so enforcing it takes a join to `crm.learners`. A learner who signed up before the platform asked about AI training has no answer in `ai_training_consent`, according to the platform's data card, so enforcing the filter has to count an unknown answer as a no.

The requester supplies the purpose, so it may reveal more only under a narrower condition: stating `ai-training` shows review text, but only for consenting learners and without contact details. Leaving it out, or giving any other purpose, gets what the role allows anyway, with the text hidden.

**Working rule:** Model each condition as an attribute of the principal, the data or the request, read by one rule per role, rather than as another role. When an attribute comes from the requester, as a purpose does, let it unlock access only under a narrower condition, so that leaving it out, or getting it wrong, falls back to what the role allows anyway.

## Return the decision as one document {#return-the-decision-as-one-document}

A yes or no can't carry the [row filters](#narrow-by-attribute) that attributes produce. A Rego decision needn't be yes or no, though: OPA policies can represent decisions as structured data, from booleans to maps of lists of maps ([OPA philosophy](https://www.openpolicyagent.org/docs/philosophy)). The platform's policy uses that to return its whole answer as one document, `data.platform.access.decision`, with four keys: `allow`, the `row_filters`, the `column_masks`, and the `reasons`. `lib.policy.decide` asks for it with one query and returns it as a Python dictionary.

The filters and masks are data, never SQL. A filter names a column and a value or a list of values, or, for consent, the learner's consent column. A mask is a name, one of `redact`, `last4`, `pseudonymize` and `redact_contacts`. Whatever enforces the decision turns each into SQL with its own code, so nothing a request or a user record contains, such as a region typed into `users.toml`, can reach a query as SQL text. Masks follow each column's classification. Sensitive columns are hidden from everyone, except the last four digits of a card number, which finance sees. Personal columns are shown to support, which needs contact details, and masked for everyone else as their job requires. And a column the metadata doesn't classify counts as sensitive:

```rego
classification(column) := level if {
	level := data.platform.classifications[table][column]
} else := "sensitive"
```

Take marketing reading four columns of `crm.learners`, three of them classified in the metadata. The fourth, `linkedin_url`, stands for a column a pipeline has added and nobody has classified yet:

[Example: one-decision-document](../examples/one-decision-document.py)

Marketing gets the read, with its two row filters, and two of the four columns are masked. `phone` is personal, and marketing doesn't need it for email. `linkedin_url` is unclassified, so it's treated as sensitive until someone reviews it. `full_name` and `email` have no mask, and a column without a mask is shown as stored, which marketing needs to send an email. The reasons give one line per group that allowed the read and one per mask, so the document explains itself. A denial carries empty filters and masks: the policy builds the two outcomes with two separate rules, so nothing downstream can mistake a denial for a read with some columns masked.

**Working rule:** Return one decision document per request, holding the allow, the row filters, the column masks and the reasons, and read it as a whole. Keep filters and masks as names and values that your enforcement code translates, never as SQL that the policy writes. If an engine needs SQL from the policy, as Trino does for its row filters and column masks ([Trino 484, OPA access control](https://trino.io/docs/484/security/opa-access-control.html)), generate it in one tested function between the two.

## Unit-test the policy in Rego {#unit-test-the-policy-in-rego}

The [decision document](#return-the-decision-as-one-document) is what a test can compare, which matters because a policy decides who sees personal data, and a change to it can widen access without anyone seeing it in a diff. OPA's answer is tests written in Rego: a test is a rule whose name is prefixed with `test_`, and "If the test rule is undefined or generates a non-true value the test result is reported as FAIL" ([OPA policy testing](https://www.openpolicyagent.org/docs/policy-testing)). A test sets up its request with `with`, which replaces part of `input` or `data` for one expression.

The book's engine, regopy, has no `opa test`, so `lib.policy.run_tests` plays its part. It finds every `test_` rule in the folder's `_test.rego` files, evaluates each one, and reports PASS when the rule is true, FAIL when it's false or undefined, and ERROR when evaluation fails. Each of the platform's tests sets the user and the action as two separate paths, each to a literal value, for reasons [the section on regopy](#where-regopy-differs-from-opa) shows:

```rego
test_finance_sees_only_the_last_four_digits if {
	decision := access.decision with input.context.identity.user as "finance-1"
		with input.action as {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "billing", "tableName": "payments", "columns": ["amount", "card_number"]}}}
	decision.allow
	decision.row_filters == []
	decision.column_masks == {"card_number": "last4"}
}
```

The tests cover each group's allow, its filters and its masks, and the ways a request is denied: a table the group isn't given, an operation other than a read, a user the policy doesn't know, and a support agent with no region.

[Example: policy-unit-tests](../examples/policy-unit-tests.py)

A test that checks only `decision.allow` would miss most mistakes, so every test of an allowed read compares the row filters and the column masks whole. In finance's test, `decision.row_filters == []` fails if the policy adds any filter to finance's reads, and `decision.column_masks == {"card_number": "last4"}` fails if it masks one column more or one fewer, or shows the card number another way. The run passes {{out:policy-unit-tests:tests passed}} tests.

**Working rule:** Write a test for every rule that grants or narrows access, and one for every way a request can be denied, and compare whole filters and masks, not single keys. Run the tests on every change to the policy and to the data the policy reads.

## Check who sees what from a decision table {#check-who-sees-what}

[Unit tests](#unit-test-the-policy-in-rego) are written in Rego by whoever writes the policy. The people who own the data, such as the support lead or the finance team, should be able to check the policy too, without reading Rego. A decision table gives them that: one row per request, saying who reads which table and what they must get back, in the terms of their job.

The platform's table is `lib.policy.WHO_SEES_WHAT`, a list of rows in Python. Each gives a user, a table, the columns read and an optional purpose, then whether the read is allowed, which columns the row filters test and which columns are masked, and how. The code runs every row through `lib.policy.decide`, which returns the [decision document](#return-the-decision-as-one-document), and prints one line per row, in the form to show the data's owners:

[Example: who-sees-what](../examples/who-sees-what.py)

Read down the table and each role's rules show up as outcomes. The policy matches {{out:who-sees-what:rows of the table the policy matches}} rows: the support agent filtered by region and denied payments; the instructor filtered by course and denied the learners table; finance seeing `card_number` as `last4` where the data scientist sees `redact`; and the data scientist's two reads of reviews, with and without a purpose. A reviewer who thinks a row is wrong can say so in a pull request without knowing which rule produced it.

**Working rule:** Keep a who-sees-what table in the repository with the policy, run it with the unit tests, and show its printed form to the data's owners, in terms they can check. When an owner disagrees with a row, change the row first, and let the failing row drive the change to the policy.

## Know where regopy differs from OPA {#where-regopy-differs-from-opa}

The platform's [tests](#unit-test-the-policy-in-rego) run in regopy because the book's examples must run offline, in seconds, inside Python, and OPA is a separate program. regopy {{version:regopy}} is close to OPA. Its engine states that it supports Rego v1.18.1 as OPA defines it, and that it passes all the non-built-in test suites OPA publishes. Its README lists the built-ins it doesn't support, such as `net.*` and `http.send` ([rego-cpp README at v1.5.2](https://github.com/microsoft/rego-cpp/blob/v1.5.2/README.md)), but regopy also differs from OPA in ways that README doesn't list. The platform's policy and tests avoid each of the cases that follow, and OPA itself is the reference for anything in production.

The first group runs without any error:

[Example: regopy-silent-differences](../examples/regopy-silent-differences.py)

- **A false decision comes back undefined.** Asked for `data.example.allow` on its own, regopy answers `{{out:regopy-silent-differences:a false decision, queried on its own}}` where the value is `false`, so "denied" can't be told from "no decision". Bound to a variable, the same query gives `{{out:regopy-silent-differences:the same decision, bound to a variable}}`. `lib.policy` binds every decision it reads.
- **Pre-1.0 syntax runs.** Since OPA 1.0, `if` is required for every rule and `contains` for multi-value rules ([OPA v1.0.0 release notes](https://github.com/open-policy-agent/opa/releases/tag/v1.0.0)), and rego-cpp's changelog records that it has dropped support for v0 Rego ([rego-cpp changelog](https://github.com/microsoft/rego-cpp/blob/v1.5.2/CHANGELOG)). Yet regopy evaluates `allow { ... }` and answers `{{out:regopy-silent-differences:a rule in pre-1.0 syntax}}`, so a policy can pass here and fail to load in OPA, and behind Trino as well.
- **An unbound variable switches a rule off.** OPA requires a variable in a comparison to be bound by another expression in the same rule, and rejects the policy otherwise ([OPA policy language](https://www.openpolicyagent.org/docs/policy-language)). regopy runs it: a `deny` with `regoin` misspelt for `region` is silently undefined, so `allow if not deny` allows an `apac` request: `{{out:regopy-silent-differences:a deny with a misspelt, unbound variable, for apac}}`.
- **A mock that refers to another rule is ignored.** In a policy whose `region` rule reads `input.user.region`, a mock written as a literal, `with input.user as {"region": "emea"}`, gives `{{out:regopy-silent-differences:region under a mock written as a literal}}`. Written as a reference to a rule holding the same object, it gives the real input's `{{out:regopy-silent-differences:region under a mock that refers to a rule}}`.

The second group fails, and one failure takes Python down with it. One case adds a test to a copy of the platform's tests; the other formats the same string from nine sources:

[Example: regopy-failed-outputs](../examples/regopy-failed-outputs.py)

[Example: regopy-sprintf-quotes](../examples/regopy-sprintf-quotes.py)

- **Replacing the whole input breaks every query.** One extra test that writes its request as `with input as {...}`, and evaluates rules that call built-ins such as `sprintf`, makes every query in that interpreter fail. Every test then ends in ERROR, {{out:regopy-failed-outputs:tests that end in ERROR}}, and so does the first query asked of a fresh interpreter loaded with the same files, before any test runs: the query `true` does not succeed, which the example asserts. Replacing one path at a time, as the platform's tests do, works.
- **Printing a failed output crashes Python.** Converting one of those failed outputs to a string ends the process with `{{out:regopy-failed-outputs:printing the failed output ends Python with}}`. So `lib.policy` checks `output.ok()` before it reads anything, and a decision asked of that interpreter raises an error instead: `{{out:regopy-failed-outputs:lib.policy.decide raises}}`.
- **`sprintf` keeps stray quotes.** Formatting a string returned by some built-ins, such as `object.get`'s default, `lower` or `concat`, or a local variable set to a literal, keeps its quotes. regopy's own output is then invalid JSON, as `{{out:regopy-sprintf-quotes:from object.get, its default, regopy's invalid JSON}}` shows, which its Python wrapper fails to parse. Strings from `input`, a rule, a function, iteration and `object.get`'s found value format cleanly, and the platform's policy formats only strings like those.

**Working rule:** Treat regopy as a fast local evaluator and OPA as the reference. Write Rego v1 only, bind every decision to a variable, mock input one path at a time with literal values, and check that an output succeeded before reading it. Before a policy guards production data, check and test it with OPA itself, with `opa check --strict` and `opa test`.

## Write the same rules in Cedar {#the-same-rules-in-cedar}

Rego isn't the only language for policies like the platform's. Cedar's designers present it as a language for expressive, fast, safe and analyzable authorization ([Cutler et al., 2024](https://arxiv.org/abs/2403.04651)). A Cedar request names a principal, an action and a resource, and the answer follows three rules: default deny, forbid overrides permit, and skip on error, where a policy whose evaluation errors is left out of the answer, as "if a policy's evaluation returns error, the policy does not factor into the authorization response; it is skipped" ([Cedar, authorization](https://docs.cedarpolicy.com/auth/authorization.html)). A schema declares every type of entity, the kinds of principal and resource a request can name, with its attributes, and attributes are required unless the schema marks them optional ([Cedar, schema](https://docs.cedarpolicy.com/schema/schema.html)). Validation checks policies against that schema before any request, and a set that validates avoids most of the errors that evaluation could raise, for requests that match the schema ([Cedar, validation](https://docs.cedarpolicy.com/policies/validation.html)). The book's examples run Cedar with cedarpy {{version:cedarpy}}, which wraps the Rust Cedar Policy library for Python ([cedarpy](https://pypi.org/project/cedarpy/4.12.2/)).

The platform's rules in Cedar are in `governance/policies/access.cedar`, with the schema in `access.cedarschema`. A `permit` per group repeats the Rego policy's [`readable` map](#deny-by-default-then-allow-by-role). A `forbid` keeps support agents to learners in their own region, and another hides sensitive columns from everyone except finance's view of card numbers. `lib.policy.cedar_entities` generates the users, groups, tables and columns from the same [data document](#generate-the-data-the-policy-reads) the Rego policy reads, and the code adds the first learner of `crm.learners` in each region:

[Example: cedar-same-rules](../examples/cedar-same-rules.py)

The decisions agree with the Rego policy wherever Cedar can express them. Finance may read the `card_number` column, and the data scientist may not, where Rego allows the read and redacts the column. The data scientist may read `amount`, and marketing may not read payments. The `emea` agent may read the `emea` learner and neither of the others. The policies validate against the schema too, and misspelling `region` gives a precise error before anything runs: {{out:cedar-same-rules:with region misspelt}}.

Rego has no such check by default. The same typo in an attribute, `user.regoin` in the support agent's row filter, is undefined in OPA and regopy alike, and `opa check --strict` passes it, because OPA checks references against a schema only when you give it one ([OPA policy language, schemas](https://www.openpolicyagent.org/docs/policy-language#using-schemas-to-enhance-the-rego-type-checker)), and the platform gives none. The agent's row filter disappears, and only a test that compares the filters, such as `test_support_reads_only_its_own_region`, catches it.

Cedar returns a decision and little else. The last line of the output is everything it gives back for finance's card numbers: the decision, and the ID of the policy that made it. cedarpy names policies by position, `policy3` for the fourth, not by their `@id` annotations such as `finance-tables`. Cedar's documentation says the annotation is only how its own CLI sets policy IDs, and other interfaces set them another way ([Cedar, syntax](https://docs.cedarpolicy.com/policies/syntax-policy.html)). Nothing in the answer says `last4`, the [mask](#return-the-decision-as-one-document) the Rego policy returns for finance's card numbers, because Cedar can permit a column but can't say how to show it. That rule would move into the enforcement code, outside the policy and its tests.

Cedar decides one learner at a time, so filtering the platform's learners means one request per learner, or Cedar's experimental partial evaluation, which returns residual policies for the parts it can't yet decide, and whose result is not a final authorization decision ([cedarpy](https://pypi.org/project/cedarpy/4.12.2/)). Cedar's paper makes the trade-off from the other side: "Rego's added expressiveness is a blessing but also a curse". It adds that Rego provides no built-in authorization concepts such as allow or deny ([Cutler et al., 2024](https://arxiv.org/abs/2403.04651)). The same paper reports Cedar 42.8 to 80.8 times faster than Rego at the median, on three workloads with transitive relations, with Cedar 3.0.1 against OPA's Rego 0.61.0. Those are its designers' measurements, not this book's, and they say nothing about regopy.

**Working rule:** Choose Cedar when every question is "may this principal do this to this resource?", as in an application's permission checks, and you want a schema to check every policy before it runs. Choose Rego when the answer has to carry data, as a warehouse's row filters and column masks do, or when the engine already asks OPA, as Trino does. For the platform's warehouse, we recommend Rego.

## What goes wrong in production {#policies-in-production}

We consider the most damaging failure a rule that silently doesn't apply because an attribute is missing, and it happens in both languages. In Rego, "Expressions that refer to undefined values are also undefined" ([OPA policy language](https://www.openpolicyagent.org/docs/policy-language)). The support agent's [row filter](#narrow-by-attribute) takes its value from `user.region`, so for a user without a region the filter doesn't exist at all. A rule that allows by group still allows, and no filter means every row.

Take a copy of the platform's policy and tests, in a temporary folder, and remove the one line, `not missing_attribute`, from [`allow`](#deny-by-default-then-allow-by-role). Then add a support agent with their group and no region, as happens when someone is given a role before anyone sets their region, and ask both policies for the agent's tickets:

[Example: missing-region-guard](../examples/missing-region-guard.py)

Without the guard, the agent is allowed, with row filters {{out:missing-region-guard:without the guard, row filters}}, and sees the tickets of {{out:missing-region-guard:without the guard, regions whose tickets the agent sees}}, including every learner's message and contact details. The reasons already say the region is missing; nothing in `allow` asked. With the guard, the agent is denied, and the reason names what is missing: {{out:missing-region-guard:with the guard, reasons}}. `default allow := false` didn't help either, because a rule did allow. One test catches it, {{out:missing-region-guard:tests that fail without the guard}}, which is why the policy's tests include the case of the missing attribute.

Cedar fails the same way, through [skip on error](#the-same-rules-in-cedar). Strip the `has region` check from the support agents' `forbid` in the platform's Cedar policies, and it reads `principal.region` on an agent who has none:

[Example: cedar-missing-region](../examples/cedar-missing-region.py)

Without the check, Cedar lets a support agent with no region read the learners of all three regions. Evaluating the `forbid` raises an error, Cedar skips that policy, and the support `permit` still applies. With the check, the same agent is denied every learner. Passing the schema to `is_authorized` didn't stop it, because the schema declares `region` optional, since users outside support have none. Validation did, before any request: {{out:cedar-missing-region:validation without it}}.

Other failures to watch for:

- **A misspelt attribute is undefined, not an error.** `user.regoin` for `user.region` passes `opa check --strict`, and the support filter disappears as it does for a missing region. Compare whole filters in the tests, and give OPA a schema if you want the check before any test runs.
- **A deny rule that reads a missing attribute fails open.** The guard fixes `allow`; a `deny` or `forbid` needs the same care. Deny explicitly when a required attribute is absent, and write the test for it before the rule.
- **Policies nobody can test.** Rules kept in an engine's settings or in hand-made views per role can't be unit-tested or diffed, so they drift. Keep the policy, its tests and its decision table in the repository, and run all three on every change.
- **regopy passes and OPA refuses.** Pre-1.0 syntax and unbound variables run in regopy. Check every policy with `opa check --strict` before deploying it to OPA.
- **Data that changed under the policy.** A support agent moved to another region is one edit to `users.toml`, and the decision follows only when the data document is rebuilt. Rebuild it whenever its sources change, and deploy it with the policy.

## What this chapter decided {#policies-decisions}

- **One policy, in Rego,** `governance/policies/access.rego`, denies by default, allows by group, and narrows by three attributes: the agent's region, the learner's consent and the request's purpose.
- **Requests are shaped like Trino's OPA requests,** built by `lib.policy.request`, plus an optional top-level `purpose`; groups come from `users.toml`, not from the request.
- **The policy's data is generated** by `lib.policy.data_document` from `users.toml`, `metadata.toml` and `learning.courses`, and never typed.
- **Each decision is one document** with `allow`, `row_filters`, `column_masks` and `reasons`. Filters and masks are data, never SQL, and the masks are `redact`, `last4`, `pseudonymize` and `redact_contacts`. A denial carries no filters or masks.
- **Missing facts fail closed:** a column the metadata doesn't classify is treated as sensitive, and a user whose row filter needs a missing attribute is denied.
- **The one purpose the policy knows is `ai-training`,** which limits reviews to learners who agreed to AI training and shows their text with contact details removed. A read without a purpose gets what the role allows, with review text hidden.
- **Two kinds of test run on every change:** the Rego unit tests through `lib.policy.run_tests`, and the who-sees-what table, `lib.policy.WHO_SEES_WHAT`.
- **regopy for the examples, OPA as the reference:** Rego v1 only, every decision bound to a variable, input mocked one path at a time with literals, and every output checked before it's read.
- **Cedar is for comparison** (`governance/policies/access.cedar`). It suits allow-or-deny checks on single resources; the platform's warehouse needs filters and masks as data, so it uses Rego.
