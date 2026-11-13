---
chapter_id: a-catalog-you-can-test
---

# A catalog you can test

By the end of this chapter you can keep a catalog's metadata as code. Every table gets an owner and a domain, and every column a description and a classification, in a file your team reviews. The file is keyed so a rename can't lose anything, written into the Iceberg catalog, and checked by a test that fails when anything is missing. You'll also have a local model draft descriptions and classifications, and measure how often its drafts are wrong before anyone accepts them.

The platform's catalog has three problems. Its warehouse, an Iceberg catalog kept by an online business that sells professional courses to adult learners, holds eight source tables in namespaces named after the teams that run them, such as `crm.learners` and `support.tickets`. Its pipelines build reporting tables from those and keep creating tables, and nothing says who owns `marketing.audience`, the list of learners marketing may email, or whether `ai.review_corpus`, the text the platform's course assistant reads, holds personal data. A column renamed in a schema change can lose its classification without anyone noticing. And every table a pipeline adds needs a description and a classification for each of its columns, written by hand, which is slow enough that teams skip it.

[Chapter 1](#governance-end-to-end) built a rough version of the whole governed platform. This chapter makes its catalog solid.

The chapter's examples use five of the book's shared modules. `lib.catalog.open_catalog` builds a fresh catalog of the platform's tables in a temporary folder, on PyIceberg's SQL catalog over SQLite, which PyIceberg's docs recommend only for exploration and development ([PyIceberg configuration](https://github.com/apache/iceberg-python/blob/pyiceberg-0.12.0/mkdocs/docs/configuration.md)). `lib.pipelines.run` runs one of the platform's pipelines and writes its output to the catalog. `lib.warehouse.connect` opens DuckDB on the catalog's tables, so a query can name them as the platform does, and `lib.policy` evaluates the platform's access policies, which [chapter 5](#access-policies-as-code) writes. And `lib.metadata`, which this chapter builds up, loads the platform's metadata, writes it into the catalog and checks the catalog against it.

## Keep the metadata in a reviewed file {#keep-metadata-in-a-reviewed-file}

A catalog's **metadata** is what you know about each table that the data doesn't say: who owns it, which part of the business it belongs to, what it holds, and how each column must be protected. Catalog servers keep this in their own database and edit it in a web page. That works for browsing, but a change made there is invisible to code review, and no test can say whether the catalog is complete. Keeping the metadata in a file in the repository, beside the pipelines that create the tables, makes every change a diff that someone approves, and gives a test something to compare the catalog with.

The platform's file is `governance/metadata.toml`. For each table it records:

- an **owner**, the team answerable for the table, never a person;
- a **domain**, the part of the business the table belongs to;
- a description of the table;
- for each column, its name, a description and a **classification**.

A classification is one of four values, ordered from least to most protected:

| Classification | Meaning | On the platform |
| --- | --- | --- |
| `public` | May be shown to anyone outside the platform | A course's title |
| `internal` | For the platform's staff, and doesn't identify a person by itself | When an enrollment started |
| `personal` | Identifies a person or tells something about one | A learner's email, or text they wrote |
| `sensitive` | Data whose leak would directly harm someone | A card number, an instructor's bank account |

The order matters: when a column is built from several others, as a pipeline's output columns often are, the stricter classification wins, and `lib.metadata.stricter` returns it. Loaded and counted, the file shows what it covers:

[Example: metadata-model](../examples/metadata-model.py)

Most columns are `internal`, because most of a warehouse is IDs, dates and counts: {{out:metadata-model:columns classified internal}} of its {{out:metadata-model:columns described}} columns are. The protected columns are few. Of the file's columns, {{out:metadata-model:columns classified personal}} are `personal`, and `sensitive` holds the card number in `billing.payments` and the payout account in `crm.instructors`. The file covers the eight source tables and the two reporting tables the platform's analysts rely on, and every table belongs to one of {{out:metadata-model:teams that own tables}} owning teams. The point is that each description carries the reason for its classification. `country` is `internal` because a country alone doesn't single anyone out, while `city` is `personal` because, as its description says, it identifies a learner together with their date of birth.

The four values and the one-owner rule are ours, not a standard's. The Open Data Contract Standard (ODCS) records a domain, an owner as a free-text role on a team, and a free-text classification ([ODCS v3.2.0](https://github.com/bitol-io/open-data-contract-standard/tree/v3.2.0/docs)), and its JSON schema accepts any string as a classification ([ODCS JSON schema](https://github.com/bitol-io/open-data-contract-standard/blob/v3.2.0/schema/odcs-json-schema-v3.2.0.json)). The catalog servers differ too. DataHub types its owners ([ownership types](https://docs.datahub.com/docs/ownership/ownership-types)), and it lets an asset belong to only one domain at a time ([domains](https://docs.datahub.com/docs/domains)). It classifies columns with glossary terms ([business glossary](https://docs.datahub.com/docs/glossary/business-glossary)).

OpenMetadata lets an owner be either a team or several users, a rule enforced at the instance level and on by default ([data ownership](https://docs.open-metadata.org/v2.0.x/how-to-guides/guide-for-data-users/data-ownership)). Its classifications are a controlled vocabulary of tags ([classification](https://docs.open-metadata.org/v2.0.x/how-to-guides/data-governance/classification)), such as `PII.Sensitive` ([PII tags](https://docs.open-metadata.org/v2.0.x/how-to-guides/data-governance/classification/sample-data-using-pii-tag)). In Databricks' Unity Catalog every object has an owner, who holds every privilege on it ([privileges](https://docs.databricks.com/aws/en/data-governance/unity-catalog/manage-privileges)). Tables and columns carry tags ([tags](https://docs.databricks.com/aws/en/database-objects/tags)), and a governed tag takes values "only from a predefined set of allowed values" ([governed tags](https://docs.databricks.com/aws/en/admin/governed-tags/)). A file like the platform's maps onto each of them. None of these documents describes the check this chapter's test makes: a team, never a person, as the owner of every table, and one of the four classifications on every column.

**Working rule:** Keep owners, domains, descriptions and classifications in a file in the repository, reviewed like the pipelines. Use a closed list of classifications, ordered by how much protection each needs, and one owning team per table. If your organization edits metadata in a catalog server's web page, make one side the source and generate the other from it; two sources that both accept edits drift apart.

## Key column metadata by field ID {#key-column-metadata-by-field-id}

[The platform's metadata file](#keep-metadata-in-a-reviewed-file) keys each column by a number, not by its name, because names change. Iceberg gives every column a **field ID**, an integer unique within the table's schema, and the spec requires that a rename keep it: "Renaming an existing field must change the name, but not the field ID" ([Iceberg table spec](https://iceberg.apache.org/spec/)). Readers find values by that ID too, since Iceberg selects a data file's columns by field ID, so a renamed column still reads the files written before the rename ([Iceberg table spec](https://iceberg.apache.org/spec/)). Metadata keyed by name has nothing to hold on to: after a rename, the old name points at nothing and the new name has no entry.

The platform's CRM team wants `crm.learners.email` called `contact_email`, to make room for a work email. Staged in a scratch catalog, the rename shows what a name-keyed copy loses. The column is looked up in the metadata file, keyed by field ID, and in a copy keyed by name, as a spreadsheet of columns usually is:

[Example: rename-orphans-name-keys](../examples/rename-orphans-name-keys.py)

The two lookups disagree, and only the one keyed by field ID knows the column is the same. The rename kept field ID {{out:rename-orphans-name-keys:contact_email field ID}}, and {{out:rename-orphans-name-keys:values read under the new name}} values came through under the new name. Keyed by name, `contact_email` has no classification, and the entry for `email` matches no column. Keyed by field ID, the column is still `personal`. A check built on the name-keyed copy would report an unclassified column and miss that a classified one had gone.

The ID-keyed file does notice the rename, and `lib.metadata.problems`, the check this chapter builds on, reports it: `contact_email: renamed from email since it was reviewed`. The data surviving a rename doesn't make the rename harmless: SQL that names `email` breaks, and so does any system that knows columns only by name. The platform's access policies, which [chapter 5](#access-policies-as-code) writes, receive the columns a query reads by name, so they read classifications from a name-keyed view that `lib.policy` generates from this file. When the check fails, someone updates the name in the file, rereads the description, and the generated view follows. Until then, the view has no entry for `contact_email`, and the policies treat a column they can't find as `sensitive`. A support agent, who sees every other personal column of `crm.learners`, gets `contact_email` as `{{out:rename-orphans-name-keys:a support agent reading contact_email sees it as}}`: the rename fails closed, and support notices first.

**Working rule:** Key column metadata by field ID, with the name beside it for people. Treat a name that no longer matches as a reason to review the column's description, not as a new column. Where a system knows columns only by name, generate its name-keyed view from the ID-keyed file, never maintain it by hand.

## Write descriptions into the table's schema {#write-descriptions-into-the-schema}

[The metadata file](#keep-metadata-in-a-reviewed-file) is where the platform's metadata is reviewed. People and tools that read a table, though, look in the catalog, so some of the metadata has to be copied there. The question is where each fact belongs in the catalog, and Iceberg answers part of it: each column can carry a `doc`, an optional comment that travels with the schema ([Iceberg table spec](https://iceberg.apache.org/spec/)). A table also has properties, a map of strings, but the spec says that map "is used to control settings that affect reading and writing and is not intended to be used for arbitrary metadata."

So we follow the spec as far as we can. Column descriptions go into `doc`, and classifications stay in the file, where the policies and checks read them. The owner and the domain go into two table properties, as a pointer for tools that read only the table, so anyone with a table and no repository can find the team to ask. That last choice goes against the spec's stated intent, and we keep it to those two short values.

The Iceberg project's own answer is a separate channel. The REST catalog spec of Iceberg 1.12.0 adds `labels`, ownership or classification that a catalog returns with a table, per table and per field ID, kept out of properties on purpose ([REST catalog spec](https://github.com/apache/iceberg/blob/apache-iceberg-1.12.0/open-api/rest-catalog-open-api.yaml)). The change came in with [pull request 15750](https://github.com/apache/iceberg/pull/15750). PyIceberg {{version:pyiceberg}}'s REST client doesn't read them ([source](https://github.com/apache/iceberg-python/blob/pyiceberg-0.12.0/pyiceberg/catalog/rest/__init__.py)).

`lib.metadata.apply` does the copying. For every table the file describes and the catalog holds, it writes each description into its column's `doc`, and the owner and domain into the properties, changing only what differs. Against a fresh catalog of the platform's eight source tables, `apply` shows what it changes:

[Example: apply-metadata](../examples/apply-metadata.py)

Applying the file writes the metadata into the catalog, and applying it again writes nothing, so `apply` can run on every merge. The first run changed {{out:apply-metadata:tables changed}} tables. The description of `email` is now in the Iceberg schema and in the Arrow schema a scan returns, so code that reads the table through PyIceberg gets it with the data, without knowing the file exists. The properties hold `owner=team-crm` and `domain=crm`. A second run changed {{out:apply-metadata:tables changed by applying again}} tables. The schema ID of `crm.learners` went from {{out:apply-metadata:crm.learners schema ID before}} to {{out:apply-metadata:crm.learners schema ID after}}, although no column's ID, name or type changed, which is the subject of [the next subsection](#a-description-edit-is-a-schema-change).

### A description edit is a schema change {#a-description-edit-is-a-schema-change}

Iceberg's spec gives every schema change a new schema with its own ID, which becomes the table's current schema and joins the list of schemas the table keeps ([Iceberg table spec](https://iceberg.apache.org/spec/)). In PyIceberg {{version:pyiceberg}}, editing nothing but a column's `doc` counts as such a change. Change nothing but the description of `learning.reviews.body`, after [`lib.metadata.apply`](#write-descriptions-into-the-schema) has written the platform's descriptions, and watch the schema ID:

[Example: doc-edit-schema-id](../examples/doc-edit-schema-id.py)

A description edit changes the schema ID and nothing a review should compare. The ID went from {{out:doc-edit-schema-id:schema ID before the edit}} to {{out:doc-edit-schema-id:schema ID after the edit}}, and the table keeps {{out:doc-edit-schema-id:schemas the table keeps}} schemas: the original, the one `apply` wrote, and this one. The field IDs, names and types are the same, which `lib.metadata.shape` compares. A rule that asks for a new classification whenever the schema ID changes would fire on every description edit, and teams learn to ignore a check that fires for nothing.

**Working rule:** Write column descriptions into Iceberg's `doc`, so every reader of the table gets them, and keep classifications in the reviewed file. Put no more than an owner and a domain in table properties, and say in your code why. To decide whether a schema change needs a review, compare field IDs, names and types, never schema IDs.

## Test the catalog against the metadata {#test-the-catalog-against-the-metadata}

With [the metadata applied to the catalog](#write-descriptions-into-the-schema), the remaining question is whether the file covers everything the catalog holds. A test answers it, and it has to start from the catalog: a check that walks the file finds problems only in tables someone already described. `lib.metadata.problems` lists every table in every namespace of the catalog, and reports a table with no entry or no owner, a column whose field ID has no classification, a classification that isn't one of the four, and a column renamed since it was reviewed.

On the platform, the gaps come from pipelines. Each pipeline is one `create table ... as select ...` in `pipelines/`, and running it creates a table. Run all four pipelines on a fresh catalog and check it, and the gaps show:

[Example: metadata-gaps](../examples/metadata-gaps.py)

The two tables that fail are the ones that matter most. The catalog holds {{out:metadata-gaps:tables in the catalog}} tables, and the check finds {{out:metadata-gaps:problems found}} problems: `ai.review_corpus: no metadata` and `marketing.audience: no metadata`. The two analytics tables pass, because the file describes them, with field IDs that match the order of their `select` lists.

`marketing.audience` copies learners' names and emails out of `crm.learners`, and `ai.review_corpus` copies the free text of reviews, so both hold personal data, and neither has an owner who would answer for it. Until someone classifies them, the platform's access policy, `governance/policies/access.rego`, treats every column it can't find a classification for as `sensitive`, so the gap fails closed rather than open. [Chapter 4](#classifications-from-lineage) proposes their classifications from lineage, from the columns each output column was built from.

The test is then one line, `assert not metadata.problems(catalog, metadata.load())`, run wherever the catalog changes: in the pull request that adds a pipeline, which [chapter 10](#run-every-rule-as-one-job) sets up, and on a schedule against the production catalog, which catches tables created outside any pull request.

**Working rule:** Walk the catalog, not the metadata file, so a table nobody described shows up. Fail the test on any table without an owner and any column without one of the four classifications, and never default a missing classification to anything but the most protected value. A pipeline that creates a table ships with that table's metadata, in the same pull request.

## Catch schema changes before the metadata goes stale {#catch-schema-changes}

[The test against the catalog](#test-the-catalog-against-the-metadata) catches new tables. Tables also change underneath their metadata, and each kind of change needs its own response. A rename keeps the field ID, so the entry survives, but its description may now be wrong. An added column gets a field ID nobody has reviewed. A dropped column leaves an entry that describes nothing. And dropping a column and adding one with the same name doesn't bring the old column back: the spec gives every added field a new ID ([Iceberg table spec](https://iceberg.apache.org/spec/)), so the re-added column is a different column.

Schema changes are frequent enough for this to matter. In a study of ten open-source applications, "80% of the schema changes happened in 20-30% of the tables" ([Qiu, Li and Su, 2013](https://www.cs.ucdavis.edu/~su/publications/fse13-db-study.pdf)). That study covers application databases, not warehouses. If a warehouse's changes concentrate the same way, its busiest tables are the ones whose metadata goes stale first.

Two changes staged on `crm.learners` show both checks working: one adds `preferred_name`, with a description but no classification, and the other drops `country` and adds it back. The code runs `problems` and `lib.metadata.dropped`, which reports entries whose field ID the table no longer has, and reads the dropped column's values the way Iceberg's time travel does, by naming a snapshot:

[Example: schema-changes-checked](../examples/schema-changes-checked.py)

A re-added column is a new column: `country` came back as field {{out:schema-changes-checked:country field ID, after it was added back}}, not {{out:schema-changes-checked:country field ID, before it was dropped}}, and a plain scan, which reads through the current schema where the old field no longer exists, returns {{out:schema-changes-checked:country values after it was added back}} for it. Both added columns are unclassified, and the description given to `preferred_name` doesn't count: a description is a doc in the schema, and a classification is a reviewed line in the file.

The learners' countries haven't gone anywhere. A schema change writes no snapshot ({{out:schema-changes-checked:snapshots the schema changes wrote}} here), so the table's current snapshot is still the one its rows were written in. A snapshot records the ID of the table's current schema when it was created ([Iceberg table spec](https://iceberg.apache.org/spec/)), and in PyIceberg {{version:pyiceberg}}, a scan that names a snapshot reads with that schema. So time travel to that snapshot returns {{out:schema-changes-checked:country values, read by time travel to that snapshot}} countries. Dropping a column deletes none of its values: [chapter 7](#retention-consent-erasure) is where the platform erases data for good. `dropped` reports that the reviewed entry for the old field now describes nothing. The rename from [the field ID section](#key-column-metadata-by-field-id) is the third kind of change, which `problems` reports as a column to review.

**Working rule:** Run both checks on every schema change: `problems` for columns nobody has classified or renamed ones nobody has re-read, and `dropped` for entries that describe nothing. Remove a dropped column's entry in the same pull request that drops it. Dropping a personal column doesn't remove its values, which time travel reads until the snapshots that hold them expire and their files are deleted. Never drop and re-add a column to rename it: rename it in place, so it keeps its field ID and its data.

## Classify pseudonymous IDs for joining {#classify-pseudonymous-ids}

[The metadata file](#keep-metadata-in-a-reviewed-file) classifies some columns that point at people as `internal`, and the most common of them is `learner_id`. It's a number with no meaning of its own, and the platform's analysts and data scientists need it to join enrollments, events, payments and tickets to each other. An ID like this is a **pseudonymous identifier**: it doesn't name anyone, but it leads to someone through another table. One ID reaches further than its classification suggests. The code counts the tables that carry it, joins enrollments to learners, and asks the access policy who can make that join and see names:

[Example: pseudonymous-learner-id](../examples/pseudonymous-learner-id.py)

One join reaches a person: {{out:pseudonymous-learner-id:enrollments whose learner_id leads to an email}} enrollments lead to an email, and with the email come the learner's name, phone number, address and date of birth, which are among the table's `personal` columns. Yet `learner_id` is `internal` in all {{out:pseudonymous-learner-id:tables with a learner_id column}} tables that carry it.

The EU's General Data Protection Regulation (GDPR) addresses data like this in Recital 26: "Personal data which have undergone pseudonymisation, which could be attributed to a natural person by the use of additional information should be considered to be information on an identifiable natural person" ([GDPR, Recital 26](https://eur-lex.europa.eu/eli/reg/2016/679/oj)). What the recital means for a given platform is a question for its lawyers, and we don't answer it.

What we do decide is how the book's code treats the ID. `internal` describes what the column reveals in the table that holds it, and each `learner_id`'s description in the metadata file says the rest, naming `crm.learners` as the table that resolves it, as `learning.enrollments`' does: "The learner who enrolled. Identifies a person when joined with crm.learners." The protection sits on the table that resolves the ID. [Chapter 5](#return-the-decision-as-one-document) decides who may read the personal columns of `crm.learners`, and the example's last lines ask its policy who sees learners' names there: {{out:pseudonymous-learner-id:users who see learners' names in crm.learners}}. Of them, only the support agents also read a `learner_id` in another table: {{out:pseudonymous-learner-id:of them, users who read learner_id in another table}}. They can put a name on every ticket of their region, and that's their job. Marketing sees names but reads no other table that holds the ID.

So the policy, not the ID's classification, decides who can make the join. Classifying `support.tickets.learner_id` as `personal` would change nothing for support, the only group that reads the table, because the policy already shows support every personal column. Erasure, in [chapter 7](#plan-erasure-from-lineage), has to reach every table that holds a learner's `learner_id`, whatever its classification. We call such IDs pseudonymized, never anonymized.

**Working rule:** Classify a pseudonymous ID by what its own table reveals, so the joins your analysts need keep working, and say in its description what it resolves through. Protect the table that resolves it, and include the ID in every erasure. Who can resolve the ID is an access decision: list the users who may read both the ID and the resolving table's personal columns unmasked, and grant that pair only to those whose job is linking rows to people.

## Draft metadata with a local model {#draft-descriptions-with-a-local-model}

[The test against the catalog](#test-the-catalog-against-the-metadata) tells you which columns nobody has described, but it doesn't write the descriptions. Writing a description and choosing a classification for every column is the slowest part of keeping a catalog, and the work comes back with every table a pipeline adds. A language model can draft both from what a catalog tool can show it: a column's name, its type and a few of its values. Research supports both the help and its limits. On one benchmark, given instructions and a two-step pipeline, ChatGPT labelled the semantic types of table columns with "F1 scores of over 85% in zero- and one-shot setups" ([Korini and Bizer, 2023](https://arxiv.org/abs/2306.00745)). A study of generated column descriptions found that models struggle with columns whose meaning is ambiguous, and that those columns need manual expert input ([Wretblad et al., 2024](https://arxiv.org/abs/2408.04691)). So a model's draft is a proposal that a person accepts, corrects or rejects in the pull request. The first job is to measure how wrong the drafts are, so the review knows where to look.

The book's drafts, which the examples call the metadata suggestions, were recorded once by `recordings/metadata_suggestions.py`, and the examples read that recording, so none of them calls a model. The script runs Qwen3-1.7B at a pinned revision. The model is open, under the Apache-2.0 licence, so anyone can download the same weights. At 1.7 billion parameters it runs on a CPU, with no GPU, which keeps the sample values on the machine that runs it. And it's instruction-tuned, with a switch that turns off its "thinking", the reasoning it otherwise writes before it answers. The script decodes greedily, taking the most likely next token, although the model's card suggests sampling in this mode: "Temperature=0.7, TopP=0.8, TopK=20, and MinP=0" ([Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B)). A recording has to come out the same when someone records it again, and a sampled answer is one draw among many.

For each column of the platform's eight source tables, the model saw the table's name, the column's name and Iceberg type, five of the column's values chosen with a fixed seed, and the four classifications with the meanings from [the table of classifications](#keep-metadata-in-a-reviewed-file). Each meaning comes with two examples, and none of them is a column the platform has. For `sensitive`, they are "a government ID number or a health condition". The model was asked for a one-sentence description and one classification, as JSON. The recording's manifest names the model, its revision, a hash of the prompts and settings, and the date, so anyone can record again and compare.

Those five values are personal data whenever the column is: for `support.tickets.body`, the tickets the model read held learners' names, street addresses, a phone number, a date of birth and a bank account number. A model running on your own machine keeps them there. Sending the same prompt to a hosted model sends the platform's personal data to another company, which needs the same decision as any other transfer of it.

Set against the reviewed file, each draft's classification shows whether it agrees:

[Example: suggestion-agreement](../examples/suggestion-agreement.py)

All {{out:suggestion-agreement:columns the model was asked about}} answers are valid JSON with one of the four classifications, which shows the format worked and says nothing about whether the answers are right. They agree with the review on {{out:suggestion-agreement:classifications that agree with the review}} columns, and that single number hides where the misses are. Most of the agreement is `internal` columns called `internal`. Of the platform's personal columns, the model called {{out:suggestion-agreement:personal columns the model called personal}} `personal`, and of its `sensitive` ones, the card number and the payout account, {{out:suggestion-agreement:sensitive columns the model called sensitive}}: it called both `internal`.

### Examples in a prompt can give the answer away {#prompt-examples-give-answers-away}

A prompt's examples are part of the measurement. The script also records the same model on the same columns under a second prompt, identical but for its examples. That prompt gives examples for `personal` and `sensitive` only, and they're the platform's own columns. For `personal`, they are "a name, contact details, an address or text they wrote", and for `sensitive`, a card number or a bank account. Those two are `billing.payments.card_number` and `crm.instructors.payout_iban`, the only sensitive columns. Side by side, the answers show what the examples change:

[Example: suggestions-with-platform-examples](../examples/suggestions-with-platform-examples.py)

A prompt that names the platform's own columns gets the sensitive ones right, and the book's prompt does not. With the platform's columns as examples, the model gets {{out:suggestions-with-platform-examples:the prompt with the platform's columns as examples, sensitive columns called sensitive}} sensitive columns right. The book's prompt gets {{out:suggestions-with-platform-examples:the book's prompt, sensitive columns called sensitive}}, and calls both `internal`.

Overall agreement hardly moves, {{out:suggestions-with-platform-examples:the prompt with the platform's columns as examples, classifications that agree with the review}} against {{out:suggestions-with-platform-examples:the book's prompt, classifications that agree with the review}}, so one number would never show the difference. Personal columns gained nothing from their examples: {{out:suggestions-with-platform-examples:the prompt with the platform's columns as examples, personal columns called personal}} came out `personal` under that prompt too.

"A card number" names `card_number` almost word for word, while "a name" leaves the model to decide that `full_name` is one, and it didn't. A prompt whose examples match the catalog makes a model look better than it is, on the columns that need protection most, so the book's prompt takes its examples from outside the platform's data.

**Working rule:** Draft metadata with a model only from inputs you're allowed to send it, preferably to a model on your own machines. Record each answer with the model, its revision, the prompt and the settings, so a reviewer can see what produced each draft, and parse and validate every answer before anything reads it. Take the prompt's examples from outside the tables you measure it on. Report agreement with the reviewed metadata per classification, never as one number.

## Measure where the model's drafts are wrong {#measure-where-the-drafts-are-wrong}

[The recorded drafts](#draft-descriptions-with-a-local-model), a local model's descriptions and classifications for the platform's source tables, agree with the reviewed classifications on most columns. For governance, which way they miss matters more. A classification more protected than it should be costs a reviewer a minute and an analyst a masked column. One less protected than it should be leaves personal data where the policies treat it as staff data, or as data anyone may see. Splitting the misses by direction shows which way the model fails:

[Example: suggestion-mistakes](../examples/suggestion-mistakes.py)

The model's misses fall on the columns that need protection most. It protected {{out:suggestion-mistakes:columns the model protects less than the review}} columns less than the review did, and {{out:suggestion-mistakes:columns the model protects more than the review}} more. Every personal and sensitive column is among the less protected. It called {{out:suggestion-mistakes:personal columns the model called internal}} personal columns `internal`, among them the names and emails in both `crm.learners` and `crm.instructors`, the learners' phone numbers, dates of birth and notes, the IP addresses of events and the text of support tickets. It called {{out:suggestion-mistakes:personal columns the model called public}} of them `public`: learners' cities and street addresses, and the text of their reviews. The prompt's own meaning for `personal` begins "identifies a person or tells something about one", and a name or an email does both.

The other less protected columns are timestamps, an event's type and its user agent, which the model called `public` where the review chose `internal`: a smaller mistake, but one a reviewer still has to catch. The misses the other way cost less. The model called the {{out:suggestion-mistakes:internal columns the model called sensitive}} consent flags `sensitive`, and it called the course catalog's public ID, title and price `internal`.

Descriptions fail in another way, and four of the model's show it beside the reviewed ones:

[Example: suggestion-descriptions](../examples/suggestion-descriptions.py)

Every description is fluent and plausible, and three of the four miss something that only someone who knows how the platform fills the column could write. `ai_training_consent` records whether the learner's text may train the platform's AI, not whether the learner wants training, and a null means the question was never asked. `notes` are written by support agents about learners, as the values hint, with a note about invoices for the learner's employer, and not by learners. And `card_number` is a legacy column that shouldn't exist at all, which is the most useful thing its description can say. The fourth is right in substance: the platform gives each learner the `support_region` of the support team that serves their country, and the model calls it their "regional support area". Nothing in a fluent description says whether it's right or missing something, so a reviewer reads every one. The authors of one method for generating column descriptions name the same failure in single-prompt models, which write hallucinated or incomplete descriptions ([Cai et al., 2026](https://arxiv.org/abs/2606.21685)).

These are one small model's answers, decoded greedily, to two prompts. The recording can't tell whether the model misses personal columns because of its size, or because it drafts with only a name, a type and five values to go on. A larger model or a better prompt may get more columns right, and the way to find out is to measure it the same way, against the reviewed file, before trusting it with tables nobody has reviewed.

**Working rule:** Let a model raise a column's classification, never lower it: only a reviewer lowers one, in the pull request, with the reason in the column's description. Treat the model's descriptions as first drafts for someone who knows the column to rewrite. Before a model drafts metadata for tables nobody has reviewed, measure it against your reviewed metadata, per classification and per direction of error, and measure again whenever the model or the prompt changes.

## What goes wrong in production {#catalog-in-production}

A catalog's metadata fails quietly: nothing errors when it goes stale, and each failure below looks complete until someone relies on it.

- **Owners go stale.** Teams are renamed, merged and dissolved, and the owner in the file outlives them. A team, unlike a person, survives one departure, but the file still needs a check that every owner is a team that exists, run against your organization's list of teams. If you keep ownership in ODCS, its team members carry a `dateOut` and a `replacedByUsername` ([ODCS v3.2.0](https://github.com/bitol-io/open-data-contract-standard/tree/v3.2.0/docs)), which a check can read to find a team with no current member in the `owner` role.
- **Descriptions describe last year's schema.** A column's meaning changes while its name and type stay the same: `amount` starts holding refunds as negative numbers, or `status` gains a value. No schema check sees it. Code comments fail the same way. Research on them found that a detector catches inconsistencies more accurately when it sees a change before it's committed than when it sees only the code that results ([Panthaplackel et al., 2021](https://arxiv.org/abs/2010.01625)). For columns, the comparison holds only where a pull request shows the change. A pipeline's code changes in a diff, but a source system can change what it writes with no diff anywhere. Where there is a diff, make the pull request that changes a pipeline's logic show the descriptions of the columns it writes, so the reviewer reads both.
- **Metadata keyed by name goes stale on a rename.** A spreadsheet of columns, a dashboard's data dictionary, or a policy written against column names loses a column on every rename, as [the field ID section](#key-column-metadata-by-field-id) shows, and reports nothing. Generate every name-keyed view from the ID-keyed file, and run `problems` on every schema change.
- **Two sources of metadata overwrite each other.** A catalog server's web page and a file in a repository both accept edits, and a sync in either direction loses the other's. DataHub keeps edits made in its UI apart from ingested metadata, so that ingestion pipelines don't overwrite what users entered ([DataHub dataset entity](https://docs.datahub.com/docs/generated/metamodel/entities/dataset)). Choose one source, and make the other read-only or generated.
- **Description edits trigger reviews nobody needs.** In PyIceberg every doc edit gives the schema a new ID, as [the doc edit above](#a-description-edit-is-a-schema-change) shows, so a review keyed on schema IDs fires for nothing. Compare field IDs, names and types instead.
- **Drafted classifications are approved in bulk.** A pull request that adds forty model-drafted columns gets its descriptions read and its classifications skimmed, and [the recorded drafts](#measure-where-the-drafts-are-wrong) show where that leads. List each drafted classification separately from the descriptions, least protected first, so the reviewer sees what each draft would leave unprotected.
- **A table created outside the pipelines escapes the test.** A notebook or a one-off backfill creates a table nobody will add to the file. Run [`problems`](#test-the-catalog-against-the-metadata) on a schedule against the production catalog, not only in pull requests.

## What this chapter decided {#catalog-decisions}

- **Metadata is a reviewed file:** owners, domains, descriptions and classifications live in `governance/metadata.toml`, changed only by pull request.
- **Four classifications, ordered:** `public`, `internal`, `personal` and `sensitive`, with the stricter one winning when a column is built from several. A column without one is treated as `sensitive`.
- **One owning team per table,** never a person, and a domain beside it.
- **Columns are keyed by Iceberg field ID,** with the name kept beside it so a rename shows up for review.
- **Descriptions go into Iceberg's `doc`,** written by `lib.metadata.apply`. The owner and domain go into table properties as a pointer, and classifications stay in the file.
- **The catalog is tested against the file:** `problems` walks every table in the catalog, and `dropped` finds entries for columns that are gone. Schema changes are compared by field IDs, names and types, not by schema IDs.
- **`learner_id` is `internal`** in every table, with a description that says it resolves through `crm.learners`. The protection sits on that table, and the access policy decides who may join the two with names in view.
- **`apply` and `problems` run on every change:** `apply` writes the file into the catalog on every merge, and `problems` runs in every pull request and on a schedule against the production catalog.
- **The model that drafts metadata runs on the platform's own machines,** because the sample values it's shown are personal data whenever the column is.
- **Model-drafted metadata is a proposal.** Drafts are recorded with their model, measured against the reviewed file, and rewritten by a person. A model may raise a classification, never lower it. A prompt that names the catalog's own columns flatters the model, so the examples in a prompt come from outside the platform's data.
