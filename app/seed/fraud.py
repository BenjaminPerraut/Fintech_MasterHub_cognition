"""Fictional payments/transfers ledger with injected fraud patterns, then a rule scan and some triage in flight."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from ..db import transaction
from ..engine import service, store
from ..rules import engine
from .actors import ALICE, KARIM, OLIVIA, OMAR
from .kyc import PEOPLE

MERCHANTS = ["Mercadona", "Carrefour", "Lidl", "Uber", "Glovo", "Netflix", "Spotify", "Zara", "Amazon EU", "Shell",
             "Renfe", "Starbucks", "IKEA", "Decathlon", "Booking.com"]
EU = ["ES", "FR", "DE", "IT", "PT", "NL", "IE", "BE"]


def _iban(rng, cc):
    return f"{cc}{rng.randint(10, 99)} {rng.randint(1000, 9999)} {rng.randint(1000, 9999)} {rng.randint(1000, 9999)}"


def seed(conn, manifests: dict) -> None:
    rng = random.Random(20261024)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    customers = [(f"C-{2000 + i}", name) for i, (name, _) in enumerate(PEOPLE[:30])]

    def at(days_ago_max: int) -> datetime:
        return now - timedelta(seconds=rng.randint(3600, days_ago_max * 86400))

    with transaction(conn):
        for cid, name in customers:
            for _ in range(rng.randint(8, 20)):
                engine.record_payment(conn, customer_id=cid, customer_name=name, amount=min(rng.lognormvariate(3.3, 0.8), 600),
                                      merchant=rng.choice(MERCHANTS), country=rng.choice(EU), ts=at(14).isoformat())
            payees = [(f"{rng.choice(['Landlord', 'Savings', 'Family', 'Gym', 'Utility'])} {cid[-2:]}{k}", rng.choice(EU)) for k in range(3)]
            for _ in range(rng.randint(2, 6)):
                payee, cc = rng.choice(payees)
                engine.record_transfer(conn, customer_id=cid, customer_name=name, amount=rng.uniform(50, 2500),
                                       beneficiary_name=payee, beneficiary_iban=_iban(rng, cc), beneficiary_country=cc,
                                       is_new=False, ts=at(30).isoformat())
        picks = rng.sample(customers, 24)
        # Payment velocity: 3-5 card payments within ~60s (card testing / account takeover).
        for cid, name in picks[:6]:
            t, merchant = at(10), rng.choice(["Steam", "GiftCardz Online", "CryptoSwap XY", "Apple Store"])
            for _ in range(rng.randint(3, 5)):
                engine.record_payment(conn, customer_id=cid, customer_name=name, amount=rng.uniform(25, 400),
                                      merchant=merchant, country="LT", ts=t.isoformat())
                t += timedelta(seconds=rng.randint(4, 18))
        # Very high payments, absolute and relative to the customer's own history.
        for cid, name in picks[6:9]:
            engine.record_payment(conn, customer_id=cid, customer_name=name, amount=rng.uniform(5200, 14000),
                                  merchant=rng.choice(["Rolex Boutique", "Binance", "Emirates"]), country="CH", ts=at(10).isoformat())
        for cid, name in picks[9:11]:
            engine.record_payment(conn, customer_id=cid, customer_name=name, amount=rng.uniform(1500, 3200),
                                  merchant="Electro Outlet Online", country="PL", ts=at(10).isoformat())
        # Transfer bursts to new payees (mule pattern).
        for cid, name in picks[11:15]:
            t = at(10)
            for k in range(rng.randint(3, 5)):
                engine.record_transfer(conn, customer_id=cid, customer_name=name, amount=rng.uniform(400, 1900),
                                       beneficiary_name=f"New payee {k + 1}", beneficiary_iban=_iban(rng, "LT"),
                                       beneficiary_country="LT", is_new=True, ts=t.isoformat())
                t += timedelta(seconds=rng.randint(30, 140))
        for cid, name in picks[15:18]:
            engine.record_transfer(conn, customer_id=cid, customer_name=name, amount=rng.uniform(12000, 48000),
                                   beneficiary_name="Prime Property Escrow SL", beneficiary_iban=_iban(rng, "ES"),
                                   beneficiary_country="ES", is_new=rng.random() < 0.5, ts=at(10).isoformat())
        for (cid, name), cc in zip(picks[18:21], ["AE", "RU", "NG"]):
            engine.record_transfer(conn, customer_id=cid, customer_name=name, amount=rng.uniform(3500, 9000),
                                   beneficiary_name=f"Offshore Holdings {cc}", beneficiary_iban=_iban(rng, cc),
                                   beneficiary_country=cc, is_new=True, ts=at(10).isoformat())

    engine.scan(conn, manifests, service.SYSTEM)

    fraud, transfers = manifests["fraud"], manifests["transfers"]
    f_ids = [r["id"] for r in conn.execute("SELECT id FROM items WHERE app='fraud' ORDER BY priority DESC, id")]
    t_ids = [r["id"] for r in conn.execute("SELECT id FROM items WHERE app='transfers' ORDER BY priority DESC, id")]

    def work(m, who, item_id):
        service.claim(conn, m, who, item_id)
        service.transition(conn, m, who, item_id, "investigate")

    work(fraud, KARIM, f_ids[0])
    service.transition(conn, fraud, KARIM, f_ids[0], "confirm_fraud", "Card-testing pattern; customer confirmed card stolen.")
    service.approve(conn, fraud, OMAR, f_ids[0], store.open_decision(conn, f_ids[0])["id"], "Agree. Card blocked, account frozen.")
    work(fraud, OLIVIA, f_ids[1])
    service.comment(conn, fraud, OLIVIA, f_ids[1], "Called customer, no answer. Card temporarily blocked via card ops.")
    work(fraud, ALICE, f_ids[-1])
    service.transition(conn, fraud, ALICE, f_ids[-1], "mark_false_positive", "Customer confirmed: bought several concert tickets back to back.")

    work(transfers, OMAR, t_ids[0])
    service.transition(conn, transfers, OMAR, t_ids[0], "hold", "Large first-time payment abroad. Holding pending source-of-funds evidence.")
    work(transfers, KARIM, t_ids[1])
    service.transition(conn, transfers, KARIM, t_ids[1], "block", "Mule pattern: several new payees in minutes, funds received same day.")
    work(transfers, ALICE, t_ids[-1])
    service.transition(conn, transfers, ALICE, t_ids[-1], "release", "Payee is the customer's known notary; contract on file.")
    service.approve(conn, transfers, OLIVIA, t_ids[-1], store.open_decision(conn, t_ids[-1])["id"], "Checked contract. Release.")
