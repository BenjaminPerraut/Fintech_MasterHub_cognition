"""48 fictional KYC cases with realistic signal mixes and a few in-flight four-eyes decisions."""
from __future__ import annotations

import random
from datetime import date, timedelta

from ..engine import service
from ..engine.manifest import Manifest
from .actors import ALICE, KARIM, OLIVIA

PEOPLE = [
    ("Lucía Fernández García", "ES"), ("Javier Moreno Ruiz", "ES"), ("Carmen Navarro Gil", "ES"), ("Pablo Ortega Sanz", "ES"),
    ("Camille Dubois", "FR"), ("Hugo Lefebvre", "FR"), ("Léa Moreau", "FR"), ("Lukas Schmidt", "DE"), ("Hannah Weber", "DE"),
    ("Felix Wagner", "DE"), ("Oliver Bennett", "GB"), ("Amelia Clarke", "GB"), ("George Patel", "GB"), ("Emily Johnson", "US"),
    ("Michael Rodriguez", "US"), ("Sophia Nguyen", "US"), ("Daniel Kim", "US"), ("Giulia Romano", "IT"), ("Marco Bianchi", "IT"),
    ("Inês Carvalho", "PT"), ("Tiago Almeida", "PT"), ("Sanne de Vries", "NL"), ("Daan Jansen", "NL"), ("Chinedu Okeke", "NG"),
    ("Ngozi Adeyemi", "NG"), ("Rafael Souza", "BR"), ("Beatriz Lima", "BR"), ("Omar Al-Mansouri", "AE"), ("Layla Haddad", "AE"),
    ("Dmitri Volkov", "RU"), ("Anastasia Petrova", "RU"), ("Elena Ruiz Castillo", "ES"), ("Thomas Muller", "DE"),
    ("Chloé Martin", "FR"), ("Harry Thompson", "GB"), ("Ava Williams", "US"), ("Matteo Ricci", "IT"), ("Ana Sousa", "PT"),
    ("Femi Balogun", "NG"), ("Yusuf Rahman", "AE"),
]
BUSINESSES = [("Atlas Freight Logistics SL", "ES"), ("Brightwave Media GmbH", "DE"), ("Kensington Trade Partners Ltd", "GB"),
              ("Lumen Crypto OTC Desk FZE", "AE"), ("Porto Atlântico Imports Lda", "PT"), ("Delta Ventures LLC", "US"),
              ("Volga Commodities OOO", "RU"), ("Seine Luxury Goods SAS", "FR")]
COMPANIES = ["Iberdrola", "Airbus", "Siemens", "HSBC", "Accenture", "Self-employed", "Deloitte", "Telefónica", "Unilever",
             "BNP Paribas", "Freelance designer", "Retired", "University of Lisbon", "Shopify", "Stripe", "NHS"]
OCCUPATIONS = ["Software engineer", "Nurse", "Sales manager", "Consultant", "Teacher", "Entrepreneur", "Accountant",
               "Architect", "Student", "Retired", "Trader", "Doctor"]
HIGH_RISK = {"RU", "NG", "AE"}


def _risk(d: dict) -> int:
    s = 5
    s += {"potential_match": 35, "confirmed_match": 60}.get(d["sanctions_screening"], 0)
    s += 20 if d["pep"] == "yes" else 0
    s += {"potential": 12, "confirmed": 30}.get(d["adverse_media"], 0)
    s += {"mismatch": 25, "expired": 10, "pending": 5}.get(d["id_document_check"], 0)
    s += 15 if d["selfie_match_score"] < 80 else 0
    s += {"fail": 25, "inconclusive": 10}.get(d["liveness_check"], 0)
    s += {"missing": 20, "partial": 10}.get(d["source_of_funds_evidence"], 0)
    s += {"missing": 10, "mismatch": 15, "older_than_3_months": 5}.get(d["proof_of_address"], 0)
    s += 10 if d["source_of_funds"] == "crypto_assets" else 0
    s += 10 if d["nationality"] in HIGH_RISK else 0
    s += 10 if d["expected_monthly_volume"] > 50000 else 0
    return min(s, 100)


