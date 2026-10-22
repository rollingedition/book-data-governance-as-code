"""Ownership as ODCS contracts, a folder per domain, and who GitHub asks to review the downgrade of crm.learners.city
when the metadata is one shared file, and when each domain has a folder of contracts generated from it."""
import yaml

from lib import ci

main = ci.checkout()                      # the main branch holds the contracts and CODEOWNERS generated from its metadata
branch = ci.checkout(main)
ci.edit(branch, "governance/metadata.toml", '6 = { name = "city", classification = "personal"',
        '6 = { name = "city", classification = "internal"')

shared = ["/governance/ @example-org/team-platform"]          # one file for every domain, owned by the platform team
for path, owners in ci.reviewers(shared, ["governance/metadata.toml"]).items():
    print(f"one shared file, review of {path}:", " ".join(owners))

crm = yaml.safe_load((main / "governance/contracts/crm/learners.odcs.yaml").read_text(encoding="utf-8"))
print(yaml.safe_dump({"domain": crm["domain"], "team": crm["team"]}, sort_keys=False), end="")
lines = (main / ".github/CODEOWNERS").read_text(encoding="utf-8").splitlines()
for line in lines[1:]:                                       # the first line says the file is generated
    print("CODEOWNERS", line)
before, after = ci.build(main), ci.build(branch)          # the pull request changes the metadata only
print("teams that own tables:", len({table.owner for table in before.described.values()}))
for line in ci.lines(ci.run(before, after, checks=["contracts"]))[:-1]:
    print("before generating again,", line)
print("files generated again:", len(ci.generate(after)))
print("after generating again, contracts:", "passed" if ci.run(before, after, checks=["contracts"]).passed else "failed")
for path, owners in ci.reviewers(lines, ci.changed(main, branch)).items():
    print(f"a folder per domain, review of {path}:", " ".join(owners))
