from lib import ai_data
from lib.catalog import open_catalog

built = ai_data.build(open_catalog(["crm.learners", "learning.courses", "learning.reviews"]))

# mlcroissant's default, then the card the pipeline writes, with Croissant 1.1 written out.
default, _ = ai_data.card(built, conforms_to=[])
print("conformsTo, mlcroissant's default:", default["conformsTo"])
card, warnings = ai_data.card(built)
ai_data.write_card(card, built.folder)
print("conformsTo, written out:", ", ".join(card["conformsTo"]))
print("datePublished, the run's:", card["datePublished"])
print("version, the run's:", card["version"])
print("the sources the card names:", ", ".join(source["@id"] for source in card["prov:wasDerivedFrom"]))
print("the tables that decided which rows, as the card names them:",
      ", ".join(table["@id"] for table in card["prov:wasInfluencedBy"]))
print("the activity the card names:", card["prov:wasGeneratedBy"]["prov:label"])
print("fields in the record set:", ", ".join(f["name"] for f in card["recordSet"][0]["field"]))
for line in card["rai:dataPreprocessingProtocol"] + card["rai:personalSensitiveInformation"]:
    print("-", line)

# What mlcroissant reports, and what the platform's own check of the card reports.
print("mlcroissant's warnings:", len(warnings))
for warning in warnings:
    print("- mlcroissant:", warning.split("] ", 1)[1])
problems = ai_data.card_problems(card, built.folder)
print("problems the book's check finds:", len(problems))
for problem in problems:
    print("-", problem)
