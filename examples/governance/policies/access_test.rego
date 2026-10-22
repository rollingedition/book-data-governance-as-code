# Tests for access.rego, run by lib/policy.py's run_tests as `opa test` would run them. Every test of an allowed read
# compares the decision's row filters and column masks whole, so one filter or mask too many or too few fails it.
# Input is replaced one path at a time (`with input.action as ...`), never whole (`with input as ...`), and always with a
# literal, because of three ways regopy differs from OPA. A test that replaces the whole input and evaluates a rule that
# calls a built-in, a function or an operator such as `!=` makes every query fail. A mock whose value refers to another
# rule is ignored. And a string mocked from a local variable set to a literal, or from a built-in such as `lower`, keeps
# its quotes in sprintf.
package platform.access_test

import rego.v1

import data.platform.access

test_support_reads_only_its_own_region if {
	tickets := access.decision with input.context.identity.user as "agent-a05"
		with input.action as {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "support", "tableName": "tickets", "columns": ["ticket_id", "body"]}}}
	tickets.allow
	tickets.row_filters == [{"column": "support_region", "equals": "emea"}]
	tickets.column_masks == {}
	learners := access.decision with input.context.identity.user as "agent-a05"
		with input.action as {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "crm", "tableName": "learners", "columns": ["email", "phone"]}}}
	learners.allow
	learners.row_filters == [{"column": "support_region", "equals": "emea"}]
	learners.column_masks == {}
}

test_support_without_a_region_is_denied if {
	decision := access.decision with input.context.identity.user as "agent-new"
		with input.action as {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "support", "tableName": "tickets", "columns": ["ticket_id"]}}}
		with data.platform.users as {"agent-new": {"groups": ["support"]}}
	not decision.allow
}

test_marketing_sees_only_consenting_active_learners if {
	decision := access.decision with input.context.identity.user as "marketing-1"
		with input.action as {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "crm", "tableName": "learners", "columns": ["full_name", "email", "phone"]}}}
	decision.allow
	decision.row_filters == [{"column": "marketing_consent", "equals": true}, {"column": "status", "equals": "active"}]
	decision.column_masks == {"phone": "redact"}
}

# A group reads only the tables the role map gives it, and nothing but reads.
test_a_group_only_reads_its_own_tables if {
	payments := access.decision with input.context.identity.user as "marketing-1"
		with input.action as {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "billing", "tableName": "payments", "columns": ["amount"]}}}
	not payments.allow
	learners := access.decision with input.context.identity.user as "finance-1"
		with input.action as {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "crm", "tableName": "learners", "columns": ["learner_id"]}}}
	not learners.allow
	deleting := access.decision with input.context.identity.user as "finance-1"
		with input.action as {"operation": "DeleteFromTable", "resource": {"table": {"schemaName": "billing", "tableName": "payments", "columns": ["amount"]}}}
	not deleting.allow
}

test_finance_sees_only_the_last_four_digits if {
	decision := access.decision with input.context.identity.user as "finance-1"
		with input.action as {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "billing", "tableName": "payments", "columns": ["amount", "card_number"]}}}
	decision.allow
	decision.row_filters == []
	decision.column_masks == {"card_number": "last4"}
}

test_data_science_never_sees_card_numbers if {
	decision := access.decision with input.context.identity.user as "analyst-1"
		with input.action as {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "billing", "tableName": "payments", "columns": ["amount", "card_number"]}}}
	decision.allow
	decision.row_filters == []
	decision.column_masks == {"card_number": "redact"}
}

test_data_science_sees_pseudonyms_not_people if {
	decision := access.decision with input.context.identity.user as "analyst-1"
		with input.action as {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "crm", "tableName": "learners", "columns": ["learner_id", "email", "notes"]}}}
	decision.allow
	decision.row_filters == []
	decision.column_masks == {"email": "pseudonymize", "notes": "redact"}
}

test_an_unclassified_column_is_hidden if {
	decision := access.decision with input.context.identity.user as "analyst-1"
		with input.action as {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "crm", "tableName": "learners", "columns": ["shoe_size"]}}}
	decision.allow
	decision.row_filters == []
	decision.column_masks == {"shoe_size": "redact"}
}

test_an_unknown_user_is_denied if {
	decision := access.decision with input.context.identity.user as "someone-else"
		with input.action as {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "crm", "tableName": "learners", "columns": ["email"]}}}
	not decision.allow
	decision.column_masks == {}
}

test_an_instructor_sees_only_their_courses if {
	decision := access.decision with input.context.identity.user as "instructor-17"
		with input.action as {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "learning", "tableName": "reviews", "columns": ["rating", "body"]}}}
	decision.allow
	decision.row_filters == [{"column": "course_id", "in": data.platform.instructor_courses["17"]}]
	decision.column_masks == {"body": "redact_contacts"}
}

test_the_assistant_reads_only_consented_reviews if {
	decision := access.decision with input.context.identity.user as "assistant-1"
		with input.action as {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "learning", "tableName": "reviews", "columns": ["body"]}}}
	decision.allow
	decision.row_filters == [{"learner_consented": "ai_training_consent"}]
	decision.column_masks == {"body": "redact_contacts"}
}

test_training_reads_only_consented_reviews_without_contacts if {
	decision := access.decision with input.context.identity.user as "analyst-1"
		with input.action as {"operation": "SelectFromColumns", "resource": {"table": {"schemaName": "learning", "tableName": "reviews", "columns": ["review_id", "body"]}}}
		with input.purpose as "ai-training"
	decision.allow
	decision.row_filters == [{"learner_consented": "ai_training_consent"}]
	decision.column_masks == {"body": "redact_contacts"}
}
