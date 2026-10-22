# The crm domain's own rules, decided and owned by team-crm, run by lib/ci.py on the contracts whose domain is crm only.
# Same input and output as global.rego.
package platform.ci.domains.crm

import rego.v1

# Everything in crm describes a learner or an instructor, so the domain shows nothing to the public.
deny contains violation if {
	some object in input.schema
	some property in object.properties
	property.classification == "public"
	subject := sprintf("%s.%s", [object.physicalName, property.name])
	violation := {"rule": "no-public", "subject": subject, "message": "crm classifies no column public"}
}
