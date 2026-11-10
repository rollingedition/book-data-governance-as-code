---
description: "Data governance as code for warehouses built on Iceberg, Parquet and SQL: a metadata file tested against the catalog, personal data found and measured, column lineage from pipeline events, access policies in tested Rego enforced on every query, retention, consent and erasure, audit logs, consent-checked AI training data, and governance checks as a CI gate. Use when cataloging owners and classifications, finding personal data, tracing lineage, writing or enforcing access policies, deleting personal data, auditing access, building training corpora, or gating pull requests on governance."
---

## Keep a catalog's metadata in a reviewed file and test it

**When:** Owners, descriptions and classifications live in a web page, a wiki or someone's memory, and must be reviewed and checked like pipeline code.

1. Move owners, domains, descriptions and classifications into one file in the repository, changed only by pull request. Use a closed list of classifications and one owning team per table. [Keep the metadata in a reviewed file](#keep-metadata-in-a-reviewed-file)
2. Key each column's metadata by its Iceberg field ID, with the name beside it, so a rename is reviewed rather than read as a new column. [Key column metadata by field ID](#key-column-metadata-by-field-id)
3. Write column descriptions into the table's schema as `doc`. Compare schema changes by field IDs, names and types, never by schema IDs. [Write descriptions into the table's schema](#write-descriptions-into-the-schema), [A description edit is a schema change](#a-description-edit-is-a-schema-change)
4. Walk the catalog, not the metadata file, and fail on any table without an owner or any column without a classification. [Test the catalog against the metadata](#test-the-catalog-against-the-metadata)
5. On every schema change, run `problems` and `dropped`. Remove a dropped column's entry in the pull request that drops the column. [Catch schema changes before the metadata goes stale](#catch-schema-changes)

**Check:** Run [the example](examples/metadata-gaps.py). Its output includes `problems found: {{out:metadata-gaps:problems found}}`. Fix each table it names in the file.

**Pitfalls:**
- Two sources of metadata overwrite each other. Choose one, and make the other read-only or generated.
- Metadata keyed by name goes stale on a rename. Generate every name-keyed view from the ID-keyed file.
- Dropping a personal column doesn't remove its values, which time travel reads until their snapshots expire.
- A table created outside the pipelines escapes the test. Run `problems` on a schedule against production.

## Find personal data in columns and text, and measure the detector

**When:** You must decide which columns hold personal data, or mask names and contact details in free text, and need to know what the detector misses.

1. Run name rules over every column first. They may raise a classification and never lower one. [Classify columns from the schema](#classify-columns-from-the-schema)
2. Sample non-null values of every free-text column with a fixed seed, and keep the seed with the result. Size the sample from the rarest personal data you must catch. [Sample a column's values the same way every run](#sample-column-values)
3. Propose the strictest classification that the name or any sampled value supports, with `internal` as the floor. [Classify columns from their values](#classify-columns-from-their-values)
4. Before a detector masks anything, measure recall per entity type, by exact and by overlap matching, against labelled spans. [Measure a detector against labelled spans](#measure-against-labelled-spans)
5. Keep only the recognizers your text can hold, each with a threshold set from its own scores. Never run with Presidio's default threshold of 0. [Tune the recognizers against the truth](#tune-the-recognizers)
6. Read the misses by case, not by total. Use patterns only for what has a checkable shape, never for names or addresses. [See where patterns fail](#where-patterns-fail)

**Check:** Run [the example](examples/measure-pattern-recognizers.py). Its output includes `CREDIT_CARD, recall, exact: {{out:measure-pattern-recognizers:CREDIT_CARD, recall, exact}}`. Measure card recall the same way on your own labelled text.

**Pitfalls:**
- Under Presidio's defaults, every run of 8 to 17 digits is read as a US bank number. Count detections per label to notice.
- Patterns find no names, and models miss some lowercase names and first names that are also words.
- A column called `notes` gets no name rule. Sample every text column.
- `AnalyzerEngine()` downloads a model at run time. Configure the engine, pin what it fetches, and run the tests with the network off.

## Build column lineage from pipeline run events

**When:** You need the source columns a derived column depends on, the tables a column change reaches, and the classification a derived column should carry.

1. Send a START event, then a COMPLETE or FAIL event, from the code that runs each job. Name datasets by catalog identifier, never by path. [Send a run event from every pipeline](#send-run-events)
2. Trace each output column with sqlglot's `lineage`, passing the catalog's schema generated from the table metadata. [Trace each output column to its sources](#trace-output-columns)
3. Record the INDIRECT inputs too: the join keys, filters and grouping columns of every `SELECT`. [Add the inputs that decide which rows exist](#add-indirect-inputs)
4. Treat a statement the parser can't read correctly as an error, never as empty lineage. Refuse `MERGE`, or record its lineage from a `SELECT` of the columns it writes. [Fail on statements the parser can't read](#fail-on-unreadable-lineage)
5. Run impact analysis on the graph built from events, INDIRECT edges included. Send each report to the owners of the tables it reaches. [Find what a column change breaks](#impact-of-a-column-change)
6. Propose each derived column's classification as the stricter of its inputs', counting INDIRECT inputs, and send it to the table's owner. [Propose classifications from lineage](#classifications-from-lineage)

**Check:** Run [the example](examples/indirect-column-lineage.py). Its output has `source columns, values only: {{out:indirect-column-lineage:source columns, values only}}` and `source columns, with the rows' inputs: {{out:indirect-column-lineage:source columns, with the rows' inputs}}`. If the second count equals the first, the INDIRECT inputs are missing.

**Pitfalls:**
- The client drops every event when `OPENLINEAGE_DISABLED` is `true`, and logs one INFO line. After every scheduled run, compare the jobs that ran with the COMPLETE events that arrived.
- A job nobody instrumented leaves a table no lineage accounts for. Treat it as an incident.
- A table classified `internal` can hold personal copies. Fail when a declared classification is lower than lineage proposes, and raise it.

## Write access policies as tested Rego

**When:** You must decide which roles may read which tables, rows and columns, and need that decision written as code that tests can check.

1. Generate the policy's data from the file or table that owns each fact, every time you load the policy. [Generate the data the policy reads](#generate-the-data-the-policy-reads)
2. Start the policy with `default allow := false`, and allow only through rules that name the role and the resource. Never weaken the default. [Deny by default, then allow by role](#deny-by-default-then-allow-by-role)
3. Model each condition as an attribute read by one rule per role. Let a requester-supplied attribute, such as a purpose, unlock access only under a narrower condition. [Narrow by attribute](#narrow-by-attribute)
4. Return one decision document per request, holding the allow, the row filters, the column masks and the reasons. [Return the decision as one document](#return-the-decision-as-one-document)
5. Write a test for every rule that grants or narrows access, and for every way a request can be denied. Compare whole filters and masks. [Unit-test the policy in Rego](#unit-test-the-policy-in-rego)
6. Keep a who-sees-what table with the policy and run it with the tests. [Check who sees what from a decision table](#check-who-sees-what)

**Check:** Run [the example](examples/missing-region-guard.py). Its output includes `with the guard, allowed: {{out:missing-region-guard:with the guard, allowed}}`. A support agent with no region must be denied, so this line must read False.

**Pitfalls:**
- A missing attribute disables a rule silently, because an undefined expression is undefined. Guard `allow`, and deny explicitly when a required attribute is absent.
- A misspelt attribute passes `opa check --strict`. Compare whole filters in the tests.
- regopy can accept a policy that OPA refuses. Check and test every policy with OPA before it guards production data.
- A change to the users file takes effect only when the data document is rebuilt.

## Enforce policies on every query

**When:** Analysts, tools or assistants query the warehouse, and each may see only the rows and columns its decision allows.

1. Ask the policy about every table and column a query names, with `*` expanded against the catalog's schema. Deny anything that schema doesn't hold. [Find every table and column a query reads](#find-what-a-query-reads)
2. Check every source against an allowlist of catalog tables, CTEs in scope and subqueries, and deny the rest. [Allow only catalog tables, CTEs and subqueries](#allow-only-catalog-sources)
3. Don't enforce masks by rewriting column references. Replace each protected table with a subquery generated from the decision. [Replace each table with a filtered, masked subquery](#replace-each-table)
4. Build every row filter from the decision's values with a SQL builder, and deny any filter shape the layer doesn't recognize. [Build row filters from the decision's data](#build-row-filters-from-data)
5. Run the who-sees-what cases as queries through the layer on every change, and fail the build when a case disagrees. [Test who sees what by running queries](#test-who-sees-what)
6. Run an assistant's queries with the identity of the person it answers. [Query on someone's behalf](#query-on-someones-behalf)

**Check:** Run [the example](examples/who-sees-what-in-queries.py). Its output includes `marketing-1, crm.learners: {{out:who-sees-what-in-queries:marketing-1, crm.learners}}`. Marketing must read fewer rows than the table holds, with the masked column named.

**Pitfalls:**
- `SELECT *` passes a table missing from the schema without an error. Deny every table the catalog doesn't hold.
- Direct file reads, such as `read_parquet`, name no table and bypass the rewrite. Deny them, and turn off external access.
- Error messages print values. Return a generic error at the service boundary.
- DuckDB is not a security boundary. Enforce in an engine that runs as a service of its own, such as Trino.

## Apply retention, consent and erasure as code

**When:** You must delete personal data on a schedule or when a learner asks, and prove it is gone from every table, snapshot and file.

1. Keep each table's retention period, the column it counts from and the state a row must be in, in a reviewed file. Apply it against a day the scheduler passes in, never the clock. [Keep retention rules in a reviewed file](#retention-rules-in-a-file)
2. Apply retention as Iceberg row deletes, one rule at a time, recording the rule and the day in each commit's snapshot summary. [Delete expired rows with row deletes](#delete-expired-rows)
3. Keep consent in one table that the query reads as a row filter, so a withdrawal changes the next query. [Read consent where the query runs](#consent-at-query-time)
4. Plan each erasure from the catalog and the lineage graph, not from a list someone maintains. Run `lib.lineage.unaccounted` first. [Plan an erasure from lineage](#plan-erasure-from-lineage)
5. Carry out the erasure as row deletes in the plan's order, and record only a request ID in each commit. [Erase the rows the plan lists](#erase-the-rows)
6. Expire the snapshots, remove the files no snapshot references, then scan every file the platform stores. Treat a file that still holds the person as a failed erasure. [Expire snapshots by the table's own settings](#expire-snapshots), [Remove the files no snapshot references](#remove-unreferenced-files), [Prove the erasure by reading every file](#verify-the-erasure)

**Check:** Run [the example](examples/erase-learner.py). Its output includes `crm.learners, rows deleted: {{out:erase-learner:crm.learners, rows deleted}}`. The count must match the rows the erasure plan lists for `crm.learners`.

**Pitfalls:**
- Old snapshots and files keep deleted data readable by time travel. Run expiry and the file sweep in the same job, then the scan.
- An erased learner comes back after a nightly reload from the source. Erase in the source system first, and keep a register of keyed hashes that every load filters against.
- Backups keep what the sweep deletes. Decide with their owners how they age out, and record that with each request.
- Retention counted from the wrong date deletes early or late. Store the date the rule means.

## Audit every decision and check the log against the engine

**When:** Someone asks who read which data and why, and you must show the record was not altered and matches what the engine ran.

1. Write the audit record in the enforcement layer, before the query runs. Record every identity involved and each table's decision. [Record every decision where it's made](#record-every-decision)
2. Record a query's shape, never its values. Keep the executed SQL only as a keyed digest. [Keep values out of the record](#keep-values-out-of-the-record)
3. Fingerprint every decision with a hash of the policy's modules and its data document together. Explain an old decision under its own revision. [Replay an old decision under its own policy](#replay-old-decisions)
4. Chain every record to the one before with SHA-256 and a sequence number, and write the head down outside the layer's control. [Chain the records so an edit shows](#chain-the-records)
5. Check the engine's own query log on a schedule against the records. Every query must match a record, and every allowed record must match a query. [Cross-check the log against DuckDB's query log](#cross-check-the-query-log)
6. Generate access reviews from the policy and the audit log, never from role lists. [Generate the access review from the log](#generate-the-access-review)

**Check:** Run [the example](examples/chain-catches-edit.py). Its output includes `record 3 edited, checked alone: {{out:chain-catches-edit:record 3 edited, checked alone}}`. An edited record must fail the check.

**Pitfalls:**
- The audited code can switch off its own logging. DuckDB's query log belongs to the process it records. Enforce in a service with its own log.
- Personal data reaches logs through query literals, engine messages and debugging output. Scrub each route.
- OPA drops decision events when its rate limit is exceeded. Count the engine's queries against the decision records.
- A head kept next to the log detects nothing the chain doesn't. Keep it in another system, under other credentials.

## Build AI training data you can defend

**When:** You build a corpus or index from reviews or other personal text, and each record's consent, personal data, duplicates and provenance must be handled.

1. Admit only records whose consent answer is yes for the use. Never filter out the refusals and keep the rest. [Admit only reviews whose authors consented](#admit-consented-reviews)
2. Detect personal data in the text after the enforcement layer's masks, and measure what's left, by value. [Replace personal data in the text, then measure what's left](#replace-personal-data)
3. Make pseudonyms with HMAC under a key only the job holds. [Keep pseudonyms consistent with a keyed hash](#consistent-pseudonyms)
4. Deduplicate exact copies by a hash of the normalized text, then near-duplicates by MinHash, and confirm each candidate by its exact similarity. [Find near-duplicates with MinHash](#near-duplicates-with-minhash)
5. Tune LSH below your similarity threshold, so it misses few pairs, and measure its misses on a sample. [Tune LSH for recall, then confirm every pair](#tune-lsh-then-confirm)
6. Give every record its source table, row ID, and the job and run that wrote it. [Carry provenance on every record](#carry-provenance)
7. Generate the dataset card in the run that builds the data, and check it with your own code. [Generate the dataset card from the run](#generate-the-dataset-card)

**Check:** Run [the example](examples/corpus-consent-filter.py). Its output has `reviews the governed build reads, as analyst-1 for ai-training: {{out:corpus-consent-filter:reviews the governed build reads, as analyst-1 for ai-training}}` and `reviews a filter on refusals keeps: {{out:corpus-consent-filter:reviews a filter on refusals keeps}}`. The gap between them is consent the refusal filter never checked.

**Pitfalls:**
- A filter on refusals keeps the unknowns, mostly from learners never asked. Count records per consent answer through their provenance.
- A learner withdraws after a build, but the earlier build and any model made from it still hold the record. Record the run ID of each model's build, and check every build against today's consent before training.
- A pseudonym key that leaks lets anyone with a list of names compute every pseudonym. Rotate the key and rebuild.
- Presidio's `hash` operator gives a different output on each call at the pinned version. Never use it without a salt for pseudonyms.

## Run governance checks as one gate on every pull request

**When:** Rules for schemas, classifications, owners, policies and migrations must block a merge the way tests do, and the gate must stay on.

1. Run every check as one job on every pull request, each reporting all its findings under stable IDs. A check that crashes becomes a failing finding. [Run every check as one job](#run-every-rule-as-one-job)
2. Make that job the gate, required by branch protection on main, and put every finding in the pull request. [Gate the pull request, not the commit](#gate-the-pull-request)
3. Diff schemas by field ID. Report each rename, type change and removal: which readers it affects, which pipelines name the column, and what it reaches through lineage. [Report what a schema change reaches](#report-what-a-schema-change-reaches)
4. Require the table owner's approval on main for a lowered classification, a changed owner or a move to another domain. [Ask the owner before lowering a classification](#ask-the-owner-before-lowering)
5. Remove a column in two pull requests. Deprecate it first, then drop it. [Deprecate a column before removing it](#deprecate-before-removing)
6. Let a waiver name exactly one finding, with an owning team, a reason and an end date. Count, per check, the findings waived or left on main. [Count waived findings per check](#count-waived-findings)

**Check:** Run [the example](examples/governance-ci-report.py). Its report has the line `coverage: {{out:governance-ci-report:coverage}}`. A failing check must fail the job, not only appear on a dashboard.

**Pitfalls:**
- A job that branch protection doesn't require reports its findings, and the pull request merges anyway. Require the status check.
- A check that flags every ID buries real findings. Run it on main and read every finding first. If most would be waived, change the rule.
- A broken file can hide every other finding. Turn the crash into one finding that names the file, and run the other checks anyway.
- A team whose pull request fails may remove the check or add a waiver. Make the platform team the code owner of the check settings, the waivers and CODEOWNERS.