def _doc_number(rng: random.Random, kind: str, nat: str) -> str:
    if kind == "national_id_dni" and nat == "ES":
        n = rng.randint(10000000, 99999999)
        return f"{n}{'TRWAGMYFPDXBNJZSQVHLCKE'[n % 23]}"
    return f"{nat}{rng.randint(1000000, 9999999)}{rng.choice('ABCDEFGHJK')}"


def make_case(rng: random.Random, name: str, nat: str, business: bool, today: date) -> dict:
    doc = rng.choice(["passport", "national_id_dni"] if nat in ("ES", "FR", "DE", "IT", "PT", "NL") else ["passport", "residence_permit"])
    sof = rng.choices(["salary", "business_income", "investments", "inheritance", "savings", "crypto_assets", "property_sale", "other"],
                      weights=[40, 15, 12, 5, 12, 7, 6, 3])[0]
    if business:
        sof = rng.choice(["business_income", "investments"])
    d = {
        "customer_name": name, "customer_type": "business" if business else "individual", "nationality": nat,
        "date_of_birth": (today - timedelta(days=rng.randint(19 * 365, 75 * 365))).isoformat(),
        "email": name.lower().replace(" ", ".").replace("é", "e").replace("í", "i").replace("ú", "u")
                 .replace("ç", "c").replace("ê", "e").replace("â", "a").replace("ü", "u")[:28] + "@mail.example",
        "phone": f"+{rng.randint(30, 49)} {rng.randint(600000000, 799999999)}",
        "id_document_type": doc, "id_document_number": _doc_number(rng, doc, nat),
        "id_document_expiry": (today + timedelta(days=rng.randint(-200, 3000))).isoformat(),
        "id_document_check": rng.choices(["verified", "expired", "mismatch", "pending"], weights=[70, 10, 8, 12])[0],
        "ssn": f"{rng.randint(100, 899)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}" if nat == "US"
               else f"{nat}-TIN-{rng.randint(10000000, 99999999)}",
        "proof_of_address": rng.choices(["verified", "older_than_3_months", "mismatch", "missing"], weights=[70, 12, 8, 10])[0],
        "address": f"{rng.randint(1, 220)} {rng.choice(['Calle Mayor', 'Rue de Rivoli', 'Hauptstraße', 'High Street', 'Main St', 'Via Roma', 'Rua Augusta'])}, {nat}",
        "selfie_match_score": rng.choices([rng.randint(88, 99), rng.randint(70, 87), rng.randint(35, 69)], weights=[75, 18, 7])[0],
        "liveness_check": rng.choices(["pass", "fail", "inconclusive"], weights=[85, 5, 10])[0],
        "company": name if business else rng.choice(COMPANIES),
        "occupation": "Corporate account" if business else rng.choice(OCCUPATIONS),
        "salary_range": rng.choices(["<25k", "25k-50k", "50k-100k", "100k-250k", ">250k"], weights=[15, 35, 30, 14, 6])[0],
        "source_of_funds": sof,
        "source_of_funds_evidence": rng.choices(["provided", "partial", "missing"], weights=[70, 18, 12])[0],
        "expected_monthly_volume": float(rng.choice([800, 1500, 3000, 5000, 12000, 25000, 60000, 150000] if business
                                                    else [500, 900, 1500, 2500, 4000, 8000, 20000, 75000])),
        "sanctions_screening": rng.choices(["clear", "potential_match", "confirmed_match"], weights=[85, 12, 3])[0],
        "pep": rng.choices(["no", "yes"], weights=[92, 8])[0],
        "adverse_media": rng.choices(["none", "potential", "confirmed"], weights=[85, 11, 4])[0],
    }
    d["risk_score"] = _risk(d)
    d["risk_level"] = "high" if d["risk_score"] >= 70 else "medium" if d["risk_score"] >= 40 else "low"
    return d


