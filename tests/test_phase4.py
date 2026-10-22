import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.audit import verify_chain
from app.db import connect, transaction
from app.engine import service, store
from app.rules import engine
from app.seed.actors import ALICE, OLIVIA, SAM
from tests.conftest import login, post

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


def db(app):
    return connect(app.state.settings.db_path)


def pay(conn, cid, amount, seconds, merchant="Shop"):
    with transaction(conn):
        return engine.record_payment(conn, customer_id=cid, customer_name=f"Cust {cid}", amount=amount, merchant=merchant,
                                     country="ES", ts=(NOW + timedelta(seconds=seconds)).isoformat())


def xfer(conn, cid, amount, seconds, country="ES", new=False, payee="Payee"):
    with transaction(conn):
        return engine.record_transfer(conn, customer_id=cid, customer_name=f"Cust {cid}", amount=amount,
                                      beneficiary_name=payee, beneficiary_iban="ES00 0000", beneficiary_country=country,
                                      is_new=new, ts=(NOW + timedelta(seconds=seconds)).isoformat())


def alerts(conn, app, cid):
    return [store._row_to_item(r) for r in conn.execute(
        "SELECT * FROM items WHERE app=? AND json_extract(data,'$.customer_id')=?", (app, cid))]


def scan(app):
    return engine.scan(db(app), app.state.manifests, service.SYSTEM)


def test_seed_has_alerts_of_every_rule_and_stays_verifiable(seeded_app):
    conn = db(seeded_app)
    rules = {(r[0], r[1]) for r in conn.execute("SELECT app, json_extract(data,'$.rule') FROM items WHERE app IN ('fraud','transfers')")}
    assert rules == {("fraud", "velocity_burst"), ("fraud", "high_amount"), ("transfers", "transfer_burst"),
                     ("transfers", "high_amount"), ("transfers", "new_beneficiary_high_risk")}
    assert verify_chain(conn).ok and store.verify_records(conn) == []


@pytest.mark.parametrize("offsets,expected", [([0, 30], 0), ([0, 30, 61], 0), ([0, 30, 60], 1), ([0, 5, 10, 15], 1)])
def test_payment_velocity_window(seeded_app, offsets, expected):
    cid = f"T-VEL-{len(offsets)}-{offsets[-1]}"
    for s in offsets:
        pay(db(seeded_app), cid, 20, s)
    scan(seeded_app)
    hits = [a for a in alerts(db(seeded_app), "fraud", cid) if a.data["rule"] == "velocity_burst"]
    assert len(hits) == expected
    if expected:
        assert hits[0].data["txn_count"] == len(offsets)


def test_scan_is_idempotent(seeded_app):
    pay(db(seeded_app), "T-IDEM", 9000, 0)
    assert scan(seeded_app)["fraud"] == 1
    assert scan(seeded_app) == {"fraud": 0, "transfers": 0}


def test_high_amount_absolute_and_relative(seeded_app):
    conn = db(seeded_app)
    pay(conn, "T-ABS", 4999, 0)
    pay(conn, "T-ABS2", 5000, 0)
    for i in range(6):
        pay(conn, "T-REL", 40, i * 3600)
    pay(conn, "T-REL", 1200, 99999)          # 30x median, >= 1000
    pay(conn, "T-REL-SMALL", 40, 0)
    scan(seeded_app)
    assert alerts(conn, "fraud", "T-ABS") == []
    assert alerts(conn, "fraud", "T-ABS2")[0].data["rule"] == "high_amount"
    rel = alerts(conn, "fraud", "T-REL")
    assert len(rel) == 1 and "median" in rel[0].data["description"]


def test_transfer_rules(seeded_app):
    conn = db(seeded_app)
    for k in range(3):
        xfer(conn, "T-BURST", 500, k * 200, new=True, payee=f"P{k}")
    xfer(conn, "T-BIG", 10000, 0)
    xfer(conn, "T-RISK", 3000, 0, country="AE", new=True)
    xfer(conn, "T-SAFE", 3000, 0, country="FR", new=True)
    scan(seeded_app)
    assert alerts(conn, "transfers", "T-BURST")[0].data["rule"] == "transfer_burst"
    assert alerts(conn, "transfers", "T-BIG")[0].data["rule"] == "high_amount"
    assert alerts(conn, "transfers", "T-RISK")[0].data["rule"] == "new_beneficiary_high_risk"
    assert alerts(conn, "transfers", "T-SAFE") == []


