---
chapter_id: lineage-from-pipelines
---

# Lineage from pipelines

By the end of this chapter you can capture lineage from your pipelines as OpenLineage run events, with column lineage parsed from their SQL, including the columns that only join, filter and group. You'll use the graph those events describe to say what a column change breaks downstream, and to propose classifications for tables nobody has classified. You'll also have checks for the three ways lineage fails in production: events that never arrive, a job that sends none, and a copy of personal data classified as less than personal.

Four SQL pipelines build reporting tables from the eight source tables of the platform that [chapter 1](#governance-end-to-end) introduces, and nobody has recorded which source columns each one reads and writes. Its customers are adult learners buying professional courses. Each pipeline is one `create table ... as select ...` in `examples/pipelines/`, and the book's shared module `lib.pipelines` runs it on DuckDB and writes the result back to the catalog:

- `course_performance` writes `analytics.course_performance`: enrollments, completions, ratings and revenue per course.
- `learner_activity` writes `analytics.learner_activity`: each learner's events and last activity.
- `marketing_audience` writes `marketing.audience`: the learners marketing may email, with their name and email.
- `review_corpus` writes `ai.review_corpus`: the review text the platform's course assistant is grounded on.

[Chapter 2](#a-catalog-you-can-test)'s metadata check found that two of those outputs, `marketing.audience` and `ai.review_corpus`, have no metadata at all: no owner, and no classification for any column. Yet one holds learners' names and emails, and the other holds the free text of reviews, where [chapter 3](#classify-columns-from-their-values) found names and emails too. Two questions follow, and the catalog can't answer either. Which columns of those tables are personal data? And if the CRM team renames or reformats `crm.learners.email`, what breaks? Both are lineage questions: where each column's data came from, and where it goes.

This chapter adds the book's shared module for lineage, `lib.lineage`. It runs each pipeline, tells OpenLineage what the run read and wrote, column by column, and reads the result back as a graph that answers both questions.

## Send a run event from every pipeline {#send-run-events}

OpenLineage, an open specification for lineage, describes it with three objects ([object model](https://openlineage.io/docs/spec/object-model)). A **job** is a process that consumes or produces datasets, such as a pipeline. A **run** is one occurrence of a job, identified by a UUID the client generates, and the spec recommends UUIDv7. A **dataset** is a table or file, named by a namespace and a name that together identify it. A run reports itself through **run events**: a START when it begins, then one terminal event, COMPLETE, FAIL or ABORT, and a server treats the events for one run as accumulating ([run cycle](https://openlineage.io/docs/spec/run-cycle)). Lineage comes mostly from the work itself: a backend learns about datasets mainly from information about jobs.

The Python client, openlineage-python {{version:openlineage-python}}, builds events from `RunEvent`, `Run`, `Job`, `InputDataset` and `OutputDataset` in `openlineage.client.event_v2`, and sends them through a transport ([client docs](https://openlineage.io/docs/client/python)). It ships HTTP and Kafka transports for servers, and a file transport "designed mainly for integration testing", which with `append` set writes one JSON event per line to a single file ([configuration](https://openlineage.io/docs/client/python/configuration)). The client computes no lineage; it serializes what the caller passes in, plus its own metadata: a run facet with its version, the job's source location when it runs in a Git checkout, and any environment variables you configure it to collect (`emit` in `client/client.py`, [openlineage-python](https://pypi.org/project/openlineage-python/1.54.0/)). So the lineage that reaches a server is the lineage your code put in the event.

`lib.lineage.run` wraps `lib.pipelines.run`, which runs one of the platform's four SQL pipelines and writes its table to the catalog. It sends START with the tables the SQL reads as inputs, runs the pipeline, and then sends COMPLETE with the output and its two facets. Two **facets**, blocks of metadata attached to a run, a job or a dataset, hold the output's schema and its column lineage, which the [next section](#trace-output-columns) builds. If the pipeline raises, it sends FAIL instead; `lib.lineage.run_all` does this for all four into one events file. The datasets need a namespace, and OpenLineage's [naming conventions](https://openlineage.io/docs/spec/naming) have no entry for an Iceberg table in a local SQL catalog. The nearest, a file path, would put a folder on your machine into every event. So `lib.lineage` uses the placeholder `iceberg://platform`, with the catalog's identifiers, such as `crm.learners`, as names.

The examples need the same bytes every time, yet an event's time and random run ID change by default. So `lib.lineage` stamps every event with the nominal start of the nightly run on the book's "today", and derives the run ID with the client's `generate_static_uuid`, which, its docstring says, "always produces the same result" for the same time and data: here, the job's name. Run the four pipelines with lineage twice, into two files, and print what the first file's events say, not the raw JSON:

[Example: pipeline-run-events](../examples/pipeline-run-events.py)

Sending the same nominal run again wrote the same bytes, as random run IDs never would, and the example asserts it. The first file holds {{out:pipeline-run-events:events}} events, a START and a COMPLETE for each of {{out:pipeline-run-events:distinct run IDs}} runs, all stamped `{{out:pipeline-run-events:event times}}`. The `marketing_audience` job's START names the tables its SQL reads, `crm.learners`, `learning.courses` and `learning.enrollments`, and its COMPLETE names `marketing.audience`, with lineage for each column. Yet the events hold something you didn't put there: a run facet named `{{out:pipeline-run-events:run facets the client added}}`, carrying the client's own version ([changelog, 1.38.0](https://github.com/OpenLineage/OpenLineage/blob/main/CHANGELOG.md)), and every facet's `_producer` URL names that version too (`DEFAULT_PRODUCER` in `client/constants.py`). So the bytes change with each client release, though the lineage doesn't, and a test should compare what events say, as this example's summary does.

A production emitter stamps COMPLETE with the time the run finished. A derived run ID is still worth keeping there, made from the scheduled time, the job's name and the attempt number: an event sent twice then lands in the same run instead of creating a second one, and a retried job gets a run of its own. Production events should also carry the output's Iceberg snapshot ID, in the `version` dataset facet the [object model](https://openlineage.io/docs/spec/object-model) names for it. The code here leaves it out, because PyIceberg {{version:pyiceberg}} draws each snapshot ID from a random UUID (`_generate_snapshot_id` in its `pyiceberg/table/metadata.py`).

**Working rule:** Send a START and then a COMPLETE or FAIL event from the code that runs each job, so no run can happen without them, and name datasets by catalog identifiers under a namespace you choose, never a path. In tests, use the file transport with a fixed event time and static run IDs, and compare what events say, never their bytes. In production, send through a transport to your lineage server, and derive each run ID from the scheduled time, the job and the attempt, so a resent event never creates a duplicate run.

## Trace each output column to its sources {#trace-output-columns}

A [run event](#send-run-events) says which tables a job read and wrote. Column lineage says which input columns each output column came from, and that is what governance needs: a table that reads `crm.learners` may or may not copy its emails. OpenLineage puts column lineage in a dataset facet, `columnLineage`, on the output: `fields` maps each output column to its `inputFields`, and an optional `dataset` list holds inputs that affect the whole dataset ([facet schema](https://openlineage.io/spec/facets/1-2-0/ColumnLineageDatasetFacet.json)). Since the client computes none of it, something has to parse each job's SQL.

sqlglot, a SQL parser, does it with `sqlglot.lineage.lineage`. Given a statement and the schema of the tables it reads, it returns, for each output column, a tree whose leaves are source columns, and with `column=None` it returns every output column at once ([sqlglot lineage](https://sqlglot.com/sqlglot/lineage.html)). It needs the schema. Without one, `SELECT *` comes back as a single output named `*`, and an unqualified column in a join as a placeholder with no table. `lib.lineage.schema` builds the schema from the catalog's Iceberg metadata, so the parser sees the columns the warehouse has, at sqlglot {{version:sqlglot}}.

The first case is `marketing_audience`, which feeds the list marketing may email. In words, it picks learners who agreed to marketing email and whose account is active, and adds each one's favourite course category, which a CTE computes by ranking categories by the learner's enrollments. Ask sqlglot for the lineage of the pipeline's outputs and of two `course_performance` columns, to see what it leaves out:

[Example: sqlglot-misses-row-inputs](../examples/sqlglot-misses-row-inputs.py)

The values are right, but what decides which learners are in the table is missing. `email` comes from `crm.learners.email`, and `favourite_category` is traced through the CTE to `{{out:sqlglot-misses-row-inputs:marketing.audience.favourite_category comes from}}`. The pipeline's `WHERE` is `{{out:sqlglot-misses-row-inputs:the pipeline's WHERE}}`, and neither of its columns, `marketing_consent` or `status`, is in any output's lineage. The example asserts both. The consent filter is the reason marketing may hold this table at all, and the lineage doesn't mention it. Nor does it mention the join keys from enrollments and courses.

The second problem is the reverse: a filter-only input is listed as if it were a value. `course_performance` sums only succeeded payments, with `filter (where p.status = 'succeeded')`, and sqlglot says `revenue_usd` comes from `{{out:sqlglot-misses-row-inputs:analytics.course_performance.revenue_usd comes from}}`: the column summed and the column that only decides which payments count, listed alike. `completions` counts enrollments with `filter (where e.completed_at is not null)`, and comes back the same way, from `{{out:sqlglot-misses-row-inputs:analytics.course_performance.completions comes from}}`. Both follow from how sqlglot builds the tree: it sees a filter inside an expression and nothing outside it, and its nodes carry no type for how an input was used.

**Working rule:** Use sqlglot's `lineage` for the columns each output's value is computed from, and always pass the catalog's schema, generated from the table metadata, never typed. Don't read its answer as everything a column depends on: it misses the columns that join, filter and group, and it doesn't say which of its inputs are values.

## Add the inputs that decide which rows exist {#add-indirect-inputs}

[sqlglot's lineage](#trace-output-columns) lists the columns a value is computed from, and OpenLineage has a place for the rest. Each input field carries a list of transformations, each with a type, a subtype and a `masking` flag ([column lineage facet](https://openlineage.io/docs/spec/facets/dataset-facets/column_lineage_facet)). DIRECT means the output value is derived from the input value, through IDENTITY, TRANSFORMATION or AGGREGATION. INDIRECT means the output value is impacted by an input without being derived from it, through JOIN, GROUP_BY, FILTER, SORT, WINDOW or CONDITIONAL. Inputs that shape every row, such as a `WHERE`, belong in the facet's `dataset` list.

For governance, the INDIRECT side matters, since information flows through conditions as well as values. Denning calls it "implicit flow": a statement that assigns only when a condition holds still tells you the condition's value ([Denning, 1976](https://doi.org/10.1145/360051.360056)). A row in `marketing.audience` tells you that learner consented.

`lib.lineage.column_lineage` adds two things sqlglot's answer lacks: a type for each value input, and the INDIRECT inputs. It types each value input by where the column sits in the output's expression: a bare column is IDENTITY, a column inside an aggregate is AGGREGATION, and anything else is TRANSFORMATION. A column inside an aggregate's `FILTER`, a window's `PARTITION BY` or `ORDER BY`, or a `CASE`'s condition or operand is INDIRECT, and a hash on the way sets `masking`. `COALESCE`'s arguments count as values: OpenLineage lists `COALESCE` under CONDITIONAL, but any of its arguments can become the output's value. Then it walks every `SELECT` in the statement, CTEs included, and collects the columns of its row clauses, following CTE columns back to the tables they come from:

```python
ROW_CLAUSES = {"joins": "JOIN", "where": "FILTER", "group": "GROUP_BY", "having": "FILTER", "qualify": "FILTER",
               "order": "SORT"}
```

Pass the same pipeline and the same two columns to `lib.lineage.column_lineage`, which adds the inputs sqlglot leaves out:

[Example: indirect-column-lineage](../examples/indirect-column-lineage.py)

The values match [sqlglot's](#trace-output-columns), each an IDENTITY copy, and the rows' inputs are the new part. Adding them grows what `marketing.audience` depends on from {{out:indirect-column-lineage:source columns, values only}} source columns to {{out:indirect-column-lineage:source columns, with the rows' inputs}}. Some row inputs are columns no value uses: `marketing_consent` and `status` as FILTER, the two course IDs as JOIN, and the enrollments' `learner_id` as GROUP_BY and JOIN. The learner ID and category, which values copy too, also shape rows: the ID as JOIN, the category as GROUP_BY and JOIN. For `revenue_usd`, the two inputs are now told apart: `amount` is a DIRECT AGGREGATION, and `status` an INDIRECT FILTER. So are the two inputs of `completions`: `enrollment_id` is counted, and `completed_at` only filters.

The per-field `transformationType` and `transformationDescription` are deprecated, replaced by the per-input `transformations` that `lib.lineage` writes ([changelog, 1.17.1](https://github.com/OpenLineage/OpenLineage/blob/main/CHANGELOG.md)). And OpenLineage 1.53.0 added a Lineage dataset facet for declared lineage, such as a view's, sent on a `DatasetEvent`, which consumers should prefer where both describe the same relationship ([lineage facet](https://openlineage.io/docs/spec/facets/dataset-facets/lineage)). Run events from jobs carry `columnLineage`, as the platform's do.

**Working rule:** Record INDIRECT inputs with every job's column lineage: the join keys, filters and grouping columns of every `SELECT`, CTEs included, in the facet's `dataset` list, and type each value input with OpenLineage's subtypes. Expect more edges, many of them join keys. Keep them anyway: a filter on a consent column decides who may be emailed, and it reaches the table only as an INDIRECT input.

## Fail on statements the parser can't read {#fail-on-unreadable-lineage}

The platform's [column lineage](#add-indirect-inputs) is only as good as the parse behind it, and a parse can be wrong without failing. sqlglot's `lineage` accepts more than a `SELECT`: it finds the query inside a `CREATE TABLE ... AS`, an `INSERT ... SELECT` or a `CREATE VIEW`, and it reads a `MERGE` too ([sqlglot](https://pypi.org/project/sqlglot/30.24.0/), `optimizer/scope.py`). Three cases come back looking fine. An `INSERT`'s outputs are named after its `SELECT`, never after the table's columns. A `MERGE` comes back as the last query sqlglot reaches in it, as if that query were what the `MERGE` writes. When `USING` names a table or a CTE and no subquery follows, sqlglot returns no lineage and no error. When `USING` is a subquery, or a subquery sits in a `WHEN` condition or a `SET`, it returns the columns of the last such subquery, whether the `MERGE` writes them or not. And a misspelt column gets through when it's unqualified in a query on one table: sqlglot assigns it to that table whether the table has the column or not.

All four of the platform's pipelines are `CREATE TABLE ... AS` statements. As tables grow, pipelines like these often become incremental: an `INSERT` that appends a day's revenue to `analytics.daily_revenue`, or a `MERGE` that updates `analytics.learner_activity` in place. Both kinds go to sqlglot and to `lib.lineage.column_lineage`, the `MERGE` in two forms, so the answers sit side by side:

[Example: insert-and-merge-lineage](../examples/insert-and-merge-lineage.py)

The table `analytics.daily_revenue` has the columns `{{out:insert-and-merge-lineage:analytics.daily_revenue columns}}`, and sqlglot's answer for the `INSERT` is not one of them. It names the outputs `{{out:insert-and-merge-lineage:sqlglot, INSERT outputs}}`: the first after the column inside the `cast`, the second a generated name for an expression with no alias.

An `INSERT` fills columns by position, so `lib.lineage` matches by position too, taking the names from the catalog: the columns the `INSERT` lists, or else all of the table's. So `day` comes from `{{out:insert-and-merge-lineage:book, INSERT day}}`, and the `INSERT`'s grouping and its filter on `status` shape every row: `{{out:insert-and-merge-lineage:book, INSERT rows shaped by}}`. When the `SELECT` has more or fewer values than the columns written, `lib.lineage` raises: `{{out:insert-and-merge-lineage:book, INSERT with one value too many}}`.

For the `MERGE` whose `USING` names a table, sqlglot returns `{{out:insert-and-merge-lineage:sqlglot, MERGE USING a table, outputs}}`, and no error. For the one whose `USING` is a subquery, it returns `{{out:insert-and-merge-lineage:sqlglot, MERGE USING a subquery, outputs}}`, though that `MERGE` writes only `learner_id` and `country`. A check for empty lineage catches the first and passes the second, so `lib.lineage` doesn't judge what sqlglot returns for a `MERGE`. Before it calls sqlglot, it refuses every statement that isn't a `CREATE ... AS` or an `INSERT ... SELECT`: `{{out:insert-and-merge-lineage:book, MERGE USING a subquery}}`.

A misspelt column, the third case, depends on how the column is written. The platform's pipelines qualify every column with its table's alias, as in `l.email`, and sqlglot checks a qualified column against the schema itself:

[Example: misspelt-column-lineage](../examples/misspelt-column-lineage.py)

A qualified typo stops sqlglot with `{{out:misspelt-column-lineage:sqlglot, qualified l.emial}}`. An unqualified one, in a query on one table, comes back as `{{out:misspelt-column-lineage:sqlglot, unqualified emial comes from}}`: a confident answer about a column that doesn't exist. In a join, a misspelt unqualified column belongs to none of the tables, and sqlglot returns a placeholder with no table ([sqlglot](https://pypi.org/project/sqlglot/30.24.0/), `lineage.py`), which `lib.lineage` rejects as a source it can't place. For the single-table case, `lib.lineage` checks every source against the schema and raises `{{out:misspelt-column-lineage:book, unqualified emial}}`.

`lib.lineage.record`, which `run` uses, reads the lineage before the job runs, so a pipeline whose lineage can't be read fails before it writes anything. That is strict, and deliberate: a job with empty lineage would vanish from every impact report and classification check in this chapter, and one with wrong lineage would mislead them. Run the same check in CI on every pull request that changes a pipeline, so the failure shows up in review rather than at night.

**Working rule:** Treat a statement your parser can't read correctly as an error, never as empty or partial lineage. Refuse every kind of statement it gets wrong, such as `MERGE`, before you ask it, and fail on empty lineage, a source the parser can't place and a column the schema doesn't have. Match an `INSERT`'s columns by position against the table's columns in the catalog, never the `SELECT`'s names, and fail when the counts differ. For a `MERGE`, write a `SELECT` of the columns it writes, and record the job's lineage from that, as an `INSERT ... SELECT` into the `MERGE`'s table, until your parser handles `MERGE` itself.

## Find what a column change breaks {#impact-of-a-column-change}

With [column lineage](#add-indirect-inputs) in every COMPLETE event, the platform has a graph: columns are nodes, and each input field is an edge to the column or table it feeds. **Impact analysis** asks what a change to one column can break. It walks that graph from the column, following every edge out of it and continuing from wherever each lands, at any depth. No special method is needed; it's graph reachability. One rule is specific to lineage. An INDIRECT edge into a table means every row of it can change, so the walk continues from all of that table's columns.

`lib.lineage.graph` builds the graph from the events file alone, as a lineage server would, not from the SQL. So it includes every job that sends events, whatever it's written in, and nothing that doesn't. `lib.lineage.downstream` does the walk.

Take three changes the platform's teams might make. The CRM team lowercases every email, or renames `crm.learners.email`. A new sign-up flow changes what `crm.learners.marketing_consent` means for older accounts. The learning team renames a category in `learning.courses`. Before you run it, predict how many columns the consent change can reach:

[Example: column-impact](../examples/column-impact.py)

Consent is the surprise. A change to it reaches {{out:column-impact:crm.learners.marketing_consent, columns reached through values}} columns through values, yet through its filter it reaches all {{out:column-impact:crm.learners.marketing_consent, columns reached in all}} columns of `marketing.audience`, because it changes who gets emailed. A values-only lineage tool would report a change to consent, the column that decides who may be emailed, as having no impact.

A change to `email` reaches {{out:column-impact:crm.learners.email, columns reached through values}} column, `marketing.audience.email`, so renaming it breaks the marketing pipeline and no other job that sends lineage. A category rename reaches {{out:column-impact:learning.courses.category, columns reached through values}} columns through values, and {{out:column-impact:learning.courses.category, columns reached in all}} in all, because categories also group `analytics.course_performance` and rank each learner's favourite.

The same walk serves two later chapters: [chapter 7](#plan-erasure-from-lineage) follows a learner's columns downstream to find every derived table an erasure must reach, and [chapter 10](#report-what-a-schema-change-reaches) writes an impact report for every pull request that changes a schema.

**Working rule:** Run impact analysis on the graph built from events, INDIRECT edges included, and report two lists: columns whose values can change, and tables whose rows can change. Treat a change to a column used only in filters and joins as a change to every column of the tables it filters, and send the report to the owners of each table it reaches.

## Propose classifications from lineage {#classifications-from-lineage}

A derived column holds data from its inputs, so its classification can be derived from theirs. The classic rule comes from Denning's lattice model of information flow: the class of a value computed from several inputs is the least upper bound, the "join", of their classes ([Denning, 1976](https://doi.org/10.1145/360051.360056)). The platform's classifications form a line, `public`, `internal`, `personal` and `sensitive`, so the join is the stricter of them, which `stricter` in `lib.metadata`, the book's metadata module, returns. Which inputs count is a judgement. We count INDIRECT inputs too, following Denning's implicit flows, because a table filtered on consent reveals each learner's consent. That choice lives in `governance/lineage.toml`, so a team can change it in one reviewed line.

Two more choices follow from the same caution. OpenLineage's `masking` flag says only that a value was obfuscated, such as by a hash ([column lineage facet](https://openlineage.io/docs/spec/facets/dataset-facets/column_lineage_facet)). Whether a hashed identifier is still personal data is a legal question: [chapter 2](#classify-pseudonymous-ids) quotes the GDPR recital on pseudonymized data and leaves the answer to lawyers. So the book's rule never lowers a classification because of masking. Catalog servers leave this to configuration too. Apache Atlas propagates classifications along lineage, and can block that on an edge, for example where a view masks a PII column ([Atlas](https://atlas.apache.org/1.0.0/ClassificationPropagation.html)). DataHub propagates glossary terms along column lineage, a feature its 1.7.0 documentation calls a public beta in DataHub Cloud ([DataHub](https://docs.datahub.com/docs/automations/glossary-term-propagation)). A proposal is a floor, not a verdict: like the drafts a local model writes in [chapter 2](#draft-descriptions-with-a-local-model), it's reviewed before it goes into `governance/metadata.toml`, the platform's file of owners and classifications.

`lib.lineage.propose` proposes a classification for every column a job wrote, and `lib.metadata.problems` lists the tables that need one. Check the rule on the two tables people did classify, `analytics.course_performance` and `analytics.learner_activity`, before you trust it on `marketing.audience` and `ai.review_corpus`, the two with no metadata:

[Example: propagate-classifications](../examples/propagate-classifications.py)

The metadata check from [chapter 2](#test-the-catalog-against-the-metadata) still reports the two tables with no metadata, and lineage proposes a classification for each of their columns. Lineage proposes personal for the columns that copy personal data: {{out:propagate-classifications:personal columns proposed}} of them, `full_name` and `email` in `marketing.audience`, each an IDENTITY copy of a personal column in `crm.learners`, and `body` in `ai.review_corpus`, a copy of the reviews' free text. The rest come out internal, including `favourite_category`, for which the example prints `{{out:propagate-classifications:marketing.audience.favourite_category}}`. Its values are public course-catalog category names, but the rows they sit in are chosen by a join on learner IDs and a consent filter, so those are internal.

Counting INDIRECT inputs reproduces {{out:propagate-classifications:declared classifications reproduced, through DIRECT and INDIRECT}} of the declared classifications on the {{out:propagate-classifications:declared classifications checked}} columns people did classify, and all of them are `{{out:propagate-classifications:classes declared for them}}`.

DIRECT inputs alone reproduce {{out:propagate-classifications:declared classifications reproduced, through DIRECT}}, and miss {{out:propagate-classifications:missed with DIRECT inputs only}}: `course_id`, `title` and `category` of `analytics.course_performance`. A DIRECT-only rule calls them public, as in the course catalog, while the analytics team chose internal. So the rule proposes no less than people chose here, and lineage finds {{out:propagate-classifications:declared less protected than lineage proposes}} declared classifications lower than its proposal. That is weak evidence: a rule that proposed internal for every column would score as well. And it can't show what the rule does with a personal filter, since none of the platform's filters or joins reads a personal column: even `marketing_consent` is classified internal.

So build a table whose filter does. For a birthday offer, marketing wants the learners who consented and were born this month, with their ID and country, and nothing else:

[Example: personal-filter-propagates](../examples/personal-filter-propagates.py)

Neither column copies anything personal, but every row says its learner was born in the offer's month, part of a date of birth. The table holds {{out:personal-filter-propagates:learners in marketing.birthday_offers}} learners, and `governance/metadata.toml` classifies the date of birth `{{out:personal-filter-propagates:crm.learners.date_of_birth declared}}`.

Counting DIRECT inputs alone proposes `{{out:personal-filter-propagates:marketing.birthday_offers.learner_id, through DIRECT}}` for `learner_id` and `{{out:personal-filter-propagates:marketing.birthday_offers.country, through DIRECT}}` for `country`.

Add INDIRECT inputs, and it proposes `{{out:personal-filter-propagates:marketing.birthday_offers.learner_id, through DIRECT and INDIRECT}}` for `learner_id` and `{{out:personal-filter-propagates:marketing.birthday_offers.country, through DIRECT and INDIRECT}}` for `country`, decided by `{{out:personal-filter-propagates:marketing.birthday_offers.learner_id, through DIRECT and INDIRECT, decided by}}` and `{{out:personal-filter-propagates:marketing.birthday_offers.country, through DIRECT and INDIRECT, decided by}}`. That is the case INDIRECT inputs are for, and it shows the cost too: one personal filter can make every column of a table personal.

OpenLineage can carry classifications in the events themselves, with the Tags dataset facet, whose `field` key "identifies the field in a dataset if a tag applies to one" ([tags facet](https://openlineage.io/docs/spec/facets/dataset-facets/tag-facet), [schema](https://openlineage.io/spec/facets/1-0-0/TagsDatasetFacet.json)). We keep them in `governance/metadata.toml` instead, the one source that the platform's access policies also read, through `lib.policy`, the book's policy module ([chapter 5](#generate-the-data-the-policy-reads)).

**Working rule:** Propose each derived column's classification as the stricter of its inputs', counting INDIRECT inputs, and send the proposals to the table's owner for review before they become metadata. Never let lineage lower a classification someone declared, and never treat masking as declassification. Count DIRECT inputs only if your pipelines often join or filter on personal columns, such as matching on email, and every output would otherwise come out personal; then review INDIRECT personal inputs by hand.

## What goes wrong in production {#lineage-in-production}

Lineage fails quietly: a missing edge looks exactly like a column with no dependencies, and every check built on the graph, from [impact analysis](#impact-of-a-column-change) to [proposed classifications](#classifications-from-lineage), then passes. Three failures are common, and each has a check.

### Events that never arrive {#events-that-never-arrive}

The client reads `OPENLINEAGE_DISABLED` from the environment, and when it's `true`, events are dropped ([configuration](https://openlineage.io/docs/client/python/configuration)). In the client's source, that check comes first ([openlineage-python](https://pypi.org/project/openlineage-python/1.54.0/), `client/client.py`): before it looks at the transport you passed explicitly, the client picks a transport that does nothing. Its only notice is an INFO log line: "OpenLineage is disabled. No events will be emitted." Python's root logger is set to `WARNING` by default, so the line never shows ([logging HOWTO](https://docs.python.org/3.12/howto/logging.html)). A variable left in a shared environment file after debugging is enough. Set the variable, run `review_corpus` with an explicit file transport, and check what reaches the file:

[Example: lineage-disabled-silently](../examples/lineage-disabled-silently.py)

With the variable set, the pipeline still runs and writes its table, but nothing reaches the file. The transport the client chose is {{out:lineage-disabled-silently:transport the client chose}}, though the code passed a file transport in. The pipeline wrote {{out:lineage-disabled-silently:rows written}} rows to `{{out:lineage-disabled-silently:pipeline wrote}}`, no events file exists, and nothing raised an error. The check that catches it compares the jobs the scheduler ran with the jobs that sent a COMPLETE event: `lib.lineage.missing_runs` lists `{{out:lineage-disabled-silently:jobs that ran with no COMPLETE event}}` as having run without one.

**Working rule:** After every scheduled run, compare the jobs that ran with the jobs whose COMPLETE events arrived, and alert on any difference. Don't rely on the emitter to report its own silence.

### A job nobody instrumented {#job-nobody-instrumented}

Lineage covers only the jobs that send it. The platform's marketing team runs a weekly Python script that copies `marketing.audience` into `marketing.campaign_list`, a table its email tool reads. Nobody wired the script to OpenLineage:

[Example: uninstrumented-job](../examples/uninstrumented-job.py)

An export that sends no events is invisible to impact analysis. The script copied {{out:uninstrumented-job:rows exported}} learners' names and emails, yet `crm.learners.email` still reaches only `{{out:uninstrumented-job:crm.learners.email reaches}}`, so an erasure guided by this lineage would miss the copy. The catalog knows the table exists, so `lib.lineage.unaccounted` compares the catalog's tables with the lineage: every table must be one of the platform's sources or the output of a job that lineage knows. It finds `{{out:uninstrumented-job:tables no job's lineage accounts for}}`.

**Working rule:** Check the catalog against the lineage on a schedule, and treat a table no job's lineage accounts for as an incident: find the job, and make it send events.

### Personal data in a table classified internal {#personal-data-tagged-internal}

Instrumenting [the marketing team's export](#job-nobody-instrumented) fixes the graph, and exposes a second problem. When the export's owner created `marketing.campaign_list`, they classified every column internal, because only the marketing team reads it. That's a statement about who reads a table, not what it holds. Wrap the script's work in `lib.lineage.record`, with the `SELECT` it runs as its lineage, and compare the declared classifications with lineage's proposal:

[Example: personal-copy-tagged-internal](../examples/personal-copy-tagged-internal.py)

Once the export sends lineage, the walk reaches its copy: `crm.learners.email` reaches `{{out:personal-copy-tagged-internal:crm.learners.email reaches}}`, two hops, the second through the export. And `lib.lineage.understated` reports `full_name` and `email` in `marketing.campaign_list` as declared internal while lineage proposes personal: copies of personal data that any policy reading the declared classification would treat as internal.

**Working rule:** Run `lib.lineage.understated`, or your own check like it, on every lineage update, and fail when a declared classification is lower than lineage proposes. Classify by what a table holds, never by who reads it, and fix such a finding by raising the classification, not by dropping the edge.

## What this chapter decided {#lineage-decisions}

- **Every pipeline run sends OpenLineage run events,** START and then COMPLETE or FAIL, from the code that runs it (`lib.lineage.run`), with the output's schema and column lineage. Datasets are named by catalog identifiers under `iceberg://platform`. Examples fix event times and derive run IDs from the nominal run, and compare what events say, not their bytes.
- **Column lineage is sqlglot's value lineage, typed as OpenLineage subtypes, plus INDIRECT inputs:** the join, filter, grouping and sort columns of every `SELECT`, CTEs included, in the facet's `dataset` list.
- **Lineage that can't be read correctly is an error,** never empty or partial. `lib.lineage` reads only `CREATE ... AS` and `INSERT ... SELECT`, refuses every other statement, `MERGE` included, whatever its `USING` names, and stops the job before it runs on empty lineage, an unplaceable source or an unknown column. An `INSERT`'s columns come from the catalog, matched by position, and a count that differs is an error.
- **Impact analysis walks the graph built from events,** INDIRECT edges included, and reports columns whose values can change and tables whose rows can change.
- **Classifications propagate as the stricter of all inputs',** DIRECT and INDIRECT, as set in `governance/lineage.toml`, so a table chosen by a personal filter is proposed personal. Lineage never lowers a declared classification, and masking never declassifies. The proposals for the two unclassified tables, `full_name`, `email` and `body` personal and every other column internal, go to their owners for review before they enter `governance/metadata.toml`.
- **Three checks guard the lineage itself:** jobs that ran with no COMPLETE event, tables no job's lineage accounts for, and declared classifications lower than lineage proposes.
