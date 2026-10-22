"""Generate the platform's data: an online learning platform selling professional courses to adult learners.

    python prep/generate.py        (from the book's folder, in the environment of prep/requirements.txt)

Everything is generated from fixed seeds, so the same Faker version always writes the same files. No real person is
in them, and nothing in them reaches one: email addresses are on the domains RFC 2606 reserves (example.com, .net and
.org, and their subdomains), IP addresses are in the ranges RFC 5737 reserves for documentation, and names lose the
titles and degrees Faker adds. Phone numbers, card numbers and IBANs are random in a valid format, so one may match a
number or an account in service: never call, message, charge or pay them. Every fix here is applied to Faker's output, never by drawing again, so
fixing a value leaves every other value as it was. Writes one Parquet file per table to examples/data/platform/, and pii_spans.parquet: every piece of personal
data placed in the free text of support tickets and reviews, with its exact position, so chapter 3 can measure a
detector against the truth. The examples read only the committed files, so a Faker release changes nothing until this
script is run again.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from faker import Faker

OUT = Path(__file__).resolve().parent.parent / "examples" / "data" / "platform"
SEED = 2026
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc)            # the data stops here; the book's "today" is Oct 1, 2026
CONSENT_ASKED_FROM = datetime(2025, 6, 1, tzinfo=timezone.utc)      # AI-training consent was added to sign-up on this day

# Where learners come from: country, Faker locale, weight, currency and support region.
COUNTRIES = [
    ("US", "en_US", 30, "USD", "americas"), ("IN", "en_IN", 15, "INR", "apac"), ("GB", "en_GB", 10, "GBP", "emea"),
    ("DE", "de_DE", 8, "EUR", "emea"), ("BR", "pt_BR", 7, "BRL", "americas"), ("CA", "en_CA", 6, "CAD", "americas"),
    ("FR", "fr_FR", 5, "EUR", "emea"), ("ES", "es_ES", 5, "EUR", "emea"), ("AU", "en_AU", 5, "AUD", "apac"),
    ("PL", "pl_PL", 4, "PLN", "emea"), ("JP", "ja_JP", 3, "JPY", "apac"), ("MX", "es_MX", 2, "MXN", "americas"),
]
SIZES = {"learners": 5_000, "instructors": 50, "courses": 200, "enrollments": 20_000, "events": 100_000,
         "tickets": 2_000, "reviews": 3_000}
CATEGORIES = {
    "data": ["SQL for Analysts", "Data Modeling", "Dashboards That Get Used", "Statistics for Product Teams",
             "Python for Data Analysis", "Spreadsheet Automation", "Forecasting Basics", "A/B Testing in Practice"],
    "programming": ["Python Fundamentals", "Testing Your Code", "Web APIs with Python", "Git for Teams",
                    "Cloud Basics", "Docker from Scratch", "TypeScript Essentials", "Clean Code Reviews"],
    "design": ["UX Research Methods", "Design Systems", "Accessible Interfaces", "Figma for Product Teams"],
    "business": ["Project Management", "Negotiation Skills", "Product Strategy", "Financial Modeling",
                 "Writing for Work", "Leading Remote Teams"],
    "languages": ["Business English", "Spanish for Work", "German for Beginners", "Japanese for Travel"],
}
EVENT_TYPES = [("page_view", 50), ("video_play", 30), ("quiz_submit", 12), ("download", 5), ("certificate_view", 3)]
USER_AGENTS = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6)",
               "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X)", "Mozilla/5.0 (Linux; Android 15)",
               "LearningApp/4.2 (Android)", "LearningApp/4.2 (iOS)"]

rng = random.Random(SEED)
fakers: dict[str, Faker] = {}


def faker(locale: str) -> Faker:
    if locale not in fakers:
        fakers[locale] = Faker(locale)
        fakers[locale].seed_instance(SEED + len(fakers))
    return fakers[locale]


def moment(low: datetime, high: datetime) -> datetime:
    """A time between low and high, to the second."""
    return low + timedelta(seconds=rng.randint(0, max(0, int((high - low).total_seconds()))))


def one_line(address: str) -> str:
    """Some locales write a street address on two lines; in a table cell or a sentence it's one."""
    return ", ".join(part.strip() for part in address.splitlines())


TITLES = {"Mr", "Mrs", "Ms", "Miss", "Mx", "Dr", "Dra", "Prof", "Herr", "Frau", "Sr", "Sra", "Srta", "Srita", "Ing", "Lic",
          "Pan", "Pani", "M", "Mme", "Mlle"}
