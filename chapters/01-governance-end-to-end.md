---
chapter_id: governance-end-to-end
---

# Governance end to end

By the end of this chapter you can govern one table of a data platform with code, end to end. You'll catalog it with an owning team and a classification for every column, capture the lineage of a pipeline that copies it, decide who may read which of its rows and enforce that decision on a query, and see a pull request fail because it added a column nobody classified. Each piece is small and runs in seconds, and five later chapters each make one of them solid.

This book is for data, analytics and platform engineers who already build pipelines and warehouses and have been asked to make them governed. You're comfortable with SQL, Python and Parquet or a lakehouse table, and you don't need governance theory: you need governance that runs, is tested, and is reviewed in pull requests like the rest of your code. By the end of the book, you can govern a data platform with code that runs in CI:

- a catalog where every table has an owner and every column a classification;
- personal data found automatically, with what the detector misses measured;
- lineage captured from pipelines;
- access policies as tested code, enforced as row filters and column masks;
- retention and erasure that remove data;
- an audit trail;
- checks that fail a pull request;
- the same rules applied to data that trains or grounds AI.

This book follows one case throughout: an online learning platform that sells professional courses to adult learners, which we call the platform. Its warehouse is a catalog of Iceberg tables, each under the namespace of the team that runs it: learners and instructors under `crm`, courses, enrollments, learning events and course reviews under `learning`, payments under `billing`, and support tickets under `support`. SQL pipelines build reporting tables from them. Support agents, instructors, marketing, finance, data scientists and the platform's course assistant, its AI, each need something different from those tables, and that's what makes the platform worth governing.

The platform's data, which every example in this book reads, is generated. A script makes it once from fixed seeds with Faker, a library for fake data, and the files are committed with a data card that says how they were made. No real person is in the data. Its email addresses are on the domains reserved for examples, so they can't reach anyone, and its IP addresses are in the ranges reserved for documentation, so they point at no one. Its phone numbers, card numbers and IBANs are random in a valid format, so one may match a number or an account in service: never call, message, charge or pay them. The card also lists what is planted for the examples to find, such as full card numbers in a legacy column and personal data in free text.

Every example runs offline, in one Python {{version:python}} process, on pinned libraries: PyIceberg {{version:pyiceberg}} for the Iceberg catalog and its tables, DuckDB {{version:duckdb}} for queries, sqlglot {{version:sqlglot}} to parse SQL, openlineage-python {{version:openlineage-python}} for lineage, and regopy {{version:regopy}} for policies written in Rego, the policy language of Open Policy Agent (OPA). Six services that a production platform runs don't run here, and each has a stand-in:

- **Trino**, the SQL engine every tool would query through: DuckDB, behind the book's enforcement layer, which rewrites each query before DuckDB runs it.
- **OPA's server**, which Trino asks for access decisions ([Trino 484, OPA access control](https://trino.io/docs/484/security/opa-access-control.html)): regopy, evaluating the same Rego inside the Python process.
- **Spark**, or whatever engine runs the pipelines at scale: DuckDB, running each pipeline's SQL file.
- **A catalog server or a cloud catalog**, serving table metadata through an API such as the Iceberg REST catalog's ([REST catalog spec, 1.12.0](https://github.com/apache/iceberg/blob/apache-iceberg-1.12.0/open-api/rest-catalog-open-api.yaml)): PyIceberg's SQL catalog, where a SQLite file records where each table's metadata is, and the metadata files sit beside the data on the local disk.
- **A lineage server**: a file of OpenLineage events, which the book's code reads back into a graph.
- **The CI service and git**: two folders, one for the main branch and one for a pull request's branch.

The examples teach the open standards those services work with, Iceberg's table metadata, OpenLineage and Rego, so what you build maps onto them.

