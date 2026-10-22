---
chapter_id: access-policies-as-code
---

# Access policies as code

By the end of this chapter you can write a data platform's access rules as one policy that denies by default and returns row filters and column masks as data. The policy allows by role and narrows by attribute, and an engine can apply its whole decision. You'll write it in Rego, the policy language of Open Policy Agent (OPA), test it the way `opa test`, OPA's test runner, does, check it against a who-sees-what table that the data's owners can read, know where the in-process engine this book uses differs from OPA, and see the same rules in Cedar, a narrower policy language.

The platform, the online learning platform this book follows, sells professional courses to adult learners, and each of the people and programs that read its warehouse needs something different. Support agents each serve one region, `americas`, `emea` or `apac`, and need learners' contact details to answer tickets. Instructors see the learners and reviews of their own courses, without contact details. Marketing may reach only learners who agreed to marketing email. Finance reconciles payments, and the legacy `card_number` column of `billing.payments` must never show more than its last four digits. Data scientists see `learner_id` as stored, to join tables on it, with personal columns pseudonymized and free text redacted. The platform's course assistant, its AI, reads only the reviews whose authors agreed to AI training. [Chapter 1](#governance-end-to-end) showed one rule, support agents see only their own region, and enforced it on one query. Rules for every group need to live in one place, stated exactly, and tested before anyone relies on them.

This chapter's terms are the ones the rest of the book uses. A **policy** is code that answers whether a request may go ahead; its answer is a **decision**. The **principal** is who asks, a **role** is a group of principals with the same job, and an **attribute** is any other fact a rule reads, about the principal, the data or the request. A **row filter** limits which rows a query returns, and a **column mask** changes how a column's values are shown, such as **pseudonymization**, which replaces an identifier with a consistent stand-in.

The platform's policy is `governance/policies/access.rego`, and its tests are in `governance/policies/access_test.rego`. The book's shared module `lib.policy` loads both into regopy {{version:regopy}}, the Python binding of rego-cpp, Microsoft's C++ implementation of Rego, which evaluates policies inside the Python process with no server to run.

## Shape the request like Trino's {#shape-the-request-like-trinos}

A policy engine answers questions about requests, so the shape of a request decides which systems can ask it. OPA's premise is that policy is taken out of the service that enforces it: OPA "lets you decouple policy from that software service" so that it can be read, versioned and tested on its own ([OPA philosophy](https://www.openpolicyagent.org/docs/philosophy)). The service sends a JSON document describing the request, and the policy reads it as `input`. Trino, an open-source SQL engine, has a plugin that does exactly this: for each access check a query needs, it sends OPA a document with `context.identity`, the user and their groups, and `action`, the operation and the resource, down to the catalog, schema, table and columns ([Trino 484, OPA access control](https://trino.io/docs/484/security/opa-access-control.html)).

The platform's policy reads the same fields, so it decides Trino's request to read columns, `SelectFromColumns`, as Trino sends it. Trino needs more than that one decision, though: it wants each row filter and column mask as SQL, a row filter as `{"expression": "clause"}`, whereas the policy returns a column and a value ([Trino 484, OPA access control](https://trino.io/docs/484/security/opa-access-control.html)), and it also asks about other checks, such as `ExecuteQuery` and `AccessCatalog`, that this policy's `allow` denies ([`OpaAccessControl.java` at Trino 484](https://github.com/trinodb/trino/blob/484/plugin/trino-opa/src/main/java/io/trino/plugin/opa/OpaAccessControl.java)). [Chapter 6](#engine-enforcement) shows what a module beside the policy has to return for them. `lib.policy.request` builds the platform's requests in Trino's shape. Here an `emea` support agent, `agent-a05`, reads three columns of `support.tickets`:

[Example: trino-shaped-request](../examples/trino-shaped-request.py)

The operation is `SelectFromColumns`, Trino's name for a read, and the table sits under `action.resource.table` with its catalog, schema, name and the columns the query touches. The platform's request differs from Trino's in two places that matter. It leaves out `context.identity.groups`, because the policy looks a user's groups up in the same file as their attributes, `governance/users.toml`, so the tests and the policy read one source of truth. And it may carry a top-level `purpose`, such as `ai-training`, which Trino doesn't send. Behind Trino, we recommend deriving the purpose from the identity, for instance by giving the training pipeline a service user of its own, rather than trusting a value the client sets.

**Working rule:** Shape the policy's input like the requests of the engine that will enforce it, field for field, and add only what that engine can't send, at the top level where a reviewer sees it. If your engine isn't Trino, start from its request format instead, and keep the shape in one function, as `lib.policy.request` does, so tests and callers can't drift apart.

## Generate the data the policy reads {#generate-the-data-the-policy-reads}

The [request](#shape-the-request-like-trinos) says who asks for what. Everything else a rule needs, such as each user's groups and each column's classification, comes from a second document that OPA calls `data`. A fact in that document is only as current as its source, and every fact the platform's policy reads has an owner already. Users and their attributes live in `governance/users.toml`. Each column's classification lives in `governance/metadata.toml`, the reviewed metadata file that [chapter 2](#key-column-metadata-by-field-id) keys by Iceberg field ID. And which instructor teaches which course is in the `learning.courses` table itself. Typing any of these into the policy, or copying them into a second file, makes a copy that someone has to remember to update.

`lib.policy.data_document` builds the document from those three sources each time a policy is loaded, and never from anything typed:

[Example: generated-policy-data](../examples/generated-policy-data.py)

The document holds {{out:generated-policy-data:users, from governance/users.toml}} users, the classifications of {{out:generated-policy-data:columns classified}} columns across {{out:generated-policy-data:tables, from governance/metadata.toml}} tables, and the courses of {{out:generated-policy-data:instructors with courses, from learning.courses}} instructors. `agent-a05` has a group and a region, `instructor-17` a group and an instructor ID, and nothing in `users.toml` says which courses instructor 17 teaches: the document finds {{out:generated-policy-data:instructor 17's courses}} in `learning.courses`. When the platform publishes another of their courses, the instructor's row filter includes it the next time the policy's data is generated, with no edit anywhere. A classification works the same way: changing `card_number` from `sensitive` would take one reviewed change to `metadata.toml`, and the policy would follow.

**Working rule:** Generate the policy's data from the file or table that owns each fact, every time you load the policy, and never copy a fact into the policy or into a second file. When a fact has no owner yet, such as a support agent's region, give it one file, reviewed like code, as the platform does with `users.toml`.

## Deny by default, then allow by role {#deny-by-default-then-allow-by-role}

With the [request](#shape-the-request-like-trinos) and its [data](#generate-the-data-the-policy-reads) in place, the first thing a policy has to settle is what happens to a request no rule anticipated. Saltzer and Schroeder's principle of fail-safe defaults answers it: "Base access decisions on permission rather than exclusion" ([Saltzer and Schroeder, 1975](https://web.mit.edu/Saltzer/www/publications/protection/Basic.html)). In Rego, that is a default value. A rule whose body never evaluates to true has no value, which Rego calls **undefined**, and "The default value is used when all the rules sharing the same name are undefined" ([OPA policy language](https://www.openpolicyagent.org/docs/policy-language)), so with `default allow := false`, a request that no `allow` rule matches is denied, whoever sends it and whatever it asks for.

The rules that do allow start from roles: role-based access control, first formalized by Ferraiolo and Kuhn, grants access by the job a principal holds rather than by who they are ([Ferraiolo and Kuhn, 1992](https://csrc.nist.gov/files/pubs/conference/1992/10/13/rolebased-access-controls/final/docs/ferraiolo-kuhn-92.pdf)). On the platform, a role is a group in `users.toml`, and the policy's `readable` object maps each group to the tables it may read. A request is allowed when it's a read, some group of the user may read the table, and nothing the group's row filters depend on is missing:

```rego
default allow := false

allow if {
	input.action.operation == "SelectFromColumns"
	readable_by_a_group
	not missing_attribute
}
```

The last line is a guard against a failure this chapter's [section on production](#policies-in-production) shows by removing it. The example prints the map and decides four requests:

[Example: allow-by-role](../examples/allow-by-role.py)

Finance may read `billing.payments`, and the reason says which group allowed it. Marketing may not, and neither may `someone-else`, a user the policy has never heard of: nothing grants either of them anything, so the default holds. The fourth request is finance again, on the same table, asking to delete rather than read. No rule mentions `DeleteFromTable`, so it's denied too, and the reason says why. A policy that tried to list what is forbidden would have needed a rule for that operation; this one needs none.

Every decision carries its reasons, so a denial can be explained to the person who got it, and logged.

**Working rule:** Start every policy with `default allow := false`, and allow only through rules that name the role and the resource. Keep the map from roles to tables in one place, where a reviewer can read all of it in one sitting, and give every decision a reason. When a request is denied that should have been allowed, add a rule that allows it; never weaken the default.

## Narrow by attribute: region, consent and purpose {#narrow-by-attribute}

[Roles](#deny-by-default-then-allow-by-role) say which tables a group may read; they can't say which rows. NIST's guide to attribute-based access control defines the general approach: access "is determined by evaluating attributes associated with the subject, object, requested operations, and, in some cases, environment conditions", and it treats roles as one such attribute: "RBAC works on the attribute of 'role'" ([NIST SP 800-162](https://doi.org/10.6028/NIST.SP.800-162)). The alternative, a role for every combination, ends in what the same guide calls "role explosion".

The platform shows why. Support by region would need three support roles; add marketing's consent and a purpose for AI training, and the roles multiply again. Instead, the policy keeps one role per job and reads three attributes. The **region** is an attribute of the principal, from `users.toml`. **Consent** is an attribute of the data: the learner's `marketing_consent` and `ai_training_consent` columns in `crm.learners`. The **purpose** is an attribute of the request. Each one becomes a row filter, a structured condition on a column that the engine adds to the query. The support agent's filter reads the agent's region:

```rego
row_filters contains {"column": "support_region", "equals": user.region} if {
	"support" in groups
	table in {"crm.learners", "support.tickets"}
}
```

The example asks for the filters of three support agents, an instructor, marketing, and a data scientist reading reviews twice, once with no purpose and once for `ai-training`:

[Example: attributes-as-row-filters](../examples/attributes-as-row-filters.py)

The three agents get the same rule with three values, one per region, from one role. Instructor 17's filter lists their courses, generated from `learning.courses`. Marketing gets two filters, consent given and account active, and an engine applies both. The data scientist's two reads show what the purpose does. With no purpose, every review is visible, but the review text, `body`, is shown as `{{out:attributes-as-row-filters:analyst-1 reads learning.reviews, body shown as}}`. For `ai-training`, only the reviews of learners who agreed to AI training remain, and the text comes back as `{{out:attributes-as-row-filters:analyst-1 for ai-training reads learning.reviews, body shown as}}`, with contact details taken out. That filter, `learner_consented`, tests the review's author, not the review, so enforcing it takes a join to `crm.learners`. A learner who signed up before the platform asked about AI training has no answer in `ai_training_consent`, according to the platform's data card, so enforcing the filter has to count an unknown answer as a no.

The purpose is the one attribute the requester supplies, so it reveals more only under a narrower condition: stating `ai-training` shows review text, but only for consenting learners and without contact details. Leaving it out, or giving any other purpose, gets what the role allows anyway, with the text hidden.

**Working rule:** Model each condition as an attribute of the principal, the data or the request, read by one rule per role, rather than as another role. When an attribute comes from the requester, as a purpose does, let it unlock access only under a narrower condition, so that leaving it out, or getting it wrong, falls back to what the role allows anyway.

## Return the decision as one document {#return-the-decision-as-one-document}

A yes or no can't carry the [row filters](#narrow-by-attribute) that attributes produce, but a Rego decision isn't limited to yes or no: "OPA policies can represent decisions as arbitrary structured data (e.g., booleans, strings, maps, maps of lists of maps, etc.)" ([OPA philosophy](https://www.openpolicyagent.org/docs/philosophy)). The platform's policy uses that to return its whole answer as one document, `data.platform.access.decision`, with four keys: `allow`, the `row_filters`, the `column_masks`, and the `reasons`. `lib.policy.decide` asks for it with one query and returns it as a Python dictionary.

The filters and masks are data, never SQL. A filter names a column and a value or a list of values, or, for consent, the learner's consent column; a mask is a name, one of `redact`, `last4`, `pseudonymize` and `redact_contacts`. Whatever enforces the decision turns each into SQL with its own code, so nothing a request or a user record contains, such as a region typed into `users.toml`, can reach a query as SQL text. Masks follow each column's classification from the metadata. Sensitive columns are hidden from everyone, except the last four digits of a card number for finance. Personal columns are shown to support, which needs contact details, and masked for everyone else as their job requires. And a column the metadata doesn't classify is treated as sensitive:

```rego
classification(column) := level if {
	level := data.platform.classifications[table][column]
} else := "sensitive"
```

For the platform, take marketing reading four columns of `crm.learners`. Three are in the metadata. The fourth, `linkedin_url`, stands for a column that a pipeline has added to the table and that nobody has classified yet:

[Example: one-decision-document](../examples/one-decision-document.py)

The document allows the read, with marketing's two row filters. Two columns are masked: `phone`, which is personal and which marketing doesn't need for email, and `linkedin_url`, which is unclassified and so treated as sensitive until someone reviews it. `full_name` and `email` have no mask, and a column without a mask is shown as stored: marketing needs both to send an email. The reasons give one line per group that allowed the read and one per mask, so the document explains itself.

A denial carries empty filters and masks. The policy builds the two outcomes with two separate rules, so nothing downstream can mistake a denial for a read with some columns masked.

**Working rule:** Return one decision document per request, holding the allow, the row filters, the column masks and the reasons, and read it as a whole. Keep filters and masks as names and values that your enforcement code translates, never as SQL that the policy writes. If an engine needs SQL from the policy, as Trino does for its row filters and column masks ([Trino 484, OPA access control](https://trino.io/docs/484/security/opa-access-control.html)), generate it in one tested function between the two.

## Unit-test the policy in Rego {#unit-test-the-policy-in-rego}

The [decision document](#return-the-decision-as-one-document) is what a test can compare. That matters, because a policy decides who sees personal data, and a change to it can widen access without anyone seeing it in a diff. OPA's answer is tests written in Rego: a test is a rule whose name is "prefixed with test_", and "If the test rule is undefined or generates a non-true value the test result is reported as FAIL" ([OPA policy testing](https://www.openpolicyagent.org/docs/policy-testing)). A test sets up its request with `with`, which replaces part of `input` or `data` for one expression.

The book's engine, regopy, has no `opa test`, so `lib.policy.run_tests` plays its part: it finds every `test_` rule in the folder's `_test.rego` files, evaluates each, and reports PASS when the rule is true, FAIL when it's false or undefined, and ERROR when evaluation fails. Each of the platform's tests sets the user and the action as two separate paths, each to a literal value, for reasons [the section on regopy](#where-regopy-differs-from-opa) shows:

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

The run passes {{out:policy-unit-tests:tests passed}} tests. A test that checks only `decision.allow` would miss most mistakes, so every test of an allowed read compares the row filters and the column masks whole. In finance's test, `decision.row_filters == []` fails if the policy adds any filter to finance's reads, and `decision.column_masks == {"card_number": "last4"}` fails if it masks one column more or one fewer, or shows the card number another way.

**Working rule:** Write a test for every rule that grants or narrows access, and one for every way a request can be denied, and compare whole filters and masks, not single keys. Run the tests on every change to the policy and to the data the policy reads.

## Check who sees what from a decision table {#check-who-sees-what}

[Unit tests](#unit-test-the-policy-in-rego) are written in Rego by whoever writes the policy. The people who own the data, such as the support lead or the finance team, should be able to check the policy too, without reading Rego. A decision table gives them that: one row per request, saying who reads which table and what they must get back, in the terms of their job.

The platform's table is `lib.policy.WHO_SEES_WHAT`, a list of rows in Python. Each gives a user, a table, the columns read and an optional purpose, then whether the read is allowed, which columns the row filters test and which columns are masked, and how. The example runs every row through `lib.policy.decide`, which returns the [decision document](#return-the-decision-as-one-document), and prints what the policy decided, one line per row: the form to show the data's owners.

[Example: who-sees-what](../examples/who-sees-what.py)

{{out:who-sees-what:rows of the table the policy matches}} rows match. Read down the table and each role's rules show up as outcomes: the support agent filtered by region and denied payments; the instructor filtered by course and denied the learners table; finance seeing `card_number` as `last4` where the data scientist sees `redact`; the data scientist's two reads of reviews, with and without a purpose. A reviewer who thinks a row is wrong can say so in a pull request without knowing which rule produced it.

**Working rule:** Keep a who-sees-what table in the repository with the policy, run it with the unit tests, and show its printed form to the data's owners, in terms they can check. When an owner disagrees with a row, change the row first, and let the failing row drive the change to the policy.

## Know where regopy differs from OPA {#where-regopy-differs-from-opa}

The platform's [tests](#unit-test-the-policy-in-rego) run in regopy because the book's examples must run offline, in seconds, inside Python, and OPA is a separate program. regopy {{version:regopy}} is close to OPA: its engine says "We support v1.18.1 of Rego as defined by OPA" and that it passes "all the non-builtin specific test suites" OPA publishes, and it lists the built-ins it doesn't support, such as `net.*` and `http.send` ([rego-cpp README at v1.5.2](https://github.com/microsoft/rego-cpp/blob/v1.5.2/README.md)). regopy also differs from OPA in ways its README doesn't list. The platform's policy and tests avoid each of the cases that follow, and OPA itself is the reference for anything in production.

The first group runs without any error:

[Example: regopy-silent-differences](../examples/regopy-silent-differences.py)

- **A false decision comes back undefined.** Asked for `data.example.allow` on its own, regopy answers `{{out:regopy-silent-differences:a false decision, queried on its own}}` where the value is `false`, so "denied" can't be told from "no decision". Bound to a variable, the same query gives `{{out:regopy-silent-differences:the same decision, bound to a variable}}`. `lib.policy` binds every decision it reads.
- **Pre-1.0 syntax runs.** Since OPA 1.0, "Using `if` for all rule definitions and `contains` for multi-value rules is now mandatory" ([OPA v1.0.0 release notes](https://github.com/open-policy-agent/opa/releases/tag/v1.0.0)), and rego-cpp's changelog says that "Support for v0 Rego has been dropped" ([rego-cpp changelog](https://github.com/microsoft/rego-cpp/blob/v1.5.2/CHANGELOG)). Yet regopy evaluates `allow { ... }` and answers `{{out:regopy-silent-differences:a rule in pre-1.0 syntax}}`, so a policy can pass here and fail to load in OPA, and so behind Trino.
- **An unbound variable switches a rule off.** OPA requires a variable in a comparison to "appear in another expression in the same rule that would cause the variable to be bound" ([OPA policy language](https://www.openpolicyagent.org/docs/policy-language)), and rejects the policy otherwise. regopy runs it: a `deny` with `regoin` misspelt for `region` is silently undefined, so `allow if not deny` allows an `apac` request: `{{out:regopy-silent-differences:a deny with a misspelt, unbound variable, for apac}}`.
- **A mock that refers to another rule is ignored.** In a policy whose `region` rule reads `input.user.region`, a mock written as a literal, `with input.user as {"region": "emea"}`, gives `{{out:regopy-silent-differences:region under a mock written as a literal}}`. Written as a reference to a rule holding the same object, it gives the real input's `{{out:regopy-silent-differences:region under a mock that refers to a rule}}`.

The second group fails, and one of the failures takes Python down with it. The first example adds a test to a copy of the platform's tests, and the second formats the same string from nine sources:

[Example: regopy-failed-outputs](../examples/regopy-failed-outputs.py)

[Example: regopy-sprintf-quotes](../examples/regopy-sprintf-quotes.py)

- **Replacing the whole input breaks every query.** One extra test that writes its request as `with input as {...}`, and evaluates rules that call built-ins such as `sprintf`, makes every query in that interpreter fail. Every test then ends in ERROR, {{out:regopy-failed-outputs:tests that end in ERROR}}, and so does the first query asked of a fresh interpreter loaded with the same files, before any test runs: asked whether the query `true` succeeded, regopy says `{{out:regopy-failed-outputs:the query true succeeds}}`. Replacing one path at a time, as the platform's tests do, works.
- **Printing a failed output crashes Python.** Converting one of those failed outputs to a string ends the process with `{{out:regopy-failed-outputs:printing the failed output ends Python with}}`. So `lib.policy` checks `output.ok()` before it reads anything, and a decision asked of that interpreter raises an error instead: `{{out:regopy-failed-outputs:lib.policy.decide raises}}`.
- **`sprintf` keeps stray quotes.** Formatting a string returned by some built-ins, such as `object.get`'s default, `lower` or `concat`, or a local variable set to a literal, keeps its quotes, and regopy's own output is then invalid JSON, `{{out:regopy-sprintf-quotes:from object.get, its default, regopy's invalid JSON}}`, which its Python wrapper fails to parse. Strings from `input`, a rule, a function, iteration and `object.get`'s found value format cleanly, and the platform's policy formats only strings like those.

**Working rule:** Treat regopy as a fast local evaluator and OPA as the reference. Write Rego v1 only, bind every decision to a variable, mock input one path at a time with literal values, and check that an output succeeded before reading it. Before a policy guards production data, check and test it with OPA itself, with `opa check --strict` and `opa test`.

## Write the same rules in Cedar {#the-same-rules-in-cedar}

Rego isn't the only language for policies like the platform's. Cedar, which its designers present as a language for "Expressive, Fast, Safe, and Analyzable Authorization" ([Cutler et al., 2024](https://arxiv.org/abs/2403.04651)), takes a narrower view of a decision. A Cedar request names a principal, an action and a resource, and the answer follows three rules: "default deny", "forbid overrides permit", and "skip on error: if a policy's evaluation returns error, the policy does not factor into the authorization response; it is skipped" ([Cedar, authorization](https://docs.cedarpolicy.com/auth/authorization.html)). A schema declares every type of entity, the kinds of principal and resource a request can name, with its attributes, and "By default, attributes that you define are required" ([Cedar, schema](https://docs.cedarpolicy.com/schema/schema.html)). Validating policies against the schema catches mistakes before any request: "If your policies are deemed valid, they are sure not to exhibit most errors that could arise during request evaluation, for requests that adhere to the expectations defined by the schema" ([Cedar, validation](https://docs.cedarpolicy.com/policies/validation.html)). The book's examples run Cedar with cedarpy {{version:cedarpy}}, which "helps you use the (Rust) Cedar Policy library from Python" ([cedarpy](https://pypi.org/project/cedarpy/4.12.2/)).

The platform's rules in Cedar are in `governance/policies/access.cedar`, with the schema in `access.cedarschema`. A `permit` per group repeats the Rego policy's [`readable` map](#deny-by-default-then-allow-by-role). A `forbid` keeps support agents to learners in their own region, and another hides sensitive columns from everyone except finance's view of card numbers. `lib.policy.cedar_entities` generates the users, groups, tables and columns from the same [data document](#generate-the-data-the-policy-reads) the Rego policy reads, and the example adds the first learner of `crm.learners` in each region:

[Example: cedar-same-rules](../examples/cedar-same-rules.py)

The policies validate against the schema, and misspelling `region` gives a precise error before anything runs: {{out:cedar-same-rules:with region misspelt}}. Rego has no such check by default. The same typo in an attribute, `user.regoin` in the support agent's row filter, is undefined in OPA and regopy alike, and `opa check --strict` passes it, because OPA checks references against a schema only when you give it one ([OPA policy language, schemas](https://www.openpolicyagent.org/docs/policy-language#using-schemas-to-enhance-the-rego-type-checker)), and the platform gives none. The agent's row filter disappears, and only a test that compares the filters, such as `test_support_reads_only_its_own_region`, catches it. The decisions agree with the Rego policy where Cedar can express them. Finance may read the `card_number` column, and the data scientist may not, where Rego allows the read and redacts the column. The data scientist may read `amount`, and marketing may not read payments. The `emea` agent may read the `emea` learner and neither of the others.

The last line of the output is everything Cedar returns for finance's card numbers: a decision, and the ID of the policy that decided it. cedarpy names the policies by position, `policy3` for the fourth, rather than by their `@id` annotations, such as `finance-tables`. Cedar's documentation explains why that's allowed: "@id is not special in the Cedar language … The Cedar CLI uses the @id annotation to set policy IDs, but other interfaces, such as the Cedar APIs, have other ways to set policy IDs" ([Cedar, syntax](https://docs.cedarpolicy.com/policies/syntax-policy.html)). Nothing in the answer says `last4`, the [mask](#return-the-decision-as-one-document) the Rego policy returns for finance's card numbers, because Cedar can permit a column but has no way to say "show the last four digits": that rule would move into the enforcement code, outside the policy and its tests. Rows are the same. Cedar decides one learner at a time, so filtering the platform's learners means one request per learner, or Cedar's experimental partial evaluation, which "evaluates whatever is known and returns residual policies for the parts that aren't", and whose result "is not a final authorization decision" ([cedarpy](https://pypi.org/project/cedarpy/4.12.2/)). Cedar's paper makes the trade-off from the other side: "Rego's added expressiveness is a blessing but also a curse. It provides no built-in authorization concepts such as 'allow' or 'deny'". The same paper reports Cedar 42.8 to 80.8 times faster than Rego at the median, on three workloads with transitive relations, with Cedar 3.0.1 against OPA's Rego 0.61.0. Those are its designers' measurements, not this book's, and they say nothing about regopy.

**Working rule:** Choose Cedar when every question is "may this principal do this to this resource?", as in an application's permission checks, and you want a schema to check every policy before it runs. Choose Rego when the answer has to carry data, as a warehouse's row filters and column masks do, or when the engine already asks OPA, as Trino does. For the platform's warehouse, we recommend Rego.

## What goes wrong in production {#policies-in-production}

We consider the most damaging failure a rule that silently doesn't apply because an attribute is missing, and it happens in both languages. In Rego, "Expressions that refer to undefined values are also undefined" ([OPA policy language](https://www.openpolicyagent.org/docs/policy-language)). The support agent's [row filter](#narrow-by-attribute) takes its value from `user.region`, so for a user without a region the filter doesn't exist at all. A rule that allows by group still allows, and no filter means every row.

To see it, take a copy of the platform's policy and tests, in a temporary folder, with the one line `not missing_attribute` taken out of [`allow`](#deny-by-default-then-allow-by-role). Then add a support agent to the data with their group and no region, as happens when someone is given a role before anyone sets their region, and ask both policies for the agent's tickets:

[Example: missing-region-guard](../examples/missing-region-guard.py)

With the guard, the agent is denied, and the reason names what is missing: {{out:missing-region-guard:with the guard, reasons}}. Without it, the request is allowed with row filters `{{out:missing-region-guard:without the guard, row filters}}`, and the agent sees the tickets of {{out:missing-region-guard:without the guard, regions whose tickets the agent sees}}: every region, including every learner's message and contact details. The decision's own reasons say the region is missing, but nothing in `allow` asked. `default allow := false` didn't help either, because a rule did allow. One test catches it, `{{out:missing-region-guard:tests that fail without the guard}}`, which is why the policy's tests include the case of the missing attribute.

Cedar fails the same way, through [skip on error](#the-same-rules-in-cedar). Take the support agents' `forbid` from the platform's Cedar policies without its `has region` check, so it reads `principal.region` on an agent who has none:

[Example: cedar-missing-region](../examples/cedar-missing-region.py)

With the check, the agent is denied every learner. Without it, evaluating the `forbid` raises an error, Cedar skips the policy, the support `permit` still applies, and the agent may read the learners of all three regions. Passing the schema to `is_authorized` didn't stop it, because the schema declares `region` optional: users outside support have none. Validation did, before any request: {{out:cedar-missing-region:validation without it}}.

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
