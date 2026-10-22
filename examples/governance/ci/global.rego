# Rules every domain's data contracts follow, owned by the platform team. lib/ci.py runs them on each ODCS contract it
# generates from governance/metadata.toml, one contract per table, as Conftest would run them on a file.
#
# Input: one contract (ODCS v3.2.0). Output: data.platform.ci.global.deny, a set of violations, each an object with the
# rule's name, the subject it's about and a message. An empty set means the contract passes.
package platform.ci.global

import rego.v1

levels := {"public", "internal", "personal", "sensitive"}

# ODCS has no owner field: ownership is a role on a member of the contract's team. ODCS's own examples write the role
# as "Owner", so the rule ignores case.
owners := {member.username | some member in input.team.members; lower(member.role) == "owner"; member.username != ""}

deny contains violation if {
	count(owners) != 1
	violation := {"rule": "one-owner", "subject": input.id, "message": sprintf("%d owners, where one is required", [count(owners)])}
}

# A domain that is missing, empty or blank is no domain: an empty string is a defined value in Rego, so `not` alone
# would let it through.
deny contains violation if {
	not input.domain
	violation := {"rule": "one-domain", "subject": input.id, "message": "no domain"}
}

deny contains violation if {
	trim_space(input.domain) == ""
	violation := {"rule": "one-domain", "subject": input.id, "message": "no domain"}
}

# ODCS accepts any classification string; the platform accepts four.
deny contains violation if {
	some object in input.schema
	some property in object.properties
	not property.classification in levels
	subject := sprintf("%s.%s", [object.physicalName, property.name])
	violation := {"rule": "known-classification", "subject": subject, "message": sprintf("classification %s is not one of the four", [property.classification])}
}

deny contains violation if {
	some object in input.schema
	some property in object.properties
	property.description == ""
	subject := sprintf("%s.%s", [object.physicalName, property.name])
	violation := {"rule": "described", "subject": subject, "message": "no description"}
}