DEGREES = {"Jr", "Sr", "II", "III", "IV", "MD", "DDS", "DVM", "PhD", "MBA", "BSc", "MSc"}


def bare(name: str) -> str:
    """A name as a person gives it on sign-up: without the titles and degrees Faker adds ("Herr", "Prof.", "B.Sc.")."""
    words = name.split()
    while len(words) > 2 and (words[0].rstrip(".") in TITLES or words[0].endswith(".")):
        words.pop(0)
    while len(words) > 2 and (words[-1].rstrip(".") in DEGREES or "." in words[-1].rstrip(".")):
        words.pop()
    return " ".join(words)


def reserved(domain: str, kind: str) -> str:
    """A domain that reaches no one (RFC 2606): a company's under example.com, a mail provider's one of three names."""
    label = domain.split(".")[0]
    if kind == "company":
        return f"{label}.example.com"
    return ["mail.example.net", "post.example.org", "inbox.example.com"][len(label) % 3]


def iban(value: str) -> str:
    """Faker's IBAN, made valid where its default bank format is a digit short for the UK (22 characters, ISO 13616)."""
    if value.startswith("GB") and len(value) == 21:
        bban = value[4:8] + "0" + value[8:]
        digits = "".join(str(int(ch, 36)) for ch in bban + "GB00")
        value = f"GB{98 - int(digits) % 97:02d}{bban}"
    return value


DOCUMENTATION_NETS = ["192.0.2", "198.51.100", "203.0.113"]


def documentation_ip(address: str) -> str:
    """A public-looking IPv4 address moved into the ranges reserved for documentation (RFC 5737)."""
    a, b, c, d = map(int, address.split("."))
    return f"{DOCUMENTATION_NETS[(a * 65_536 + b * 256 + c) % 3]}.{d % 254 + 1}"


def ascii_words(name: str) -> list[str]:
    return [p for p in "".join(c if c.isalnum() or c == " " else " " for c in name).lower().split() if p.isascii()]


def email_for(drawn: str, f: Faker, index: int) -> str:
    """Email addresses as people type them: from their name, mostly lowercase, on common or company domains. Takes the
    name as Faker drew it: whether to draw a random number depends on it, so the sequence doesn't change with `bare`."""
    both = len(ascii_words(drawn)) >= 2 and rng.random() < 0.6
    parts = ascii_words(bare(drawn))
    local = ".".join(parts[:2]) if both and len(parts) >= 2 else (parts[0] if parts else "learner") + str(index % 1000)
    domain = rng.choice(["example.com", "example.net", "example.org", "mail.example.com",
                         reserved(f.free_email_domain(), "provider"), reserved(f.domain_name(), "company")])
    address = f"{local}@{domain}"
    return address.upper() if rng.random() < 0.02 else address


@dataclass
class Text:
    """Free text built from fragments, remembering where each piece of personal data was placed."""
    parts: list[str] = field(default_factory=list)
    spans: list[tuple[int, int, str, str]] = field(default_factory=list)
    length: int = 0

    def add(self, text: str) -> None:
        self.parts.append(text)
        self.length += len(text)

    def personal(self, value: str, entity: str) -> None:
        self.spans.append((self.length, self.length + len(value), entity, value))
        self.add(value)

    def write(self, template: str, values: dict[str, tuple[str, str | None]]) -> None:
        """Fill a template whose {slots} name values: (text, entity), with entity None for text that isn't personal."""
        rest = template
        while "{" in rest:
            before, slot_and_after = rest.split("{", 1)
            slot, rest = slot_and_after.split("}", 1)
            self.add(before)
            value, entity = values[slot]
            self.personal(value, entity) if entity else self.add(value)
        self.add(rest)

    @property
    def value(self) -> str:
        return "".join(self.parts)


