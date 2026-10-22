import math

from lib import pii
from lib.catalog import open_catalog
from lib.warehouse import connect

con = connect(open_catalog(["crm.learners"]))
first = pii.sample(con, "crm.learners", "notes", rows=20)
second = pii.sample(con, "crm.learners", "notes", rows=20)
print("second draw, same notes as the first:", "yes" if second == first else "no")
# Notes can hold a phone number, which may be in service: masked() prints it with only its last four digits.
print("first sampled notes:", " | ".join(pii.masked(note) for note in first[:3]))

# How rare is personal data in the column? Run the detector over every note, once, to know.
detector = pii.platform_detector()
[(rows, notes)] = con.sql("select count(*), count(notes) from crm.learners").fetchall()
every_note = [note for (note,) in con.sql("select notes from crm.learners where notes is not null").fetchall()]
holding = sum(bool(pii.detect(detector, note)) for note in every_note)
print("learners:", f"{rows:,}")
print("learners with notes:", notes)
print("notes holding personal data the detector finds:", holding)

# The chance that n sampled values hold at least one of them is 1 - (1 - p)^n, for a share p of the values.
for unit, p in (("rows", holding / rows), ("notes", holding / notes)):
    print(f"chance 20 sampled {unit} catch one:", f"{1 - (1 - p) ** 20:.1%}")
    print(f"sampled {unit} needed for a 99% chance:", math.ceil(math.log(0.01) / math.log(1 - p)))
print("values the platform samples per text column:", pii.ROWS)
print("rarest share that sample catches 99% of the time:", f"1 in {math.floor(1 / (1 - 0.01 ** (1 / pii.ROWS)))}")