def seed(conn, m: Manifest) -> None:
    rng = random.Random(20261022)
    today = date.today()
    roster = [(n, c, False) for n, c in PEOPLE] + [(n, c, True) for n, c in BUSINESSES]
    rng.shuffle(roster)
    plan = (["new"] * 14 + ["in_review"] * 8 + ["pending_approve"] * 3 + ["pending_reject"] * 1 + ["escalated"] * 3
            + ["info_requested"] * 3 + ["approved_active"] * 8 + ["approved_closed"] * 5 + ["rejected"] * 3)
    for (name, nat, business), target in zip(roster, plan):
        d = make_case(rng, name, nat, business, today)
        if target.startswith(("approved", "rejected")):
            submitted = today - timedelta(days=rng.randint(200, 2900))
            d.update(sanctions_screening="clear", id_document_check="verified", liveness_check="pass")
            if target == "rejected":
                d.update(id_document_check="mismatch", selfie_match_score=rng.randint(30, 60))
            d["risk_score"] = _risk(d)
            d["risk_level"] = "high" if d["risk_score"] >= 70 else "medium" if d["risk_score"] >= 40 else "low"
        else:
            submitted = today - timedelta(days=rng.randint(0, 25))
        d["submitted_at"] = submitted.isoformat()
        if target in ("approved_closed", "rejected"):
            end = submitted + timedelta(days=rng.randint(30, 900))
            end = min(end, today - timedelta(days=5))
            # Some relationships ended > 5y ago so their retention has lapsed (purge due).
            if target == "approved_closed" and rng.random() < 0.6:
                end = today - timedelta(days=rng.randint(5 * 365 + 10, 5 * 365 + 400))
            if end <= submitted:
                submitted = end - timedelta(days=rng.randint(100, 600))
                d["submitted_at"] = submitted.isoformat()
            d.update(relationship_status="closed", relationship_end=end.isoformat(),
                     retention_until=(end + timedelta(days=5 * 365 + 1)).isoformat())
        elif target == "approved_active":
            d["relationship_status"] = "active"
        else:
            d["relationship_status"] = "onboarding"

        item = service.create(conn, m, service.SYSTEM, d, created_at=f"{d['submitted_at']}T09:00:00+00:00",
                              enforce_roles=False)
        if target == "new":
            continue
        analyst = ALICE if rng.random() < 0.6 else KARIM
        other = KARIM if analyst is ALICE else ALICE
        service.claim(conn, m, analyst, item.id)
        service.transition(conn, m, analyst, item.id, "start_review")
        if target == "in_review":
            if rng.random() < 0.5:
                service.comment(conn, m, analyst, item.id, "Documents look consistent; checking source of funds next.")
        elif target == "info_requested":
            service.transition(conn, m, analyst, item.id, "request_info",
                               "Proof of address older than 3 months. Requested a recent utility bill.")
        elif target == "escalated":
            service.transition(conn, m, analyst, item.id, "escalate",
                               "Potential sanctions name match and high expected volume. Needs Ops Lead review.")
        elif target == "pending_approve":
            service.transition(conn, m, analyst, item.id, "approve",
                               "All checks verified, SoF evidence provided (payslips). Recommend approval.")
        elif target == "pending_reject":
            service.transition(conn, m, analyst, item.id, "reject",
                               "Selfie match below threshold and document mismatch. Recommend offboarding.")
        else:
            t = "reject" if target == "rejected" else "approve"
            service.transition(conn, m, analyst, item.id, t, "Checks complete. Recommendation attached.")
            from ..engine import store
            dec = store.open_decision(conn, item.id)
            approver = OLIVIA if t == "reject" else other
            service.approve(conn, m, approver, item.id, dec["id"], "Reviewed evidence independently. Agree.")
