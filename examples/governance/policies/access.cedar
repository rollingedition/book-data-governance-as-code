// The platform's access rules in Cedar, for comparison with access.rego, which is the policy the platform enforces.
// Cedar answers one question: may this principal take this action on this resource? Its answer is Allow or Deny,
// never a row filter or a column mask. Every request is denied unless a permit allows it, and a forbid overrides
// any permit.

// Which groups may read which tables, as in access.rego's `readable`. A column or a learner is in its table.
@id("support-tables")
permit (principal in Group::"support", action == Action::"read", resource)
when { resource in [Table::"crm.learners", Table::"support.tickets"] };

@id("instructor-tables")
permit (principal in Group::"instructor", action == Action::"read", resource)
when { resource in [Table::"learning.courses", Table::"learning.enrollments", Table::"learning.reviews"] };

@id("marketing-tables")
permit (principal in Group::"marketing", action == Action::"read", resource)
when { resource in [Table::"crm.learners", Table::"learning.courses", Table::"marketing.audience"] };

@id("finance-tables")
permit (principal in Group::"finance", action == Action::"read", resource)
when { resource in [Table::"billing.payments", Table::"learning.courses", Table::"learning.enrollments"] };

@id("data-science-tables")
permit (principal in Group::"data-science", action == Action::"read", resource)
when {
  resource in [
    Table::"crm.learners", Table::"learning.courses", Table::"learning.enrollments", Table::"learning.events",
    Table::"learning.reviews", Table::"billing.payments", Table::"analytics.course_performance",
    Table::"analytics.learner_activity"
  ]
};

@id("ai-tables")
permit (principal in Group::"ai", action == Action::"read", resource)
when { resource in [Table::"learning.courses", Table::"learning.reviews"] };

// Support agents read only the learners in their own region. Cedar decides one learner at a time.
@id("support-region")
forbid (principal in Group::"support", action == Action::"read", resource is Learner)
unless { principal has region && resource.support_region == principal.region };

// Sensitive columns are hidden from everyone, except card numbers from finance. Cedar has no way to say "the last
// four digits": finance gets the whole column or nothing.
@id("sensitive-columns")
forbid (principal, action == Action::"read", resource is Column)
when { resource.classification == "sensitive" }
unless { principal in Group::"finance" && resource == Column::"billing.payments.card_number" };
