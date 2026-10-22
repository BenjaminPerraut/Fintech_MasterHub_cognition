"""Rule-based detection over an immutable payments/transfers ledger. Hits become queue items (fraud / transfers)."""
from __future__ import annotations

import hashlib
import random
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from ..audit import record_event
from ..db import register_schema, transaction
from ..engine import service

CONFIG_PATH = Path(__file__).with_name("rules.yaml")

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, customer_name TEXT NOT NULL, amount REAL NOT NULL,
    merchant TEXT NOT NULL, country TEXT NOT NULL, channel TEXT NOT NULL, ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_payments_cust_ts ON payments(customer_id, ts);
CREATE TRIGGER IF NOT EXISTS payments_no_update BEFORE UPDATE ON payments
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS payments_no_delete BEFORE DELETE ON payments
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TABLE IF NOT EXISTS transfers (
    id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, customer_name TEXT NOT NULL, amount REAL NOT NULL,
    beneficiary_name TEXT NOT NULL, beneficiary_iban TEXT NOT NULL, beneficiary_country TEXT NOT NULL,
    is_new_beneficiary INTEGER NOT NULL DEFAULT 0, ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_transfers_cust_ts ON transfers(customer_id, ts);
CREATE TRIGGER IF NOT EXISTS transfers_no_update BEFORE UPDATE ON transfers
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS transfers_no_delete BEFORE DELETE ON transfers
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
"""
register_schema(SCHEMA)
SIM_KINDS = {"payment_burst": "4 card payments in 24 seconds", "large_payment": "One €7,800 payment",
             "transfer_burst": "4 transfers to new payees in 45 seconds", "large_transfer": "One €25,000 transfer to a new payee (AE)"}


def config_text() -> str:
    return CONFIG_PATH.read_text()


def load_config() -> dict:
    return yaml.safe_load(config_text())


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def record_payment(conn, *, customer_id, customer_name, amount, merchant, country, ts, channel="card") -> str:
    n = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] + 1
    pid = f"PAY-{n:06d}"
    conn.execute("INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (pid, customer_id, customer_name, round(float(amount), 2), merchant, country, channel, ts))
    return pid


def record_transfer(conn, *, customer_id, customer_name, amount, beneficiary_name, beneficiary_iban,
                    beneficiary_country, is_new, ts) -> str:
    n = conn.execute("SELECT COUNT(*) FROM transfers").fetchone()[0] + 1
    tid = f"TRF-{n:06d}"
    conn.execute("INSERT INTO transfers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (tid, customer_id, customer_name, round(float(amount), 2), beneficiary_name, beneficiary_iban,
                  beneficiary_country, int(bool(is_new)), ts))
    return tid


def _by_customer(conn, table: str) -> dict[str, list[sqlite3.Row]]:
    groups: dict[str, list] = {}
    for r in conn.execute(f"SELECT * FROM {table} ORDER BY customer_id, ts, id"):
        groups.setdefault(r["customer_id"], []).append(r)
    return groups


def _clusters(rows, min_count: int, window: int):
    out, i = [], 0
    while i < len(rows):
        t0, j = _dt(rows[i]["ts"]), i
        while j + 1 < len(rows) and (_dt(rows[j + 1]["ts"]) - t0).total_seconds() <= window:
            j += 1
        if j - i + 1 >= min_count:
            out.append(rows[i:j + 1])
            i = j + 1
        else:
            i += 1
    return out


def _hit(rule, key, rows, description, score, counterparty, country, **extra) -> dict:
    return {"customer_name": rows[0]["customer_name"], "customer_id": rows[0]["customer_id"], "rule": rule,
            "rule_key": key, "description": description, "amount_total": round(sum(r["amount"] for r in rows), 2),
            "txn_count": len(rows), "window_start": rows[0]["ts"][:19].replace("T", " "),
            "window_end": rows[-1]["ts"][:19].replace("T", " "), "counterparty": counterparty, "country": country,
            "transactions": "\n".join(f"{r['id']}  {r['ts'][:19].replace('T', ' ')}  EUR {r['amount']:,.2f}" for r in rows),
            "alert_score": min(int(score), 100), **extra}


def detect_payments(conn, cfg: dict) -> list[dict]:
    v, h = cfg["payments"]["velocity"], cfg["payments"]["high_amount"]
    hits = []
    for rows in _by_customer(conn, "payments").values():
        for cl in _clusters(rows, v["min_count"], v["window_seconds"]):
            total, secs = sum(r["amount"] for r in cl), (_dt(cl[-1]["ts"]) - _dt(cl[0]["ts"])).total_seconds()
            hits.append(_hit("velocity_burst", f"P-VEL:{cl[0]['id']}", cl,
                             f"{len(cl)} payments in {secs:.0f}s (rule: >= {v['min_count']} within {v['window_seconds']}s)",
                             50 + 10 * (len(cl) - v["min_count"]) + (20 if total >= 1000 else 0),
                             ", ".join(sorted({r["merchant"] for r in cl})), cl[0]["country"]))
        for r in rows:
            others = [x["amount"] for x in rows if x["id"] != r["id"]]
            med = statistics.median(others) if len(others) >= h["min_history"] else None
            reason = None
            if r["amount"] >= h["absolute_eur"]:
                reason = f"EUR {r['amount']:,.2f} >= absolute threshold EUR {h['absolute_eur']:,}"
            elif med and r["amount"] >= h["min_amount_eur"] and r["amount"] >= h["multiple_of_median"] * med:
                reason = f"EUR {r['amount']:,.2f} is {r['amount'] / med:.0f}x this customer's median (EUR {med:,.2f})"
            if reason:
                hits.append(_hit("high_amount", f"P-HIGH:{r['id']}", [r], reason,
                                 60 + r["amount"] / h["absolute_eur"] * 15, r["merchant"], r["country"]))
    return hits


def detect_transfers(conn, cfg: dict) -> list[dict]:
    b, h, nb = cfg["transfers"]["burst"], cfg["transfers"]["high_amount"], cfg["transfers"]["new_beneficiary"]
    hits = []
    for rows in _by_customer(conn, "transfers").values():
        for cl in _clusters(rows, b["min_count"], b["window_seconds"]):
            secs = (_dt(cl[-1]["ts"]) - _dt(cl[0]["ts"])).total_seconds()
            payees = sorted({r["beneficiary_name"] for r in cl})
            hits.append(_hit("transfer_burst", f"T-BURST:{cl[0]['id']}", cl,
                             f"{len(cl)} transfers in {secs / 60:.1f} min to {len(payees)} payee(s) "
                             f"(rule: >= {b['min_count']} within {b['window_seconds'] // 60} min)",
                             55 + 10 * (len(cl) - b["min_count"]) + (15 if any(r["is_new_beneficiary"] for r in cl) else 0),
                             ", ".join(payees), cl[0]["beneficiary_country"], beneficiary_iban=cl[0]["beneficiary_iban"]))
        for r in rows:
            if r["amount"] >= h["absolute_eur"]:
                rule, why, score = "high_amount", f"EUR {r['amount']:,.2f} >= absolute threshold EUR {h['absolute_eur']:,}", \
                    65 + r["amount"] / h["absolute_eur"] * 10
            elif r["is_new_beneficiary"] and r["amount"] >= nb["min_amount_eur"] and r["beneficiary_country"] in nb["high_risk_countries"]:
                rule, why, score = "new_beneficiary_high_risk", \
                    f"First transfer to a new payee in {r['beneficiary_country']} (high-risk) for EUR {r['amount']:,.2f}", 80
            else:
                continue
            hits.append(_hit(rule, f"T-{rule}:{r['id']}", [r], why, score, r["beneficiary_name"], r["beneficiary_country"],
                             beneficiary_iban=r["beneficiary_iban"]))
    return hits


def scan(conn: sqlite3.Connection, manifests: dict, actor: service.Actor) -> dict:
    """Idempotent: each hit has a stable rule_key and is only turned into an alert once."""
    cfg, created = load_config(), {}
    for app_key, detect in (("fraud", detect_payments), ("transfers", detect_transfers)):
        m = manifests.get(app_key)
        if m is None:
            continue
        existing = {r[0] for r in conn.execute("SELECT json_extract(data, '$.rule_key') FROM items WHERE app = ?", (app_key,))}
        created[app_key] = 0
        for hit in detect(conn, cfg):
            if hit["rule_key"] not in existing:
                service.create(conn, m, actor, hit, enforce_roles=False)
                existing.add(hit["rule_key"])
                created[app_key] += 1
    with transaction(conn):
        record_event(conn, actor=actor.email, actor_role=actor.role, action="rules.scanned", entity_type="rules",
                     entity_id="detection", ip=actor.ip,
                     details={"created": created, "rules_sha256": hashlib.sha256(config_text().encode()).hexdigest()})
    return created


def simulate(conn: sqlite3.Connection, kind: str, actor: service.Actor, rng: random.Random | None = None) -> str:
    if kind not in SIM_KINDS:
        raise ValueError(f"unknown simulation {kind}")
    rng = rng or random.Random()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    cid = f"C-SIM-{rng.randint(1000, 9999)}"
    base = {"customer_id": cid, "customer_name": f"Simulated Customer {cid[-4:]}"}
    with transaction(conn):
        if kind == "payment_burst":
            for k in range(4):
                record_payment(conn, **base, amount=rng.uniform(40, 250), merchant="GiftCardz Online", country="LT",
                               ts=(now + timedelta(seconds=8 * k)).isoformat())
        elif kind == "large_payment":
            record_payment(conn, **base, amount=7800, merchant="Luxury Watches Geneva", country="CH", ts=now.isoformat())
        elif kind == "transfer_burst":
            for k in range(4):
                record_transfer(conn, **base, amount=rng.uniform(400, 1900), beneficiary_name=f"Payee {k + 1}",
                                beneficiary_iban=f"LT{rng.randint(10, 99)} 3250 {rng.randint(1000, 9999)}", beneficiary_country="LT",
                                is_new=True, ts=(now + timedelta(seconds=15 * k)).isoformat())
        else:
            record_transfer(conn, **base, amount=25000, beneficiary_name="Gulf Trade Holdings FZE",
                            beneficiary_iban="AE07 0331 2345 6789 0123 456", beneficiary_country="AE", is_new=True,
                            ts=now.isoformat())
        record_event(conn, actor=actor.email, actor_role=actor.role, action="rules.simulated", entity_type="rules",
                     entity_id="detection", details={"kind": kind, "customer_id": cid, "sample_data": True}, ip=actor.ip)
    return cid
