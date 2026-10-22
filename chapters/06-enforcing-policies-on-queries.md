---
chapter_id: enforcing-policies-on-queries
---

# Enforcing policies on queries

By the end of this chapter you can enforce access decisions on every query the platform runs. You'll rewrite each query so that every table it reads is replaced by a subquery that keeps only the rows the user may see and masks the columns they may not, deny every query that reads anything else, test who gets what by running real queries, and say where a rewrite in front of DuckDB stops protecting anything.

The platform, the online learning platform this book follows, sells professional courses to adult learners, and its warehouse holds their contact details, their payments and the text they write. [Chapter 5](#access-policies-as-code) wrote the platform's access policy in Rego. Given a user, a table, the columns they want and, optionally, why they want them, the policy returns a **decision**: a document that says whether to `allow` the read, which **row filters** keep the rows the user may see, which **column masks** change how a column is shown (`redact`, `last4`, `pseudonymize` or `redact_contacts`), and the `reasons`. A decision does nothing on its own. A support agent's SQL reaches DuckDB as text, and something between the two has to apply the decision to every table that text reads, however the query is written.

That layer is this chapter's subject: the book's shared module `lib.enforce`, which we call the **enforcement layer**. Its `Enforcer` holds four things: a DuckDB connection with a view for each catalog table (`lib.warehouse.connect`), the tables' schema, the policy and a key for pseudonyms. Users never get the connection. `Enforcer.sql(query, user)` rewrites a user's query and runs the rewrite. A service exposes only `lib.enforce.answer`, which calls it and returns the rows: never the relation, a handle on the connection, or `Enforcer.run`, which executes SQL the layer wrote with the key attached. The examples run sqlglot {{version:sqlglot}} and DuckDB {{version:duckdb}}.

## Find every table and column a query reads {#find-what-a-query-reads}

A policy can only decide about what it's asked, so before the enforcement layer asks, it has to know every table a query reads and which columns it reads from each. Reading that off SQL text is a parsing problem with traps: `*`, aliases, joins with `USING`, common table expressions (CTEs) and identifiers in any case. sqlglot parses a query into a tree, and its `qualify` step resolves every column against a schema, qualifies it with its table, and expands `*` into the columns it stands for when it has a schema to expand against ([`qualify` at v30.24.0](https://github.com/tobymao/sqlglot/blob/v30.24.0/sqlglot/optimizer/qualify.py)). The enforcement layer builds that schema when it starts: it lists the catalog's tables, and reads each one's columns and types from the view `lib.warehouse.connect` created for it, so a view DuckDB holds for anything else never enters the schema.

For the platform, `Enforcer.reads` returns the columns each query reads from each table:

[Example: tables-a-query-reads](../examples/tables-a-query-reads.py)

`SELECT *` becomes every column of `crm.learners`, and the join reads `learner_id` from both tables, because the `USING` clause compares them, though neither appears in the output. In the query with a CTE, the outer query returns only `email`, but the CTE names every column of `crm.learners` except `notes`, so the layer asks about all of those: it asks about every column a query names anywhere, which can be more than the query returns, and never fewer. `"CRM"."LEARNERS"` resolves to `crm.learners`, because the layer normalizes identifiers the way DuckDB resolves them before it matches anything: DuckDB returns {{out:tables-a-query-reads:DuckDB, "CRM"."LEARNERS"}} of `crm.learners` for that name. The dialect matters: sqlglot lowercases `"LEARNERS"` for DuckDB and Trino, and keeps it as `{{out:tables-a-query-reads:postgres normalizes "LEARNERS" to}}` for PostgreSQL, so the layer normalizes with the dialect of the engine that runs the query.

`qualify` has one gap that decides the layer's design: it fails on an unknown column of a known table, but it passes over a table that isn't in the schema at all, and leaves that table's `*` unexpanded. Run it on a join with `marketing.audience`, which stands for a table a pipeline wrote after the schema was read, then send the same query and five other reads through the layer:

[Example: unknown-tables-denied](../examples/unknown-tables-denied.py)

`qualify` on its own returns the query with `SELECT *` intact and no error, so a layer that trusted it would ask the policy about `crm.learners` and pass `marketing.audience` through unchecked. The enforcement layer denies every table that isn't in the catalog before it asks the policy anything. A column the table doesn't have is denied by `qualify` itself. The other four reads show why that rule is broad. `lib.warehouse` registers each table's scan with DuckDB under a name of its own, such as `scan_crm_learners`. Run directly on the layer's connection, that scan returns {{out:unknown-tables-denied:the scan, run directly}} of raw learners. `read_parquet` reads a file, and `query_table` reads whatever table its string names, {{out:unknown-tables-denied:the table function, run directly}} when run directly. `SUMMARIZE` isn't a query the rewrite can analyse. The first three aren't catalog tables and the last isn't a query, so all four are denied. The layer's connection also has DuckDB's `enable_external_access` turned off, so a file read that got past the rewrite would still fail, with a `{{out:unknown-tables-denied:the file, run directly}}` ([Securing DuckDB](https://duckdb.org/docs/2.0/operations_manual/securing_duckdb/overview)).

**Working rule:** Ask the policy about every table and column a query names, with `*` expanded against the catalog's own schema, and deny anything that schema doesn't hold: unknown tables, files, table functions and statements other than a single query. A denial costs a user one question to the platform team; a pass-through costs whatever the unchecked table holds.

## Allow only catalog tables, CTEs and subqueries {#allow-only-catalog-sources}

[Denying names the catalog doesn't hold](#find-what-a-query-reads) isn't enough on its own, because a query can read data through sources that aren't table names. A table function can sit under `LATERAL`, or in a subquery in the select list. Outside a CTE's scope, or before the CTE is defined, DuckDB resolves its name to whatever else the connection holds under that name. A check that inspects only table names, and accepts any name a CTE uses, passes both.

So the enforcement layer checks sources against an allowlist. `Enforcer.check_sources` walks the whole query and requires every `FROM` and `JOIN` source, at every level, to be a catalog table, a CTE in scope or a subquery, and a `LATERAL` to be over a subquery. Everything else is denied, including table functions wherever they appear, `VALUES`, `UNNEST` and joins in parentheses. A CTE is in scope in its own query, below it and in later CTEs, and a recursive one also in its `UNION`'s recursive branch. An allowlist fails closed on a kind of source nobody thought of.

A query through the layer must also change nothing. DuckDB's scalar `write_log` writes into the engine's query log. `duckdb_functions()` marks such functions: `has_side_effects` is "`true` if this function changes the database state" ([DuckDB, `duckdb_functions`](https://duckdb.org/docs/2.0/sql/meta/duckdb_table_functions)). At {{version:duckdb}} it also marks functions that only vary, such as `random` and `uuid`. The layer denies every flagged function, and every macro that calls one, such as `pg_sleep`, reading the list from `duckdb_functions()` when it starts: a list sorted by hand into harmful and harmless is one an upgrade can outdate unnoticed, and a governed read seldom needs a random number.

`lib.enforce` keeps the cases as a regression test, `source_cases`, which [chapter 10's CI](#run-every-rule-as-one-job) runs, as it does [the order test](#gate-columns-on-row-filters) below:

[Example: catalog-sources-only](../examples/catalog-sources-only.py)

Each case is denied before any policy is asked, with a reason that names the source. The layer parses with sqlglot's warnings silenced, since sqlglot logs any statement it keeps as text, such as a `CALL`, values included ([sqlglot source, v30.24.0](https://github.com/tobymao/sqlglot/blob/v30.24.0/sqlglot/parser.py)), and every case checks that nothing was logged: {{out:catalog-sources-only:cases that pass}} cases pass. Two of them, run on the layer's own connection, show what the denials prevent: the name used before its CTE reads the scan of the same name, {{out:catalog-sources-only:a CTE's name before the CTE, run directly}} of raw learners, and `write_log` leaves `{{out:catalog-sources-only:the log function, run directly, query-log entries it leaves}}`: its own statement, and an entry no query made. Every function with side effects is denied, {{out:catalog-sources-only:functions with side effects, denied}}. The last line checks that the allowlist doesn't deny too much: run as `agent-a05`, the EMEA support agent, a CTE, a join and a scalar subquery over catalog tables return {{out:catalog-sources-only:a CTE, a join and a scalar subquery, allowed}}.

**Working rule:** Check every source a query reads against an allowlist of catalog tables, CTEs in scope and subqueries, and deny the rest, along with every function the engine marks as having side effects, read from its own list. Keep the cases as a regression test for every change to the layer and every engine upgrade, and add one whenever a new kind of source or function turns up.

## Masking column references leaks {#column-masks-leak}

With the columns known, the obvious enforcement rewrites them: wherever the query selects `email` and the decision masks it, select the mask instead. It needs nothing but the column list from [the first section](#find-what-a-query-reads), and it looks right on the queries people test with. It fails because a query can read a column without selecting it, and DuckDB can return a column without naming it.

On the platform, `analyst-1`, a data scientist, may read `crm.learners` with `email` and the other personal columns pseudonymized, and free text such as `notes` redacted. `Enforcer.mask_select_list` does the column rewrite, kept only to show this failure. The example prints its rewrite of a plain read, then runs five reads aimed at learner 2's email, with any stored email replaced by a placeholder, since the book's examples never print one. Before you run it, predict how many of the five the column rewrite stops:

[Example: select-list-masks-leak](../examples/select-list-masks-leak.py)

None. The rewrite of the plain read is correct: `email` comes back as a hash. The five reads get past it in three ways:

- **A `WHERE` on the raw column** never passes through the select list, so it compares the stored email. The analyst guesses an email and gets back learner 2's ID, `{{out:select-list-masks-leak:a WHERE on the raw column}}`, which links that person to every row they can join on `learner_id`.
- **The whole row as a struct, `COLUMNS(...)` and `to_json`** return the stored email, `{{out:select-list-masks-leak:to_json}}`, without any column reference named `email` for the rewrite to find: DuckDB expands the row, or matches column names against a pattern, itself.
- **A cast error** prints a stored email in its message. DuckDB's cast errors include the value, and PostgreSQL's documentation warns that "most casting functions include their input values in the error messages they produce" ([PostgreSQL 18, Rules and Privileges](https://www.postgresql.org/docs/18/rules-privileges.html)).

Masking the column wherever the query names it, in the `WHERE` clause too, would stop the first and the last read. It can't stop the middle three, because they never name the column.

**Working rule:** Don't enforce masks by rewriting the column references in a query. However many reference shapes you cover, the engine has more ways to read a column than the rewrite has to recognize one. Mask where the data comes from instead, so every read sees the masked value, which is what the [next section](#replace-each-table) does.

## Replace each table with a filtered, masked subquery {#replace-each-table}

[Rewriting column references](#column-masks-leak) leaks because the raw table stays in the query. The fix replaces the table. `Enforcer.rewrite` replaces each catalog table in the query, under the query's own alias for it, with a subquery built from the user's decision:

- every column of the table, in the table's order, so `*`, a struct or `COLUMNS(...)` sees the shape it expects;
- each column the query reads, as stored or masked as the decision says, inside a `CASE` on the row filters that [a later section](#gate-columns-on-row-filters) explains;
- each column it doesn't read, as a typed `NULL`, since the policy wasn't asked about it;
- a `WHERE` clause with every row filter in the decision.

Everything the user's query does, from its `WHERE` to its struct reads, then happens on that subquery. It's the model Trino's OPA plugin follows, where a row filter "essentially behaves like an additional `WHERE` clause", as the [section on engines](#engine-enforcement) shows.

For the platform, take `instructor-17`, who reads the low ratings among their courses' reviews. The policy filters reviews to the instructor's own courses and shows `body` with contact details removed:

[Example: table-rewrite](../examples/table-rewrite.py)

The instructor's query stays on the outside, qualified but otherwise unchanged. Inside, `course_id` and `rating` come through as stored and `body` as the `redact_contacts` mask, each in a `CASE` on the row filter, a literal list of the instructor's courses, and the columns the query doesn't read are typed `NULL`s. The query returns {{out:table-rewrite:reviews returned}} reviews, and {{out:table-rewrite:of them holding an email}} of them hold an email.

The five reads that defeated the column rewrite, run by `analyst-1` through the table rewrite, and through `lib.enforce.answer`, the function a service calls at its boundary:

[Example: table-rewrite-holds](../examples/table-rewrite-holds.py)

The `WHERE` finds no one, `{{out:table-rewrite-holds:a WHERE on the raw column}}`, because inside the subquery `email` is already the pseudonym. The struct, `COLUMNS(...)` and `to_json` reads return `NULL`, not the pseudonym, and that is the typed-`NULL` rule working: those queries name no column of `crm.learners` but `learner_id`, so the policy was asked about no other column, and the subquery gives the rest no value. A read the rewrite can't see fails closed. DuckDB's cast error now holds a pseudonym instead of an email, and the analyst doesn't see even that: `answer` fetches the rows inside a `try`, turns any engine error into `QueryFailed`, and writes DuckDB's message to the platform team's log, the example's last line. The analyst gets `{{out:table-rewrite-holds:a cast error}}`. And `answer` returns rows because a DuckDB relation runs any SQL on its connection: the analyst's relation, asked for an email, returns `{{out:table-rewrite-holds:the relation, running SQL of its own}}`.

Generating the subquery per query, rather than writing a view per role, is what keeps it in step with the policy. The platform's decisions depend on region, courses, consent and purpose, and a hand-made view for every combination is the "role explosion" NIST describes, where conditions become "numerous roles that are ad hoc and limited in membership" ([NIST SP 800-162](https://doi.org/10.6028/NIST.SP.800-162)). The views would also drift from the policy the first time someone edited one and not the other.

**Working rule:** Replace every protected table with a subquery generated from the decision, under the query's own alias, and leave the user's query otherwise untouched. Give columns the query doesn't read a typed `NULL`. If your users need whole-row reads to show values, ask the policy about every column of each table instead: the masks then cover all of them, at the cost of requests, and audit records, that name columns the query never used.

## Write each mask as SQL in one place {#write-masks-as-sql}

The policy names a mask; the enforcement layer decides what SQL it becomes. Keeping that mapping in one place, the `MASKS` table in `lib.enforce` and the typed `NULL` beside it, means a fix to a mask reaches every table that uses it, and the policy never carries SQL, which would be code arriving as data. The [table rewrite](#replace-each-table) calls `lib.enforce.masked` for every masked column:

[Example: masks-as-sql](../examples/masks-as-sql.py)

Each mask does one job:

- **`redact`** is a `NULL` cast to the column's type, so the column keeps its type for anything the query does with it, such as a union or a date function.
- **`last4`** keeps the last four digits of a card number, after removing anything that isn't a digit.
- **`pseudonymize`** is a keyed hash, SHA-256 over the key followed by the value, because DuckDB {{version:duckdb}} has no HMAC function: `duckdb_functions()` lists {{out:masks-as-sql:DuckDB functions named like HMAC}} named like it. Where your engine has one, prefer HMAC ([RFC 2104](https://www.rfc-editor.org/rfc/rfc2104)).
- **`redact_contacts`** replaces emails and phone numbers in free text with `[email]` and `[phone]`.

The key is what makes a pseudonym a pseudonym. The next example plays someone who holds a list of the platform's emails and hashes each one to look for it among the pseudonyms that `analyst-1`, the platform's data scientist, sees in `crm.learners`:

[Example: pseudonym-key](../examples/pseudonym-key.py)

Without the key, the hashes match {{out:pseudonym-key:pseudonyms matched from a list of emails, without the key}}. With the key, they match {{out:pseudonym-key:pseudonyms matched from a list of emails, with the key}}, so whoever holds the key and a list of emails can name every pseudonym on it. That's why the key belongs to the enforcement layer alone. It isn't in the policy, which is code read in pull requests, or in the query, where a user who chose the key could compute pseudonyms of guessed emails. The layer passes it to DuckDB as a query parameter, so it never appears in the query's text, which DuckDB's query log records, as [chapter 8](#keep-values-out-of-the-record) shows. That's also why the layer denies any user query that carries a parameter: one named `$pseudonym_key` would receive the key. In production, keep the key in a secret manager that only the enforcement layer can read. Rotate it when it may have leaked, and plan for what rotation does: {{out:pseudonym-key:pseudonyms that change when the key is rotated}} pseudonyms change, so data pseudonymized under the old key no longer joins with data under the new one.

`redact_contacts` is two regular expressions, and it's worth measuring what they remove. The platform's tickets and reviews come with every piece of personal data the generator placed in them, labelled by construction, which [chapter 3](#finding-personal-data) uses as its ground truth:

[Example: redact-contacts-recall](../examples/redact-contacts-recall.py)

In the reviews, which instructors and the assistant read through this mask, no email is left, {{out:redact-contacts-recall:reviews, EMAIL_ADDRESS, left in the text}}, and every name is, {{out:redact-contacts-recall:reviews, PERSON, left in the text}}. In the tickets, no phone number is left, {{out:redact-contacts-recall:tickets, PHONE_NUMBER, left in the text}}, but the phone pattern made {{out:redact-contacts-recall:[phone] replacements in tickets}} replacements, because card numbers, IBANs and dates are digits too: {{out:redact-contacts-recall:tickets, CREDIT_CARD, left in the text}} card numbers are left, {{out:redact-contacts-recall:tickets, IBAN_CODE, left in the text}} IBANs and {{out:redact-contacts-recall:tickets, DATE_TIME, left in the text}} dates. Names and street addresses stay: the mask removes contact details, and it doesn't make text anonymous.

**Working rule:** Keep every mask's SQL in one table in the enforcement layer, and let the policy name masks, never write them. Key every pseudonym with a secret only the layer holds, passed as a parameter. Measure each text mask against labelled data before a policy relies on it, and name masks for what they remove, so nobody reads `redact_contacts` as "anonymized".

## Build row filters from the decision's data {#build-row-filters-from-data}

Masks change how a value is shown; row filters decide which rows a user gets. They arrive in a decision as data, in three shapes: a column equal to a value, a column in a list of values, and a consent check on the learner a row belongs to. `lib.enforce.row_filter` turns each into a SQL condition, with the values written as SQL literals by sqlglot, never pasted into SQL text. It denies the query for a shape it doesn't know or a column the table doesn't have, and the layer checks a consent filter's column against `crm.learners` before it builds the check. [Chapter 5](#return-the-decision-as-one-document) made the policy return filters as data for exactly this reason: nothing a request carries can reach a query as code.

The consent shape is the one that needs care. The course assistant, `assistant-1`, may read reviews only by learners who agreed to their text training the platform's AI, and that consent lives in `crm.learners`, which the assistant may not read. The layer turns it into a semi-join, a test that the review's `learner_id` is among the learners who consented, and runs it with the layer's own access to `crm.learners`:

[Example: row-filters-from-data](../examples/row-filters-from-data.py)

The agent's region and the instructor's courses become an equality and an `IN` list. A region value written to look like SQL stays a string, its quotes doubled. The assistant reads {{out:row-filters-from-data:reviews the assistant reads}} reviews. The consent filter tests `IS TRUE`, so the authors of {{out:row-filters-from-data:reviews whose author's AI-training consent is never asked}} reviews, who signed up before the platform asked the question and have no answer recorded, count as not consenting, along with those who said no. And the assistant asking for `crm.learners` itself is denied, with the policy's reason: the semi-join's read of that table belongs to the enforcement layer, not to the user.

**Working rule:** Build every row filter from the decision's values with a SQL builder, and deny any filter shape or column the layer doesn't recognize. Treat an unknown consent as no consent. When a filter needs a table the user may not read, read it with the layer's own access inside the subquery, and never by widening the user's access.

## Gate every column on the row filters {#gate-columns-on-row-filters}

[The row filters](#build-row-filters-from-data) sit in the subquery's `WHERE`, and the user's query wraps the subquery, so it's tempting to assume DuckDB applies the filters first. SQL promises no order, and an optimizer moves conditions wherever they're cheapest. A user's condition that can fail, such as a cast, can then run on rows the filters drop, and a generic error doesn't help: that the query fails says something about a row the user may not see. PostgreSQL's documentation names the leak: a function "that might throw an error depending on the values received as arguments ... could provide significant information about the unseen rows if applied before the security view's row filters". In PostgreSQL, a view marked `security_barrier` keeps "maliciously-chosen functions and operators from being passed values from rows until after the view has done its work", and only functions marked `LEAKPROOF` may still run first ([PostgreSQL 18, Rules and Privileges](https://www.postgresql.org/docs/18/rules-privileges.html)). DuckDB has neither.

So the enforcement layer makes the order irrelevant. Every column the query reads leaves the subquery as `CASE WHEN <row filters> THEN <value> END`, as in [the instructor's rewrite](#replace-each-table). The user's conditions then depend on the filters' result: whatever DuckDB evaluates first, on a row the filters drop they see `NULL` in every column.

The book's test, `lib.enforce.order_cases`, checks that for each filter shape the layer writes: the support agent's equality, the instructor's `IN` list and the course assistant's consent semi-join. For each, it copies the filtered table with a `body` that holds digits in the rows the filter keeps and text in the rows it drops. A query that casts `body` to a number then fails only if the cast sees a dropped row's value:

[Example: row-filter-order](../examples/row-filter-order.py)

With the filter in the `WHERE` alone, the equality and the `IN` list were safe, and the consent semi-join wasn't: DuckDB ran the cast on reviews whose authors didn't consent. With every column gated, all three are safe, {{out:row-filter-order:filter shapes the layer's subquery keeps safe}}. The first two were safe by the optimizer's choice at {{version:duckdb}}, not by any promise, so the test runs on every upgrade. The gate costs evaluating the filters again in each column the query reads.

**Working rule:** Never rely on the engine's evaluation order to keep a user's conditions off rows a filter drops. Make every value a user's query can read depend on the row filters, and test each filter shape on every engine upgrade. Where an engine has a barrier of its own, such as PostgreSQL's `security_barrier` views, use it as well.

## Test who sees what by running queries {#test-who-sees-what}

[Chapter 5](#check-who-sees-what) checks the policy with a who-sees-what table, `lib.policy.WHO_SEES_WHAT`: a list of requests, each with the decision a reviewer expects for it. That tests the decisions, not what users get, which also depends on the layer's rewrite, its masks' SQL and the data. `lib.enforce.observe` runs each case's columns through the layer as the case's user, and compares what comes back with what's stored: how many rows, and which columns differ. A case agrees with its decision when the user is denied exactly when the case says so, gets fewer rows than stored exactly when the case expects a row filter, and gets changed values in exactly the columns the case expects masked:

[Example: who-sees-what-in-queries](../examples/who-sees-what-in-queries.py)

What users get agrees with the decision in {{out:who-sees-what-in-queries:cases where what the user gets agrees with the decision}} cases. In the output, the EMEA support agent gets their region's learners as stored, marketing the consenting, active learners with `phone` changed, and the analyst every learner with `email` and `notes` changed. The last two lines cover `marketing.audience`, which a pipeline writes and nobody has classified yet. Marketing may read it, and gets {{out:who-sees-what-in-queries:marketing-1, marketing.audience, which has no metadata, rows}} rows with {{out:who-sees-what-in-queries:marketing-1, marketing.audience, which has no metadata, columns with a value}} columns holding a value, because the policy treats an unclassified column as sensitive.

The test earns its place on a change. Suppose a pull request adds `analyst-1` to the support group for EMEA, so they can help with tickets. It's a two-line change to `governance/users.toml`, a group and the region support's row filter needs, and the example below makes it in memory, then runs the same cases:

[Example: group-change-caught](../examples/group-change-caught.py)

The change fails {{out:group-change-caught:cases that fail}} cases, and none of them is about tickets. On `crm.learners`, the analyst now gets {{out:group-change-caught:fails, analyst-1, crm.learners, rows}} rows with no column changed: support's region filter applies to them, and the policy's personal-column masks apply only to users outside support, so every email and note comes through as stored. Review text, which a data scientist otherwise can't read at all, or reads with contacts removed when the purpose is AI training, comes through whole. [Chapter 5's decision-level table](#check-who-sees-what) fails on the same cases, {{out:group-change-caught:cases that fail as decisions}}; this one shows what the analyst would actually get.

**Working rule:** Run the who-sees-what cases as queries through the enforcement layer on every change to the policies, the users file, the metadata or the layer itself, and fail the build when any case disagrees. Fix a failing case by changing the policy or the users file, and change a case only when the people who own the data agree that what the user should get has changed.

## Hold back small groups in reports {#hold-back-small-groups}

Row filters and masks decide which rows and values a user sees. Aggregates are a second channel: a count or an average over a few people describes those people. The usual defence is a **minimum group size**, below which a reported group is held back. The number is a rule of thumb, not a measurement. One public data-use agreement from the US Centers for Medicare & Medicaid Services sets 11, and adds that "no use of percentages or other mathematical formulas may be used if they result in the display of a cell less than 11" ([CMS-R-0235L](https://www.cms.gov/medicare/cms-forms/cms-forms/downloads/cms-r-0235l.pdf)). The platform uses the same minimum, {{out:small-groups-difference:minimum group size}}, and keeps it in `governance/enforcement.toml`, where a change is reviewed like a policy change, and `lib.enforce.minimum_group_size` reads it.

The platform sends each instructor a daily course report: learners and average progress per course and learner country. The report job reads learners' countries, which instructors may not read, and instructors may read their own courses' enrollments with each one's progress. Put those together:

[Example: small-groups-difference](../examples/small-groups-difference.py)

Of the {{out:small-groups-difference:cells in the report}} cells of `instructor-17`'s report, {{out:small-groups-difference:cells under the minimum}} are under the minimum, and {{out:small-groups-difference:cells of one learner}} hold one learner each. A cell of one names its learner's country to anyone who can find that learner, and the instructor can: matching the cell's average progress against their own enrollments names {{out:small-groups-difference:learners a cell of one names}} learners exactly. With the minimum, {{out:small-groups-difference:cells shown with the minimum}} cells are shown and none of those lookups works.

The minimum doesn't stop differencing. An enrollment that arrives alone in its course on its day, into a cell that's already shown, raises that cell by one in the next day's report, and the instructor sees which enrollment arrived. That reveals the country of {{out:small-groups-difference:learners whose country the daily report reveals anyway}} learners. This is the attack Denning, Denning and Schwartz called a tracker: they showed that "the compromise of small query sets can in fact almost always be accomplished with the help of characteristic formulas called trackers" ([Denning et al., 1979](https://doi.org/10.1145/320064.320069)). Snowflake, which enforces a minimum group size in its engine, says its policies "do not guarantee a malicious actor could not use deliberate queries to obtain potentially sensitive data" ([Snowflake aggregation policies](https://docs.snowflake.com/en/user-guide/aggregation-policies)). Against differencing, we recommend noise, as in differential privacy ([Dwork and Roth, 2014](https://doi.org/10.1561/0400000042)), rather than higher thresholds, and *Privacy Engineering for Data Teams* covers it.

**Working rule:** Hold back every reported group below the minimum in `governance/enforcement.toml`, and treat the minimum as protection against looking one person up, never against differencing. Don't break a report down by an attribute its reader may not read, sent to a reader who can see the rows behind it: the instructor's report by country is a lookup table for countries. Drop the breakdown, or report it only after it has been made noisy.

## Hand the same policies to an engine that enforces them {#engine-enforcement}

In production, enforcement belongs in the engine every tool queries through, because Saltzer and Schroeder's principle of complete mediation holds that "every access to every object must be checked for authority" ([Saltzer and Schroeder, 1975](https://web.mit.edu/Saltzer/www/publications/protection/Basic.html)), and a policy enforced in one tool and not another fails it. Trino, an open-source distributed SQL engine, can take its access control from Open Policy Agent (OPA) through a plugin, and [chapter 5](#shape-the-request-like-trinos) shaped the platform's requests like the plugin's so the same policy could serve both. Trino 484 offers file-based, OPA and Ranger access control, and none for Cedar ([Trino 484, Security](https://trino.io/docs/484/security.html)).

The plugin asks OPA separate questions at separate URIs ([Trino 484, OPA access control](https://trino.io/docs/484/security/opa-access-control.html)):

- **Whether to allow** an operation, such as `SelectFromColumns`, answered with a boolean `allow`.
- **Row filters**, `GetRowFilters`, for a table. OPA answers with a list of `{"expression": ...}`, and "each expression essentially behaves like an additional WHERE clause". The URI is optional: "if not set no row filtering is applied".
- **Column masks**, `GetColumnMask`, for one column, or a batch of columns at `opa.policy.batch-column-masking-uri`. "Only a single column mask may be returned for a given column."

The operation names and where each request puts its table or column are in the plugin's source at tag 484 ([`OpaAccessControl.java`](https://github.com/trinodb/trino/blob/484/plugin/trino-opa/src/main/java/io/trino/plugin/opa/OpaAccessControl.java), [`OpaHighLevelClient.java`](https://github.com/trinodb/trino/blob/484/plugin/trino-opa/src/main/java/io/trino/plugin/opa/OpaHighLevelClient.java)). The documentation's batch-mask policy reads `filterResources[i].catalogName` where its own request example nests those fields under `.column`, so as printed it wouldn't match. Build the platform's requests in those shapes and give them to its policy:

[Example: trino-requests](../examples/trino-requests.py)

The policy's `allow` admits only `SelectFromColumns`, so pointed at Trino unchanged it would deny the checks Trino makes first: `allow` is `{{out:trino-requests:allow, ExecuteQuery, agent-a05}}` for `ExecuteQuery`, asked before any query runs, and for `AccessCatalog`. The `row_filters` rule doesn't test the operation, so it answers `GetRowFilters`, but on its own it skips `allow` and [chapter 5](#deny-by-default-then-allow-by-role)'s missing-attribute guard: for `agent-new`, a support agent with no region yet, it returns `{{out:trino-requests:GetRowFilters, agent-new, support.tickets, the rule alone}}`, which Trino reads as no filtering. So the answer has to come from the decision for the same read, asked as `SelectFromColumns`: its filters, written in Trino's SQL by the same [`row_filter`](#build-row-filters-from-data) the rewrite uses, or, when it denies the read, `{{out:trino-requests:GetRowFilters, agent-new, support.tickets, answered}}`, which keeps no rows.

`GetColumnMask` names the column under `action.resource.column`, while the policy reads a `SelectFromColumns` request's columns, so as sent it masks nothing and the policy rejects the operation; translated, it gets `pseudonymize`. In production, the translations, the check of `allow` and the Trino SQL belong in a Rego module beside the policy, so OPA can answer Trino itself; the book's examples run no OPA server, so the example shows what that module must return. One more piece maps onto Trino directly: the consent filter reads `crm.learners`, which the assistant may not, and Trino lets a filter carry an `identity` to run as, "such that a filter can target a column the requesting user cannot see".

Since 1.9.0, OPA's Compile API can also write SQL `WHERE` clauses from a policy by partial evaluation ([OPA v1.9.0](https://github.com/open-policy-agent/opa/releases/tag/v1.9.0), [Data filtering with OPA](https://www.openpolicyagent.org/docs/filtering)), but it needs an OPA server, which the examples don't run.

**Working rule:** Put enforcement in the engine every tool queries through, and keep one policy for it and for any rewrite like this chapter's. With Trino, answer every operation the plugin asks about, set both the row-filter and the column-mask URIs, since without them it applies no filters or masks, and return row filters only from a decision that allows the read. Build your tests from the request shapes in the plugin's source rather than from its documentation's example policies.

## Query on someone's behalf {#query-on-someones-behalf}

An assistant that answers questions from the warehouse is one more user of the enforcement layer, with a choice to make: whose identity does its query carry? The platform's course assistant, `assistant-1`, has its own access, for reviews by consenting learners. When it answers a person, it must query as that person, so the answer contains exactly what they could have read themselves. OWASP's guidance for language-model applications says the same: "Implement authorization in downstream systems rather than relying on an LLM to decide if an action is allowed or not" ([OWASP LLM06:2025](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)). The assistant itself belongs to *Data Assistants in Practice*; this section is about its queries.

`instructor-17` asks the assistant how their courses' reviews look, and the assistant writes a query. The tool it calls takes the user from the signed-in session, never from the model or the conversation, so no prompt can change who the query runs as:

[Example: assistant-on-behalf](../examples/assistant-on-behalf.py)

Run as `instructor-17`, the answer covers their {{out:assistant-on-behalf:run as instructor-17, courses in the answer}} courses and {{out:assistant-on-behalf:run as instructor-17, reviews behind it}} reviews. Run as the assistant, it covers {{out:assistant-on-behalf:run as assistant-1, courses in the answer}} courses, most of them other instructors', and only {{out:assistant-on-behalf:run as assistant-1, instructor-17's reviews behind it}} of the instructor's own reviews, the ones whose authors consented to AI training. The assistant's identity is wrong both ways: it shows the instructor what they may not see, and hides what they may. The same holds for retrieval: an assistant grounded on governed text needs "permission-aware vector and embedding stores" ([OWASP LLM08:2025](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/)).

**Working rule:** Run an assistant's queries with the identity of the person it answers, taken from their authenticated session, and let the enforcement layer apply that person's decision. Use the assistant's own identity only for work it does for no one in particular, such as a nightly summary of consented reviews for the course pages, and record both identities in the audit log. Whoever builds the assistant's training set queries as themselves, with the purpose `ai-training`, as [chapter 9](#admit-consented-reviews) does.

## What goes wrong in production {#enforcement-in-production}

An enforcement layer fails by letting a read through that nobody tested, and most of these failures look like correct answers:

- **`SELECT *` slips past on a table missing from the schema.** `qualify` leaves the star unexpanded and raises nothing. Deny every table the catalog doesn't hold, and rebuild the schema whenever the catalog changes.
- **Aggregates leak small groups.** A cell of one names its member, and differences between reports name members of cells that pass any minimum, as [the report example](#hold-back-small-groups) showed. Set the minimum, and remove breakdowns by attributes the reader may not read.
- **Joins and copies bring masked columns back.** A pipeline that runs with full access, such as the one that writes `marketing.audience`, copies `email` into a table of its own, protected only by its own classification: unclassified, it's hidden from everyone, as [the who-sees-what test](#test-who-sees-what) showed, and classified `internal` by mistake, it's shown to anyone who may read the table. [Chapter 4](#classifications-from-lineage) propagates classifications along lineage to catch that. Unmasked columns re-identify through joins too: Sweeney found 87% of the US population likely unique on ZIP code, gender and date of birth in 1990 census data ([Sweeney, 2002](https://dataprivacylab.org/dataprivacy/projects/kanonymity/kanonymity.pdf)), and Golle, counting again, found 61% on the same 1990 data and 63% on 2000's ([Golle, 2006](https://doi.org/10.1145/1179601.1179615)). Rocher and colleagues estimate, from a model fitted to census data, that 15 demographic attributes would make 99.98% of people in Massachusetts unique ([Rocher et al., 2019](https://doi.org/10.1038/s41467-019-10933-3)).
- **Direct file reads bypass the rewrite.** `read_parquet`, a path in `FROM` and table functions read data without naming a table. Deny them in the rewrite, and turn off `enable_external_access`, which DuckDB then won't turn back on: "{{out:unknown-tables-denied:turning external access back on}}". `lock_configuration` keeps every other setting as the layer left it; set it once the layer's connection is fully configured ([Securing DuckDB](https://duckdb.org/docs/2.0/operations_manual/securing_duckdb/overview)), not when it's created, since [chapter 8](#cross-check-the-query-log) starts DuckDB's query log after that. Anyone who can read the warehouse's files in storage needs no SQL at all, so storage permissions are part of enforcement.
- **Identifier case decides what a name matches.** A layer that matched names without normalizing them would let `"LEARNERS"`, which DuckDB resolves to `learners`, reach the raw table, as [the first section](#find-what-a-query-reads) showed. Normalize with the dialect of the engine that runs the query before matching any name.
- **Error messages print values.** DuckDB's cast errors include the value. The table rewrite makes it a masked one, and [the gate on each column](#gate-columns-on-row-filters) keeps dropped rows out of reach, but the message still reaches the user unless something stops it. Return a generic error at the service boundary, as `lib.enforce.answer` does, and log the engine's message where only the platform team reads it, apart from the audit log, which [chapter 8](#keep-values-out-of-the-record) keeps free of values. PostgreSQL warns that plans and timings remain covert channels even behind a security barrier ([PostgreSQL 18, Rules and Privileges](https://www.postgresql.org/docs/18/rules-privileges.html)).
- **DuckDB is not a security boundary.** "DuckDB executes SQL with the full privileges of the user running it, much like a shell or scripting interpreter" ([Securing DuckDB](https://duckdb.org/docs/2.0/operations_manual/securing_duckdb/overview)). A rewrite inside the same process protects data only while users can't reach DuckDB by any other route. Treat this chapter's layer as a tested model of what an engine such as Trino enforces, and enforce there in production.

## What this chapter decided {#enforcement-decisions}

- **Enforcement replaces tables, not columns.** Each catalog table in a query becomes a subquery generated from the user's decision. Rewriting column references is kept only to show that it leaks.
- **The layer fails closed.** Its sources are an allowlist of catalog tables, CTEs in scope and subqueries. It denies functions with side effects, parameters and anything but a single query. Columns a query doesn't read are typed `NULL`s, and the ones it reads are gated on the row filters.
- **Users get one generic error** from `answer`, and DuckDB's message goes to the platform team.
- **Masks are SQL in one place.** The policy names masks; `lib.enforce` writes their SQL. Pseudonyms are keyed with a secret only the layer holds, passed as a parameter.
- **Row filters are built from data,** with values as literals. Consent is a semi-join the layer runs with its own access, and an unknown consent counts as no.
- **Who sees what is tested as queries:** chapter 5's cases run through the layer on every change.
- **Reports hold back groups under the minimum group size,** {{out:small-groups-difference:minimum group size}} in `governance/enforcement.toml`, and don't break down by attributes their readers may not read.
- **Assistants query as the person they answer,** with the identity taken from the session.
- **In production, the engine enforces:** Trino with OPA, from the same policy, with both URIs set and row filters answered only from an allowed decision.