On the platform, governance starts with a spreadsheet. Someone listed the columns of `crm.learners`, the table that holds every learner's name, email, phone number and address, with a classification beside each, and that's all there is. No team is named as the table's owner. A nightly pipeline copies learners' names and emails into a table for marketing, and nothing records that it does. Support agents each serve one region, but a query on the warehouse returns every region's learners. And the CRM team is about to add a column. This chapter answers each of those with the code that the later chapters build, at the smallest scale that works: one table, one pipeline, one rule and one check. Only one of the four is a policy engine; the others are metadata, lineage events and a test, kept in the same repository and reviewed the same way.

Five later chapters each make one piece solid. [Chapter 2](#a-catalog-you-can-test) keeps the catalog in step with schema changes and measures a local model's drafted metadata. [Chapter 4](#lineage-from-pipelines) uses lineage for impact analysis and to propose classifications. [Chapter 5](#access-policies-as-code) writes the whole policy, with its tests, and [chapter 6](#enforcing-policies-on-queries) enforces it on every query. [Chapter 10](#governance-in-ci) runs the checks of chapters 2 to 7 in CI, with owners' approvals and waivers. Four chapters add what this one leaves out: finding the personal data the metadata misses ([chapter 3](#finding-personal-data)), retention, consent and erasure ([chapter 7](#retention-consent-erasure)), an audit trail ([chapter 8](#audit-and-evidence)), and the same rules for data that trains or grounds AI ([chapter 9](#governing-data-for-ai)). The examples use the book's shared modules, files in `examples/lib/` that hold the code many examples need, which you can open and read; each section names the ones it uses.

## Catalog a table with its owner and classifications {#catalog-a-table}

The platform's first problem is its spreadsheet: a list of columns that nothing checks. A catalog lists a platform's tables and their schemas, and governance needs two facts the data doesn't carry: who answers for each table, and how each column must be protected. Kept in a spreadsheet or a catalog server's web page, those facts change without review, and no test can say whether they're complete. Kept in a file in the repository, beside the pipelines that create the tables, every change to them is a diff someone approves, and a test can compare the file with the catalog.

The platform's file is `governance/metadata.toml`. It gives each table an **owner**, a **domain**, the part of the business the table belongs to, and a description, and each column a description and a **classification**. Catalog tools mean different things by an owner, as [chapter 2](#keep-metadata-in-a-reviewed-file) compares, and we use one meaning throughout: the team accountable for the table, never a person, so the answer survives someone leaving. A classification is one of four values, from least to most protected:

- `public`: may be shown to anyone outside the platform;
- `internal`: for the platform's staff, and doesn't identify a person by itself;
- `personal`: identifies a person, or tells something about one;
- `sensitive`: data whose leak would directly harm someone, such as a card number.

Columns are keyed by their Iceberg field ID, which survives a rename, with the name beside it for people:

```toml
[tables."crm.learners"]
owner = "team-crm"
domain = "crm"
description = "Every learner who has signed up, with their contact details and consents. One row per learner."

[tables."crm.learners".columns]
3 = { name = "email", classification = "personal", description = "The email the learner signs in with." }
```

Three of the book's shared modules do the work. `lib.catalog.open_catalog` builds a fresh Iceberg catalog of the platform's tables in a temporary folder, which it removes when the example exits. `lib.metadata` loads the file, writes it into the catalog with `apply`, and checks the catalog against it with `problems`, which walks every table the catalog holds and reports a table with no metadata or no owner, and a column with no classification. Catalog `crm.learners`:

[Example: catalog-the-learners](../examples/catalog-the-learners.py)

The table's owner is `{{out:catalog-the-learners:owner}}`, in the domain `{{out:catalog-the-learners:domain}}`. Of its columns, {{out:catalog-the-learners:columns classified personal}} are `personal`: `{{out:catalog-the-learners:personal columns}}`. `notes` is among them although its name says nothing, because support agents type whatever they like into it. The other {{out:catalog-the-learners:columns classified internal}} are `internal`: the learner's ID, two dates, their country, their two consent flags, their account's status and their support region. After `apply`, the table's properties read `{{out:catalog-the-learners:table properties}}`, and `email`'s description sits in the Iceberg schema, where any reader of the table finds it: "{{out:catalog-the-learners:email's description, in the Iceberg schema}}" The classifications stay in the file, because that is where the access policy and the checks read them. And `problems` finds {{out:catalog-the-learners:problems the check finds}} problems: the catalog's one table has an owner, and every column one of the four classifications.