def table(rows: list[dict], name: str, schema: pa.Schema) -> int:
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), OUT / f"{name}.parquet", compression="zstd")
    return len(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    counts = {}
    ts = pa.timestamp("s", tz="UTC")

    # learners
    weights = [c[2] for c in COUNTRIES]
    learners = []
    signups = sorted(moment(START, END - timedelta(days=30)) for _ in range(SIZES["learners"]))
    for i, signup in enumerate(signups, start=1):
        country, locale, _, _, region = rng.choices(COUNTRIES, weights)[0]
        f = faker(locale)
        drawn = f.name()
        name = bare(drawn)
        closed = rng.random() < 0.08
        asked = signup >= CONSENT_ASKED_FROM
        notes = None
        if rng.random() < 0.1:
            notes = rng.choice([
                "Prefers email over phone.", "VIP: bought a team licence.", "Asked about invoices for their employer.",
                f"Called about a refund, call back on {f.phone_number()}.", f"Also uses {email_for(f.name(), f, i + 7)} for work.",
                f"Moved to {f.city()}, update the billing address.", "Requested a certificate in German.",
            ])
        learners.append({
            "learner_id": i, "full_name": name, "email": email_for(drawn, f, i), "phone": f.phone_number(),
            "country": country, "city": f.city(), "street_address": one_line(f.street_address()),
            "date_of_birth": f.date_of_birth(minimum_age=18, maximum_age=70), "signup_at": signup,
            "marketing_consent": rng.random() < 0.42, "ai_training_consent": (rng.random() < 0.31) if asked else None,
            "status": "closed" if closed else "active", "closed_at": moment(signup + timedelta(days=30), END) if closed else None,
            "support_region": region, "notes": notes,
        })
    counts["learners"] = table(learners, "learners", pa.schema([
        ("learner_id", pa.int64()), ("full_name", pa.string()), ("email", pa.string()), ("phone", pa.string()),
        ("country", pa.string()), ("city", pa.string()), ("street_address", pa.string()), ("date_of_birth", pa.date32()),
        ("signup_at", ts), ("marketing_consent", pa.bool_()), ("ai_training_consent", pa.bool_()), ("status", pa.string()),
        ("closed_at", ts), ("support_region", pa.string()), ("notes", pa.string())]))

    # instructors and courses
    instructors = []
    for i in range(1, SIZES["instructors"] + 1):
        country, locale, *_ = rng.choices([c for c in COUNTRIES if c[1] not in ("ja_JP", "en_IN", "es_MX")], k=1)[0]
        f = faker(locale)
        drawn = f.name()
        name = bare(drawn)
        instructors.append({"instructor_id": i, "full_name": name, "email": email_for(drawn, f, 500 + i), "country": country,
                            "payout_iban": iban(f.iban()), "joined_at": moment(START - timedelta(days=400), START)})
    counts["instructors"] = table(instructors, "instructors", pa.schema([
        ("instructor_id", pa.int64()), ("full_name", pa.string()), ("email", pa.string()), ("country", pa.string()),
        ("payout_iban", pa.string()), ("joined_at", ts)]))

    courses = []
    titles = [(category, title) for category, names in CATEGORIES.items() for title in names]
    for i in range(1, SIZES["courses"] + 1):
        category, base = titles[(i - 1) % len(titles)]
        level = ["Introduction to", "Intermediate", "Advanced", "Hands-on"][(i - 1) // len(titles) % 4]
        title = f"{level} {base}" if level != "Intermediate" else f"{base}, Intermediate"
        price = rng.choice([0, 19, 29, 49, 79, 99, 149]) if category != "languages" else rng.choice([0, 29, 49])
        courses.append({"course_id": i, "title": title, "category": category, "instructor_id": rng.randint(1, SIZES["instructors"]),
                        "price_usd": float(price), "language": "en" if rng.random() < 0.85 else rng.choice(["es", "de", "fr", "pt"]),
                        "published_at": moment(START - timedelta(days=200), END - timedelta(days=90))})
    counts["courses"] = table(courses, "courses", pa.schema([
        ("course_id", pa.int64()), ("title", pa.string()), ("category", pa.string()), ("instructor_id", pa.int64()),
        ("price_usd", pa.float64()), ("language", pa.string()), ("published_at", ts)]))

    # enrollments and payments: popular courses get more learners
    popularity = [rng.paretovariate(1.3) for _ in courses]
    currency = {c[0]: c[3] for c in COUNTRIES}
    enrollments, payments = [], []
    for i in range(1, SIZES["enrollments"] + 1):
        learner = learners[rng.randrange(len(learners))]
        course = rng.choices(courses, popularity)[0]
        start = max(learner["signup_at"], course["published_at"])
        enrolled = moment(start, END)
        progress = min(100, int(rng.betavariate(0.8, 0.9) * 110))
        completed = enrolled + timedelta(days=rng.randint(3, 120)) if progress == 100 else None
        enrollments.append({"enrollment_id": i, "learner_id": learner["learner_id"], "course_id": course["course_id"],
                            "enrolled_at": enrolled, "progress_pct": progress, "completed_at": completed if completed and completed <= END else None})
        if course["price_usd"] > 0:
            f = faker("en_US")
            payments.append({"payment_id": len(payments) + 1, "enrollment_id": i, "learner_id": learner["learner_id"],
                             "amount": course["price_usd"], "currency": currency[learner["country"]], "paid_at": enrolled,
                             "card_brand": rng.choice(["visa", "mastercard", "amex"]),
                             "card_number": f.credit_card_number(card_type=None) if rng.random() < 0.97 else None,
                             "status": rng.choices(["succeeded", "refunded", "failed"], [92, 5, 3])[0]})
    counts["enrollments"] = table(enrollments, "enrollments", pa.schema([
        ("enrollment_id", pa.int64()), ("learner_id", pa.int64()), ("course_id", pa.int64()), ("enrolled_at", ts),
        ("progress_pct", pa.int32()), ("completed_at", ts)]))
    counts["payments"] = table(payments, "payments", pa.schema([
        ("payment_id", pa.int64()), ("enrollment_id", pa.int64()), ("learner_id", pa.int64()), ("amount", pa.float64()),
        ("currency", pa.string()), ("paid_at", ts), ("card_brand", pa.string()), ("card_number", pa.string()), ("status", pa.string())]))

    # events, within enrollments
    events = []
    kinds, kind_weights = zip(*EVENT_TYPES)
    for i in range(1, SIZES["events"] + 1):
        e = enrollments[rng.randrange(len(enrollments))]
        events.append({"event_id": i, "learner_id": e["learner_id"], "course_id": e["course_id"],
                       "event_type": rng.choices(kinds, kind_weights)[0], "occurred_at": moment(e["enrolled_at"], min(END, e["enrolled_at"] + timedelta(days=150))),
                       "ip_address": documentation_ip(faker("en_US").ipv4_public()), "user_agent": rng.choice(USER_AGENTS)})
    events.sort(key=lambda r: (r["occurred_at"], r["event_id"]))
    counts["events"] = table(events, "events", pa.schema([
        ("event_id", pa.int64()), ("learner_id", pa.int64()), ("course_id", pa.int64()), ("event_type", pa.string()),
        ("occurred_at", ts), ("ip_address", pa.string()), ("user_agent", pa.string())]))

    # support tickets and reviews: free text, with every piece of personal data labelled
    spans = []
    agents = {"americas": ["A01", "A02", "A03", "A04"], "emea": ["A05", "A06", "A07", "A08"], "apac": ["A09", "A10", "A11"]}
    locale_of = {c[0]: c[1] for c in COUNTRIES}
    opening = ["Hi,", "Hello,", "Hi team,", "Good morning,", ""]
    problems = [
        "I can't open the videos in {course}, they stop after a few seconds.",
        "Quiz 2 in {course} marks a correct answer as wrong.",
        "I was charged twice for {course}.",
        "I finished {course} but there's no certificate on my profile.",
        "The download link for the slides in {course} is broken.",
        "Can I switch from {course} to another course?",
    ]
    personal = [
        ("My account email is {email}.", ["email"]),
        ("Please call me back on {phone}.", ["phone"]),
        ("My name is {name}.", ["name"]),
        ("The card I used was {card}.", ["card"]),
        ("Please refund to my account {iban}.", ["iban"]),
        ("I've moved to {address}, please update my details.", ["address"]),
        ("My colleague {name2} ({email2}) wants to enrol as well.", ["name2", "email2"]),
        ("My date of birth on the account is wrong, it should be {dob}.", ["dob"]),
        ("Order 4402 1189 was the one that failed.", []),
        ("It happened on 12/03 around 9pm.", []),
        # harder cases: what detectors miss, and what they mistake for personal data
        ("this is {lower_name}, i still can't log in.", ["lower_name"]),
        ("My manager {word_name} approved the team licence.", ["word_name"]),
        ("You can reach me on {digits_phone} after 6pm.", ["digits_phone"]),
        ("The invoice number is {invoice}.", []),
    ]
    word_names = ["Will", "Grace", "Hope", "Rose", "Bill", "Faith", "Mark", "Summer", "Joy", "Dawn"]
    tickets = []
    for i in range(1, SIZES["tickets"] + 1):
        learner = learners[rng.randrange(len(learners))]
        f = faker(locale_of[learner["country"]])
        course = rng.choice(courses)["title"]
        drawn = f.name()
        other = bare(drawn)
        values = {"course": (course, None), "email": (learner["email"], "EMAIL_ADDRESS"), "phone": (learner["phone"], "PHONE_NUMBER"),
                  "name": (learner["full_name"], "PERSON"), "card": (faker("en_US").credit_card_number(), "CREDIT_CARD"),
                  "iban": (iban(f.iban()), "IBAN_CODE"),
                  "address": (f"{one_line(f.street_address())}, {f.city()}", "LOCATION"), "name2": (other, "PERSON"),
                  "email2": (email_for(drawn, f, i), "EMAIL_ADDRESS"), "dob": (f.date_of_birth(minimum_age=18, maximum_age=70).isoformat(), "DATE_TIME"),
                  "lower_name": (learner["full_name"].lower(), "PERSON"),
                  "word_name": (rng.choice(word_names), "PERSON"),
                  "digits_phone": ("".join(ch for ch in learner["phone"].split("x")[0] if ch.isdigit()), "PHONE_NUMBER"),
                  "invoice": (str(rng.randint(10**10, 10**11 - 1)), None)}
        text = Text()
        problem = rng.choice(problems)
        if greeting := rng.choice(opening):
            text.add(greeting + " ")
            problem = problem if problem.startswith("I ") else problem[0].lower() + problem[1:]   # "Hi, the download link..."
        text.write(problem, values)
        for template, _ in rng.sample(personal, rng.choice([0, 1, 1, 2, 2, 3])):
            text.add(" ")
            text.write(template, values)
        signature = rng.random()
        if signature < 0.3:
            text.add(" Thanks, ")
            text.personal(learner["full_name"].split()[0], "PERSON")
        elif signature < 0.38:
            text.add(" Regards, ")
            text.personal(f"{learner['full_name'][0]}. {learner['full_name'].split()[-1]}", "PERSON")
        opened = moment(learner["signup_at"], END)
        tickets.append({"ticket_id": i, "learner_id": learner["learner_id"], "opened_at": opened,
                        "channel": rng.choices(["email", "chat", "phone"], [55, 35, 10])[0], "support_region": learner["support_region"],
                        "agent_id": rng.choice(agents[learner["support_region"]]), "body": text.value,
                        "status": rng.choices(["closed", "open", "pending"], [80, 12, 8])[0]})
        spans += [{"source": "tickets", "row_id": i, "start": s, "end": e, "entity": ent, "text": v} for s, e, ent, v in text.spans]
    counts["tickets"] = table(tickets, "tickets", pa.schema([
        ("ticket_id", pa.int64()), ("learner_id", pa.int64()), ("opened_at", ts), ("channel", pa.string()),
        ("support_region", pa.string()), ("agent_id", pa.string()), ("body", pa.string()), ("status", pa.string())]))

    praise = ["Clear explanations and good exercises.", "The pace was right for me.", "Too fast in the second half.",
              "Exactly what I needed for my job.", "Some videos are out of date.", "The quizzes were the best part."]
    reviews = []
    instructor_name = {c["course_id"]: instructors[c["instructor_id"] - 1]["full_name"] for c in courses}
    for i in range(1, SIZES["reviews"] + 1):
        e = enrollments[rng.randrange(len(enrollments))]
        learner = learners[e["learner_id"] - 1]
        rating = rng.choices([1, 2, 3, 4, 5], [4, 6, 15, 35, 40])[0]
        text = Text()
        text.add(rng.choice(praise))
        if rng.random() < 0.25:
            text.add(" ")
            text.write("{teacher} explains things well.", {"teacher": (instructor_name[e["course_id"]].split()[0], "PERSON")})
        if rng.random() < 0.04:
            text.add(" ")
            text.write("Happy to talk about it, write to me at {email}.", {"email": (learner["email"], "EMAIL_ADDRESS")})
        reviews.append({"review_id": i, "learner_id": learner["learner_id"], "course_id": e["course_id"], "rating": rating,
                        "body": text.value, "posted_at": moment(e["enrolled_at"], END)})
        spans += [{"source": "reviews", "row_id": i, "start": s, "end": e2, "entity": ent, "text": v} for s, e2, ent, v in text.spans]
    counts["reviews"] = table(reviews, "reviews", pa.schema([
        ("review_id", pa.int64()), ("learner_id", pa.int64()), ("course_id", pa.int64()), ("rating", pa.int32()),
        ("body", pa.string()), ("posted_at", ts)]))
    counts["pii_spans"] = table(spans, "pii_spans", pa.schema([
        ("source", pa.string()), ("row_id", pa.int64()), ("start", pa.int32()), ("end", pa.int32()), ("entity", pa.string()), ("text", pa.string())]))

    for name, n in counts.items():
        print(f"{name}: {n:,} rows")


if __name__ == "__main__":
    main()
