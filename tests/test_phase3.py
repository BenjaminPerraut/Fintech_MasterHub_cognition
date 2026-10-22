import json
import sqlite3
from datetime import date, timedelta

import pytest

from app.audit import verify_chain
from app.db import connect
from app.engine import service, store
from app.engine.manifest import ManifestError, parse_manifest
from app.seed.actors import ADA, ALICE, ERIN, ETHAN, OLIVIA, OMAR, SAM
from tests.conftest import login, post


def db(app):
    return connect(app.state.settings.db_path)


@pytest.fixture()
def refunds(seeded_app):
    return seeded_app.state.manifests["refunds"]


@pytest.fixture()
def flags(seeded_app):
    return seeded_app.state.manifests["flags"]


def new_refund(conn, m, amount, creator=SAM):
    item = service.create(conn, m, creator, {"customer_name": "Test Customer", "customer_id": "C-1",
                                             "transaction_id": "TX-1", "amount": amount, "reason": "duplicate_charge"})
    service.transition(conn, m, creator, item.id, "start_review")
    return item.id


# ---- refunds: conditional four-eyes + segregation of duties -----------------

@pytest.mark.parametrize("amount,expected", [(120, "applied"), (499.99, "applied"), (500, "proposed"), (4200, "proposed")])
def test_refund_four_eyes_threshold(seeded_app, refunds, amount, expected):
    conn = db(seeded_app)
    rid = new_refund(conn, refunds, amount)
    assert service.transition(conn, refunds, OMAR, rid, "approve", "verified") == expected
    assert store.get_item(conn, "refunds", rid).state == ("approved" if expected == "applied" else "in_review")


def test_large_refund_needs_second_ops_lead_and_payout_by_ops(seeded_app, refunds):
    conn = db(seeded_app)
    rid = new_refund(conn, refunds, 900)
    service.transition(conn, refunds, OMAR, rid, "approve", "dup charge")
    dec = store.open_decision(conn, rid)
    with pytest.raises(service.EngineForbidden, match="four-eyes"):
        service.approve(conn, refunds, OMAR, rid, dec["id"])
    service.approve(conn, refunds, OLIVIA, rid, dec["id"])
    with pytest.raises(service.EngineForbidden):
        service.transition(conn, refunds, SAM, rid, "execute_payout")
    service.transition(conn, refunds, OMAR, rid, "execute_payout")
    assert store.get_item(conn, "refunds", rid).state == "paid"
    proposed = conn.execute("SELECT details FROM audit_log WHERE entity_id=? AND action='item.proposed'", (rid,)).fetchone()
    assert "amount >= 500" in proposed["details"]


def test_refund_creator_can_never_approve(seeded_app, refunds):
    conn = db(seeded_app)
    rid = new_refund(conn, refunds, 120)              # Sam logs a small refund...
    with pytest.raises(service.EngineForbidden, match="segregation"):
        service.transition(conn, refunds, SAM, rid, "approve", "ok")   # ...and can't approve it himself
    big = new_refund(conn, refunds, 2500, creator=OMAR)  # an Ops Lead logs a large one
    service.transition(conn, refunds, OLIVIA, big, "approve", "ok")
    with pytest.raises(service.EngineForbidden, match="segregation"):  # and can't be the second approver either
        service.approve(conn, refunds, OMAR, big, store.open_decision(conn, big)["id"])


def test_refund_rbac(seeded_app, refunds):
    with pytest.raises(service.EngineForbidden):
        service.create(db(seeded_app), refunds, ERIN, {"customer_name": "x", "customer_id": "x", "transaction_id": "x",
                                                        "amount": 1, "reason": "goodwill"})


def test_refund_http_rbac_and_create(sclient, seeded_app):
    login(sclient, "u-1006")  # engineer
    assert sclient.get("/apps/refunds").status_code == 403
    post(sclient, "/logout")
    login(sclient, "u-1001")  # kyc analyst
    assert sclient.get("/apps/refunds").status_code == 403
    post(sclient, "/logout")
    login(sclient, "u-1005")  # support
    r = post(sclient, "/apps/refunds/new", {"customer_name": "Walk In", "customer_id": "C-9", "transaction_id": "TX-9",
                                             "amount": "75.50", "reason": "wrong_amount"})
    assert r.status_code == 303
    rid = r.headers["location"].rsplit("/", 1)[-1]
    item = store.get_item(db(seeded_app), "refunds", rid)
    assert item.data["sla_due"] == (date.today() + timedelta(days=5)).isoformat()
    assert item.data["requested_at"] == date.today().isoformat()
    assert store.created_by(db(seeded_app), rid) == "sam.rivera@northwindpay.example"
    assert sclient.get("/apps/refunds?f_amount_min=500&open=1").status_code == 200


def test_refund_seed(seeded_app):
    conn = db(seeded_app)
    assert conn.execute("SELECT COUNT(*) FROM items WHERE app='refunds'").fetchone()[0] == 63
    assert conn.execute("SELECT COUNT(*) FROM decisions d JOIN items i ON i.id=d.item_id "
                        "WHERE i.app='refunds' AND d.status='open'").fetchone()[0] == 5


# ---- feature flags: voting + rollout ---------------------------------------

def new_flag(conn, m, creator=ERIN):
    item = service.create(conn, m, creator, {"flag_key": "test-flag", "title": "Test", "environment": "production",
                                             "risk": "low"})
    service.transition(conn, m, creator, item.id, "open_vote")
    return item.id