That zero is what a spreadsheet can't give you. It comes from walking the catalog, so a column the file doesn't describe would show up as a problem, as `work_email` does in [the section on spreadsheets](#a-spreadsheet-nobody-updates).

**Working rule:** Keep owners, domains, descriptions and classifications in one file in the repository, reviewed like the pipelines, with a team as every table's owner and one of a closed list of classifications on every column. Write the descriptions into the catalog, so every reader of a table sees them, and test the catalog against the file by walking the catalog, never the file. If your organization edits metadata in a catalog server's web page, make one side the source and generate the other from it.

## Capture one pipeline's column lineage {#capture-one-pipelines-lineage}

[The catalog](#catalog-a-table) says what a table holds. Lineage says where its data came from and where it went, and governance needs both: a table built from `crm.learners` may or may not copy its emails. OpenLineage, an open specification for lineage, describes a **job**, such as a pipeline, its **runs**, and the **datasets** each run reads and writes. A run reports itself in events: a START when it begins, then one that ends it, such as COMPLETE or FAIL. Events carry **facets**, OpenLineage's name for blocks of metadata attached to a run, a job or a dataset.

The column lineage facet, on the output of a COMPLETE event, lists for each output column the input columns it came from, each with a type. DIRECT means the "output column value was somehow derived from inputField value", and INDIRECT that the "output column value is impacted by the value of inputField column, but it's not derived from it" ([column lineage facet](https://openlineage.io/docs/spec/facets/dataset-facets/column_lineage_facet)): a join or a filter deciding which rows exist, or a grouping, sort, window or condition.

The client library computes no lineage of its own, as [chapter 4](#send-run-events) finds in its source: it sends what you give it. So the book's module `lib.lineage` parses each pipeline's SQL with sqlglot, adds the INDIRECT inputs that sqlglot's own lineage leaves out, and wraps `lib.pipelines.run`, which runs one of the platform's SQL pipelines on DuckDB and writes its output to the catalog: START before the run, COMPLETE with the lineage after it. The events go through OpenLineage's file transport to one file, stamped with a fixed time and a run ID derived from that time and the job's name, so the example writes the same bytes every time.

The platform's `marketing_audience` pipeline is one `create table ... as select ...` statement that writes `marketing.audience`, the list of learners marketing may email. It selects each learner's ID, name, email and country from `crm.learners`, joins each learner to their favourite course category, ranked from their enrollments, and keeps the learners for whom `l.marketing_consent and l.status = 'active'` holds. Run it with lineage, read the lineage back from the events alone, as a lineage server would, and count what the table holds with `lib.warehouse.connect`, which gives DuckDB a view of each catalog table:

[Example: audience-pipeline-lineage](../examples/audience-pipeline-lineage.py)

The file holds two events, `{{out:audience-pipeline-lineage:events}}`. The run read `{{out:audience-pipeline-lineage:the run read}}` and wrote `{{out:audience-pipeline-lineage:the run wrote}}`. Column by column, the lineage says what the table's name doesn't: `email` comes from `{{out:audience-pipeline-lineage:marketing.audience.email comes from}}`, and `full_name` from `{{out:audience-pipeline-lineage:marketing.audience.full_name comes from}}`, both unchanged copies of personal columns.

Two kinds of INDIRECT input shape the rows. The filters, `{{out:audience-pipeline-lineage:its rows are shaped by, INDIRECT FILTER}}`, mean that every row of the table says its learner consented. The joins, on `{{out:audience-pipeline-lineage:its rows are shaped by, INDIRECT JOIN}}`, decide which rows exist too: a learner with no enrollment has no favourite category, so the join drops them. `crm.learners` holds {{out:audience-pipeline-lineage:consenting, active learners in crm.learners}} consenting, active learners, and `marketing.audience` {{out:audience-pipeline-lineage:rows in marketing.audience}}. Walking the graph from `crm.learners.email` reaches `{{out:audience-pipeline-lineage:a change to crm.learners.email reaches}}`, so renaming the email column, or changing its format, reaches one column of this pipeline's table. With all four of the platform's pipelines sending lineage, [chapter 4](#impact-of-a-column-change) finds that it reaches no other.

The catalog check from [the section before](#catalog-a-table), run on the same catalog, finds `{{out:audience-pipeline-lineage:the catalog check finds}}`. The pipeline created a table that nobody has described, and lineage turns that gap from "a table we know nothing about" into "a copy of two personal columns, chosen by consent". Until someone describes it, the platform's access policy treats every column it can't find a classification for as `sensitive`. So anyone who reads the copy through the platform's enforcement layer, which [a later section](#enforce-one-access-decision) introduces, gets no value from it, as [chapter 6's who-sees-what test](#test-who-sees-what) shows.

**Working rule:** Send a START and then a COMPLETE or FAIL event from the code that runs each job, so no run happens without them, and put the output's column lineage in the COMPLETE event, parsed from the job's SQL, with the inputs that join and filter rows as well as those that supply values. Read lineage back from the events, never from the SQL, so the graph shows what ran. Where a job isn't SQL that a parser can read, such as a Python script, write its lineage in the same code, and review the two together.

## Enforce one access decision on a query {#enforce-one-access-decision}

With the platform's tables [cataloged](#catalog-a-table), the next question is who may read what. Policy as code starts from OPA's premise: it "lets you decouple policy from that software service so that the people responsible for policy can read, write, analyze, version, distribute, and in general manage policy separate from the service itself" ([OPA philosophy](https://www.openpolicyagent.org/docs/philosophy)). A **policy** answers a request with a **decision**, and the platform's policy answers with a document rather than a yes or no: whether to allow the read, the **row filters** that keep only the rows the user may see, and the **column masks** that change how a column is shown. The filters and masks are data, never SQL. A decision does nothing on its own, though. Something between the user and the engine has to apply it to every table a query reads.

On the platform, support agents each serve one region, `americas`, `emea` or `apac`, and need learners' contact details to answer tickets. The policy, `governance/policies/access.rego`, denies every request by default, allows each group, the role a user holds in `governance/users.toml`, the tables its work needs, and reads the agent's region from the same file. This is the rule that keeps an agent to their region:

```rego
row_filters contains {"column": "support_region", "equals": user.region} if {
	"support" in groups
	table in {"crm.learners", "support.tickets"}
}
```

`lib.policy.decide` evaluates the policy with regopy, and `lib.enforce` holds the **enforcement layer**: its `Enforcer` asks the policy about every table a query reads, replaces each with a subquery that applies the decision, and runs the rewrite on DuckDB. `lib.enforce.answer` is the one function a service calls, and returns rows, never the engine's connection. `agent-a05`, a support agent in EMEA, asks for three columns of `crm.learners`, then counts learners by region, and reads their emails, which the example compares with the platform's data as `lib.platform.load` reads it from the generated files:

[Example: support-region-enforced](../examples/support-region-enforced.py)

The decision allows the read, `allow: {{out:support-region-enforced:allow}}`, with one row filter, `{{out:support-region-enforced:row filters}}`, and no column masks, `{{out:support-region-enforced:column masks}}`, since support is the group that needs contact details. Its reason says which group allowed it: `{{out:support-region-enforced:reasons}}`. The agent's query has no filter on region, yet it returns {{out:support-region-enforced:learners agent-a05 sees in emea}} learners, all in EMEA, of the {{out:support-region-enforced:learners in the platform's data}} the platform holds. Their emails come back as stored, {{out:support-region-enforced:emails agent-a05 sees, as stored}}, because the decision masks nothing. The layer applied the region inside the query, so the agent's SQL didn't have to, and couldn't avoid it.

regopy is the Python binding of rego-cpp, Microsoft's C++ implementation of Rego, which supports "v1.18.1 of Rego as defined by OPA" ([rego-cpp README at v1.5.2](https://github.com/microsoft/rego-cpp/blob/v1.5.2/README.md)). regopy differs from OPA in ways [chapter 5](#where-regopy-differs-from-opa) lists, so OPA stays the reference for anything in production. [Chapter 6](#column-masks-leak) shows why the layer replaces whole tables rather than masking columns where a query names them.

**Working rule:** Write access rules as one policy in the repository that denies by default and returns its decision as data: allow, row filters and column masks. Apply that decision in one layer that rewrites every query before the engine runs it, and give users that layer's answers, never the engine's connection. When an engine can ask the policy itself, as Trino can ask OPA, let the engine be that layer.

## Fail the pull request that adds an unclassified column {#fail-the-pull-request}

[The catalog check](#catalog-a-table) proves the catalog complete on the day it runs, and the platform changes every week. A check that runs when someone remembers it is a report, and reports go unread. Run on every pull request, where its exit code can fail the pull request, the same check becomes a gate, and its findings appear where engineers already work. The authors of Tricorder, Google's platform for code analysis, wrote: "We have repeatedly found that when developers have to navigate to a dashboard or run a standalone command line tool, analysis usage drops off" ([Sadowski et al., 2015](https://static.googleusercontent.com/media/research.google.com/en//pubs/archive/43322.pdf)). That is one company's experience with code analysis, not data, but governance checks meet the same people. A gate also has to judge what the pull request changes, not everything on the platform: a finding already on the main branch is reported with the team that owns it, and fails no pull request that leaves it as it is.

The book's module for this is `lib.ci`, the platform's governance job. On the platform, a schema change is a migration: an `alter table` statement in a file in the branch's `migrations/` folder. The examples hold each side of a pull request in a folder, a checkout: one of the main branch and one of the pull request's branch. For each side, `lib.ci.run` builds a scratch platform, a fresh catalog made the way the platform runs: the source tables, the side's migrations, its metadata written into the catalog, and its pipelines. It runs the checks it's given on the branch's platform, runs them on main's alone to learn what is already there, and compares the findings by ID.

This section runs one check, `coverage`. It is [the catalog check](#catalog-a-table), `lib.metadata.problems`, run on the branch's platform, and for each column nobody classified it adds what a rule on column names suggests, from the rules [chapter 3](#classify-columns-from-the-schema) writes. The CRM team's pull request adds a column for learners' work email, in one migration, and forgets the column's metadata:

[Example: unclassified-column-fails](../examples/unclassified-column-fails.py)

The pull request changes one file, `{{out:unclassified-column-fails:files the pull request changes}}`. The coverage check reports `{{out:unclassified-column-fails:coverage}}`: the tables it checked are the platform's eight source tables and the four its pipelines write. Its new finding is the pull request's own: `crm.learners.work_email` is `{{out:unclassified-column-fails:coverage/crm.learners.work_email}}`. The name rule matches `email`, so the finding tells the author what to add, and its ID, `coverage/crm.learners.work_email`, stays the same from run to run, so it can be discussed and counted.

The other two findings were there before the pull request: `ai.review_corpus`, and `marketing.audience`, the table [the lineage section](#capture-one-pipelines-lineage) found undescribed, reported as `{{out:unclassified-column-fails:coverage/marketing.audience}}`. Nobody owns either table, so the job names the platform team, which the job's settings make answerable for tables without an owner. Of the findings, {{out:unclassified-column-fails:findings the pull request introduces}} is the pull request's own and {{out:unclassified-column-fails:findings already on main}} are already on main, and the job exits with code {{out:unclassified-column-fails:exit code}}. The pull request fails until the column has a classification, and the two tables on main fail no pull request that leaves them as they are. [Chapter 10's job](#run-every-rule-as-one-job) runs the checks of chapters 2 to 7 on the same pull request, {{out:governance-ci-report:checks run}} of them.

**Working rule:** Run every governance check as one job on every pull request, required by branch protection, and fail it by exit code on any finding the pull request introduces. Report the findings already on the main branch with their owners, without failing the pull request for them. Put every finding in the pull request itself, under an ID that stays the same from run to run, with what to do about it. Run the same checks on a schedule against the production catalog too, for what changes outside pull requests.

## What goes wrong in production {#end-to-end-in-production}

Governance fails without an error in two ways that the pieces above answer: [a spreadsheet nobody updates](#a-spreadsheet-nobody-updates), and [a policy that another tool bypasses](#a-policy-another-tool-bypasses). The first is why the metadata is code, and the second why enforcement sits in one place.

### A governance spreadsheet nobody updates {#a-spreadsheet-nobody-updates}

[The platform's governance started](#governance-end-to-end) with a spreadsheet of tables and columns, with a classification beside each, and a spreadsheet describes the schema on the day it was written. Schemas don't hold still. A study of ten open-source database applications, with more than 160,000 revisions between them, lists first among its major findings that "Database schemas evolve frequently during the application lifecycle" ([Qiu, Li and Su, 2013](https://www.cs.ucdavis.edu/~su/publications/fse13-db-study.pdf)). The study is of application databases, not warehouses, but every change that adds a column to a table adds one its spreadsheet doesn't list.

Take the platform's spreadsheet, an inventory of `crm.learners` exported as CSV from the catalog and the metadata, one row per column. Then the CRM team's migration from [the pull request above](#fail-the-pull-request) adds `work_email`. Compare what the spreadsheet says with what the check against the catalog finds:

[Example: spreadsheet-misses-new-column](../examples/spreadsheet-misses-new-column.py)

By its own account, the spreadsheet is complete: it lists {{out:spreadsheet-misses-new-column:columns the spreadsheet lists}} columns, and {{out:spreadsheet-misses-new-column:of them without a classification, by the spreadsheet}} lack a classification. The table has {{out:spreadsheet-misses-new-column:columns crm.learners has}}, and the one it doesn't list is `{{out:spreadsheet-misses-new-column:columns the spreadsheet doesn't list}}`. Nothing in the spreadsheet can show that, because nothing compares it with the catalog. `lib.metadata.problems`, [the catalog check](#catalog-a-table), walks the catalog and finds `{{out:spreadsheet-misses-new-column:the check against the catalog finds}}`: the same finding that fails [the CRM team's pull request](#fail-the-pull-request). As a rule of thumb, a copy of the metadata kept by hand, whether a spreadsheet, a wiki page or a data dictionary, goes stale on its first schema change, and nothing says so.

**Working rule:** Never keep governance metadata in a document that nothing checks. Keep it in a reviewed file that a test compares with the live catalog, on every pull request and on a schedule. If people need a spreadsheet, generate it from the file, and say on it when it was generated.

### A policy one tool enforces and another bypasses {#a-policy-another-tool-bypasses}

A policy protects only the reads that pass through whatever enforces it. Trino applies row filters only when it's told where to fetch them: its `opa.policy.row-filters-uri` setting is "The optional URI for fetching row filters - if not set no row filtering is applied" ([Trino 484, OPA access control](https://trino.io/docs/484/security/opa-access-control.html)). Iceberg's REST catalog specification, at 1.12.0, lets a catalog return read restrictions with a table, and says that "A reader that supports read restrictions must fail any read against the loaded table that cannot apply a returned restriction in full" ([REST catalog spec, 1.12.0](https://github.com/apache/iceberg/blob/apache-iceberg-1.12.0/open-api/rest-catalog-open-api.yaml)). The spec gives a reader no way to say that it supports them, as it does for delegated access to storage, with the `X-Iceberg-Access-Delegation` header: an "Optional signal to the server that the client supports delegated access". So a catalog can't tell which readers will apply its restrictions. PyIceberg {{version:pyiceberg}}'s REST client reads none ([source](https://github.com/apache/iceberg-python/blob/pyiceberg-0.12.0/pyiceberg/catalog/rest/__init__.py)). We recommend that a catalog that returns read restrictions give storage access only to engines that apply them.

On the platform, the [enforcement layer](#enforce-one-access-decision) keeps `agent-a05` to EMEA. Read the same table around it twice: as a notebook would, with DuckDB on the same catalog through `lib.warehouse.connect`, which the layer itself uses; and as any reader holding the catalog's access would, with PyIceberg's own scan:

[Example: read-around-the-layer](../examples/read-around-the-layer.py)

Through the layer, the agent's query returns `{{out:read-around-the-layer:through the enforcement layer, as agent-a05}}`. The same SQL on DuckDB with no layer in front returns `{{out:read-around-the-layer:DuckDB on the same catalog}}`, and PyIceberg's scan, which has no SQL to rewrite, returns `{{out:read-around-the-layer:PyIceberg's scan of the table}}`, with {{out:read-around-the-layer:emails PyIceberg's scan returns}} emails. The policy didn't fail: it was never asked. The same happens in production whenever a second engine, a Spark job or a script holding storage credentials reads the files that the governed engine reads.

`lib.warehouse` builds each table's view on a scan that it registers under a name of its own, `scan_crm_learners` for this table, so the layer's connection holds the raw scan too. The last line shows what the layer answers when a query names it: `{{out:read-around-the-layer:the layer, asked for the raw scan}}`. That's the design [chapter 6](#enforcing-policies-on-queries) follows: enforce in one place, and deny everything else that reads data, [whatever isn't a catalog table](#allow-only-catalog-sources). In production, that place is the engine every tool queries through, as [chapter 6](#engine-enforcement) argues, and since [storage permissions are part of enforcement](#enforcement-in-production), only that engine and the jobs that write the tables should be able to read the files.

**Working rule:** Choose one place that enforces the policy, the engine every tool queries through, and give storage credentials only to it and to the jobs that write the tables, never to people or notebooks. Deny every query in that place that reads data other than through a catalog table. Before you count a table as protected, list everything that can read its files, and treat any reader that doesn't ask the policy as a bypass, unless it's a job whose outputs are cataloged and classified like the tables it reads.

## What this chapter decided {#end-to-end-decisions}

- **Governance is four kinds of code in one repository:** a metadata file, the code that sends each job's lineage events, an access policy in Rego, and checks that run on every pull request. Only one of them is a policy engine.
- **Every table has an owning team,** never a person, and every column one of four classifications: `public`, `internal`, `personal` or `sensitive`. They live in `governance/metadata.toml`, keyed by Iceberg field ID, and the catalog is tested against the file by walking the catalog.
- **Every pipeline run sends OpenLineage events,** with column lineage parsed from its SQL, including the columns that join and filter rows.
- **Access decisions are data:** allow, row filters and column masks, from one policy that denies by default. An enforcement layer applies them to every query, and denies every read that isn't of a catalog table.
- **Missing metadata fails closed:** the access policy treats a column nobody has classified as `sensitive`, and a table nobody has described stays a finding in every report until someone describes it.
- **The governance job runs on every pull request,** fails on the findings the pull request introduces, and reports the findings already on main with their owners.
- **The examples run in one process, offline,** through the standards the production services share. PyIceberg's SQL catalog stands in for a catalog server, DuckDB for Spark, DuckDB behind the enforcement layer for Trino, regopy for OPA's server, a file of events for a lineage server, and two folders for the CI service.
