"""Generates the corpus's Croissant card twice, first with mlcroissant's default conformsTo and then as the platform writes it, and runs the platform's own check on it. The prose's point rests on the "conformsTo" lines and the "problems" line."""
from lib import ai_data
from lib.catalog import open_catalog

built = ai_data.build(open_catalog(["crm.learners", "learning.courses", "learning.reviews"]))

# mlcroissant's default, then the card the pipeline writes, with Croissant 1.1 written out.
# an empty list leaves conformsTo to mlcroissant, which writes its own default
default, _ = ai_data.card(built, conforms_to=[])
print("conformsTo, mlcroissant's default:", default["conformsTo"])
# the card as the platform writes it: Croissant 1.1 and the RAI vocabulary, written out
card, warnings = ai_data.card(built)
ai_data.write_card(card, built.folder)
print("conformsTo, written out:", ", ".join(card["conformsTo"]))
print("datePublished, the run's:", card["datePublished"])
# the build's date as the major number: a nightly build may remove records
print("version, the run's:", card["version"])
print("the sources the card names:", ", ".join(source["@id"] for source in card["prov:wasDerivedFrom"]))
print("the tables that decided which rows, as the card names them:",
      ", ".join(table["@id"] for table in card["prov:wasInfluencedBy"]))
print("the activity the card names:", card["prov:wasGeneratedBy"]["prov:label"])
# the card names every column of the corpus as a field
print("fields in the record set:", ", ".join(f["name"] for f in card["recordSet"][0]["field"]))
for line in card["rai:dataPreprocessingProtocol"] + card["rai:personalSensitiveInformation"]:
    print("-", line)

# What mlcroissant reports, and what the platform's own check of the card reports.
# the message after mlcroissant's severity tag
print("mlcroissant's warnings:", len(warnings))
for warning in warnings:
    print("- mlcroissant:", warning.split("] ", 1)[1])
# the platform's own check: the library's validator doesn't report every requirement
problems = ai_data.card_problems(card, built.folder)
print("problems the book's check finds:", len(problems))
for problem in problems:
    print("-", problem)