def test_ledger_is_append_only(seeded_app):
    conn = db(seeded_app)
    for sql in ("UPDATE payments SET amount = 1", "DELETE FROM transfers"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(sql)


def test_closing_alert_as_false_positive_needs_four_eyes(seeded_app):
    conn, m = db(seeded_app), seeded_app.state.manifests["fraud"]
    pay(conn, "T-FP", 8000, 0)
    scan(seeded_app)
    aid = alerts(conn, "fraud", "T-FP")[0].id
    service.transition(conn, m, ALICE, aid, "investigate")
    assert service.transition(conn, m, ALICE, aid, "mark_false_positive", "known customer") == "proposed"
    with pytest.raises(service.EngineForbidden, match="four-eyes"):
        service.approve(conn, m, ALICE, aid, store.open_decision(conn, aid)["id"])
    service.approve(conn, m, OLIVIA, aid, store.open_decision(conn, aid)["id"])
    assert store.get_item(conn, "fraud", aid).state == "false_positive"


def test_confirm_fraud_freezes_and_hold_is_instant(seeded_app):
    conn = db(seeded_app)
    fm, tm = seeded_app.state.manifests["fraud"], seeded_app.state.manifests["transfers"]
    pay(conn, "T-CF", 8000, 0)
    xfer(conn, "T-HOLD", 20000, 0)
    scan(seeded_app)
    aid = alerts(conn, "fraud", "T-CF")[0].id
    service.transition(conn, fm, ALICE, aid, "investigate")
    service.transition(conn, fm, ALICE, aid, "confirm_fraud", "stolen card")
    service.approve(conn, fm, OLIVIA, aid, store.open_decision(conn, aid)["id"])
    assert store.get_item(conn, "fraud", aid).data["account_action"] == "frozen"
    tid = alerts(conn, "transfers", "T-HOLD")[0].id
    assert service.transition(conn, tm, ALICE, tid, "hold", "checking SoF") == "applied"
    assert store.get_item(conn, "transfers", tid).data["transfer_status"] == "held"
    with pytest.raises(service.EngineForbidden):
        service.transition(conn, tm, SAM, tid, "release", "x")


def test_alerts_cannot_be_created_by_hand(seeded_app):
    with pytest.raises(service.EngineForbidden):
        service.create(db(seeded_app), seeded_app.state.manifests["fraud"], OLIVIA, {"customer_name": "x"})


def test_rules_http_rbac_and_audit(sclient, seeded_app):
    login(sclient, "u-1005")  # support
    assert sclient.get("/rules").status_code == 403
    post(sclient, "/logout")
    login(sclient, "u-1006")  # engineer: can read the rules, cannot run them
    page = sclient.get("/rules")
    assert page.status_code == 200 and "window_seconds" in page.text and "Run scan now" not in page.text
    assert post(sclient, "/rules/scan").status_code == 403
    post(sclient, "/logout")
    login(sclient, "u-1003")  # ops lead
    before = db(seeded_app).execute("SELECT COUNT(*) FROM items WHERE app='transfers'").fetchone()[0]
    assert post(sclient, "/rules/simulate", {"kind": "large_transfer"}).status_code == 303
    conn = db(seeded_app)
    assert conn.execute("SELECT COUNT(*) FROM items WHERE app='transfers'").fetchone()[0] == before + 1
    actions = [r[0] for r in conn.execute("SELECT action FROM audit_log WHERE actor LIKE 'olivia%' ORDER BY id")]
    assert "rules.simulated" in actions and "rules.scanned" in actions
    assert post(sclient, "/rules/simulate", {"kind": "nope"}).status_code == 400
    assert sclient.get("/apps/fraud?f_rule=velocity_burst&open=1").status_code == 200