def test_flag_creator_cannot_vote_and_votes_are_final(seeded_app, flags):
    conn = db(seeded_app)
    fid = new_flag(conn, flags)
    with pytest.raises(service.EngineForbidden, match="proposed"):
        service.vote(conn, flags, ERIN, fid, "up")
    service.vote(conn, flags, ETHAN, fid, "up")
    with pytest.raises(service.EngineConflict, match="already voted"):
        service.vote(conn, flags, ETHAN, fid, "down", "changed my mind")
    with pytest.raises(service.EngineError, match="justification"):
        service.vote(conn, flags, OLIVIA, fid, "down")
    with pytest.raises(service.EngineForbidden):
        service.vote(conn, flags, SAM, fid, "up")


def test_flag_quorum_then_four_eyes_rollout(seeded_app, flags):
    conn = db(seeded_app)
    fid = new_flag(conn, flags)
    service.vote(conn, flags, ETHAN, fid, "up")
    service.vote(conn, flags, OLIVIA, fid, "up")
    with pytest.raises(service.EngineConflict, match="needs 3"):
        service.transition(conn, flags, ERIN, fid, "accept")
    service.vote(conn, flags, ADA, fid, "up")
    service.transition(conn, flags, ERIN, fid, "accept")
    with pytest.raises(service.EngineConflict, match="voting is closed"):
        service.vote(conn, flags, OMAR, fid, "up")

    assert service.transition(conn, flags, ERIN, fid, "start_canary", "go") == "proposed"
    dec = store.open_decision(conn, fid)
    with pytest.raises(service.EngineForbidden, match="four-eyes"):
        service.approve(conn, flags, ERIN, fid, dec["id"])
    service.approve(conn, flags, ETHAN, fid, dec["id"])
    item = store.get_item(conn, "flags", fid)
    assert (item.state, item.data["rollout_pct"]) == ("canary", 10)

    service.transition(conn, flags, ERIN, fid, "promote", "clean canary")
    dec = store.open_decision(conn, fid)
    with pytest.raises(service.EngineForbidden):  # promote needs an Ops Lead
        service.approve(conn, flags, ETHAN, fid, dec["id"])
    service.approve(conn, flags, OLIVIA, fid, dec["id"])
    assert store.get_item(conn, "flags", fid).data["rollout_pct"] == 100

    with pytest.raises(service.EngineError, match="justification"):
        service.transition(conn, flags, ETHAN, fid, "rollback")
    assert service.transition(conn, flags, ETHAN, fid, "rollback", "error spike") == "applied"
    item = store.get_item(conn, "flags", fid)
    assert (item.state, item.data["rollout_pct"]) == ("rolled_back", 0)
    last = conn.execute("SELECT details FROM audit_log WHERE entity_id=? ORDER BY id DESC", (fid,)).fetchone()
    assert json.loads(last["details"])["changes"]["rollout_pct"] == {"from": 100, "to": 0}


def test_votes_append_only(seeded_app, flags):
    conn = db(seeded_app)
    fid = new_flag(conn, flags)
    service.vote(conn, flags, ETHAN, fid, "up")
    for sql in ("UPDATE votes SET vote='down' WHERE item_id=?", "DELETE FROM votes WHERE item_id=?"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(sql, (fid,))


def test_vote_http(sclient, seeded_app, flags):
    fid = new_flag(db(seeded_app), flags)
    login(sclient, "u-1006")  # Erin, the creator
    assert post(sclient, f"/apps/flags/{fid}/vote", {"vote": "up"}).status_code == 403
    post(sclient, "/logout")
    login(sclient, "u-1007")  # Ethan
    assert post(sclient, f"/apps/flags/{fid}/vote", {"vote": "up"}).status_code == 303
    page = sclient.get(f"/apps/flags/{fid}")
    assert "Ethan Brooks" in page.text and "You have voted" in page.text
    post(sclient, "/logout")
    login(sclient, "u-1005")  # support: read-only
    assert sclient.get("/apps/flags").status_code == 200
    assert post(sclient, f"/apps/flags/{fid}/vote", {"vote": "up"}).status_code == 403


def test_flags_seed_and_integrity(seeded_app):
    conn = db(seeded_app)
    assert conn.execute("SELECT COUNT(*) FROM items WHERE app='flags'").fetchone()[0] == 14
    assert verify_chain(conn).ok and store.verify_records(conn) == []


BASE = {"app": "x", "title": "X", "id_prefix": "X", "initial": "a", "states": {"a": {}, "b": {}},
        "fields": [{"name": "n"}, {"name": "amt", "type": "money"}], "permissions": {"view": ["admin"]}}


@pytest.mark.parametrize("extra,transition,msg", [
    ({}, {"four_eyes_if": {"field": "amt", "gte": 5}}, "approve_roles"),
    ({}, {"four_eyes_if": {"field": "n", "gte": 5}, "approve_roles": ["admin"]}, "numeric"),
    ({}, {"requires_quorum": True}, "voting"),
    ({}, {"set": {"nope": 1}}, "undeclared"),
    ({"voting": {"roles": ["admin"], "quorum": 0, "states": ["a"]}}, {}, "quorum"),
])
def test_manifest_phase3_validation(extra, transition, msg):
    t = {"name": "t", "from": "a", "to": "b", "roles": ["admin"], **transition}
    with pytest.raises(ManifestError, match=msg):
        parse_manifest({**BASE, **extra, "transitions": [t]})
