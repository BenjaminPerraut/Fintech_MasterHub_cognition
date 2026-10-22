"""63 fictional refund requests across the lifecycle, incl. high-value ones awaiting an Ops Lead approval."""
from __future__ import annotations

import random
import unicodedata
from datetime import date, timedelta

from ..engine import service, store
from ..engine.manifest import Manifest
from .actors import OLIVIA, OMAR, SAM
from .kyc import PEOPLE

MERCHANTS = ["Amazon EU", "Zalando", "Ryanair", "Booking.com", "Apple Store", "Uber", "Netflix", "MediaMarkt", "IKEA",
             "Glovo", "Spotify", "Steam", "Decathlon", "El Corte Inglés", "Airbnb", "Vueling"]
REASONS = ["duplicate_charge", "item_not_received", "unauthorized_transaction", "service_not_as_described",
           "subscription_cancelled", "wrong_amount", "goodwill"]
STATEMENTS = {
    "duplicate_charge": "I was charged twice for the same order.",
    "item_not_received": "Order never arrived; merchant not responding.",
    "unauthorized_transaction": "I did not make this payment. My card was in my wallet.",
    "service_not_as_described": "The service was not what was advertised.",
    "subscription_cancelled": "I cancelled the subscription but was still billed.",
    "wrong_amount": "Charged more than the price shown at checkout.",
    "goodwill": "Long-time customer asking for a goodwill gesture after an outage.",
}


def _slug(name: str) -> str:
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().replace(" ", ".")[:28]


def _risk(d: dict) -> int:
    a = d["amount"]
    s = 5 + (30 if a >= 2000 else 20 if a >= 500 else 5 if a >= 150 else 0)
    s += min(d["prior_refunds_90d"], 5) * 8
    s += 15 if d["customer_tenure_months"] < 3 else 0
    s += 15 if d["reason"] == "unauthorized_transaction" else 0
    s += {"repeat_refunder": 15, "chargeback_history": 20, "new_account": 10, "high_value": 5}.get(d["risk_flag"], 0)
    s += 20 if d["kyc_status"] != "verified" else 0
    return min(s, 100)


def make(rng: random.Random, today: date, amount: float | None = None) -> dict:
    name, nat = rng.choice(PEOPLE)
    if amount is None:
        lo, hi = rng.choices([(5, 120), (120, 499), (500, 1800), (1800, 6500)], weights=[45, 30, 18, 7])[0]
        amount = round(rng.uniform(lo, hi), 2)
    requested = today - timedelta(days=rng.randint(0, 21))
    prior = rng.choices([0, 1, 2, 3, 5], weights=[55, 20, 12, 8, 5])[0]
    tenure = rng.choice([1, 2, 5, 9, 14, 26, 40, 62])
    flag = ("repeat_refunder" if prior >= 3 else "new_account" if tenure < 3 else "high_value" if amount >= 1800
            else rng.choices(["none", "chargeback_history"], weights=[92, 8])[0])
    reason = rng.choices(REASONS, weights=[18, 16, 12, 14, 14, 10, 6])[0]
    d = {
        "customer_name": name, "customer_id": f"C-{rng.randint(100000, 999999)}",
        "customer_email": f"{_slug(name)}@mail.example",
        "iban": f"{nat}{rng.randint(10, 99)} {rng.randint(1000, 9999)} {rng.randint(1000, 9999)} {rng.randint(1000, 9999)}",
        "transaction_id": f"TX-{requested:%y%m%d}-{rng.randint(10000, 99999)}",
        "merchant": rng.choice(MERCHANTS), "amount": amount, "reason": reason, "notes": STATEMENTS[reason],
        "channel": rng.choice(["app", "web", "chat", "phone", "email"]),
        "payment_method": rng.choices(["card", "sepa_transfer", "instant_transfer", "wallet"], weights=[60, 15, 15, 10])[0],
        "transaction_date": (requested - timedelta(days=rng.randint(0, 45))).isoformat(),
        "requested_at": requested.isoformat(), "sla_due": (requested + timedelta(days=5)).isoformat(),
        "prior_refunds_90d": prior, "customer_tenure_months": tenure,
        "kyc_status": rng.choices(["verified", "pending", "failed"], weights=[88, 9, 3])[0], "risk_flag": flag,
    }
    d["refund_risk"] = _risk(d)
    return d


def seed(conn, m: Manifest) -> None:
    rng = random.Random(20261023)
    today = date.today()
    plan = (["requested"] * 18 + ["in_review"] * 10 + ["info_requested"] * 3 + ["escalated"] * 4
            + ["pending_approve"] * 5 + ["approved"] * 5 + ["paid"] * 12 + ["denied"] * 6)
    rng.shuffle(plan)
    for target in plan:
        d = make(rng, today, round(rng.uniform(600, 4200), 2) if target == "pending_approve" else None)
        item = service.create(conn, m, SAM, d,
                              created_at=f"{d['requested_at']}T{rng.randint(8, 18):02d}:{rng.randint(0, 59):02d}:00+00:00")
        if target == "requested":
            continue
        worker = SAM if target == "escalated" or rng.random() < 0.75 else OMAR
        service.claim(conn, m, worker, item.id)
        service.transition(conn, m, worker, item.id, "start_review")
        if target == "in_review":
            if rng.random() < 0.5:
                service.comment(conn, m, worker, item.id, "Checked ledger: charge confirmed. Waiting for merchant reply.")
        elif target == "info_requested":
            service.transition(conn, m, worker, item.id, "request_info", "Asked the customer for the order confirmation and tracking number.")
        elif target == "escalated":
            service.transition(conn, m, worker, item.id, "escalate", "Customer disputes the payment; possible account takeover. Needs Ops Lead.")
        elif target == "denied":
            service.transition(conn, m, worker, item.id, "deny", "Merchant confirmed delivery (signed tracking). Customer advised to contact merchant.")
        elif target == "pending_approve":
            service.transition(conn, m, OMAR, item.id, "approve", "Duplicate charge confirmed in ledger. Above €500, so a second Ops Lead must approve.")
        else:
            res = service.transition(conn, m, OMAR, item.id, "approve", "Verified against ledger and merchant records.")
            if res == "proposed":
                dec = store.open_decision(conn, item.id)
                service.approve(conn, m, OLIVIA, item.id, dec["id"], "Evidence reviewed independently. Approved.")
            if target == "paid":
                service.transition(conn, m, OMAR, item.id, "execute_payout")
