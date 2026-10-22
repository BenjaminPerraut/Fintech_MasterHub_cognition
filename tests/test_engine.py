import json
import sqlite3
from datetime import date

import pytest

from app.audit import verify_chain
from app.db import connect
from app.engine import service, store
from app.engine.manifest import ManifestError, parse_manifest
from app.seed.actors import ALICE, KARIM, OLIVIA, SAM
from tests.conftest import ALICE as U_ALICE, ERIN as U_ERIN, KARIM as U_KARIM, OLIVIA as U_OLIVIA, SAM as U_SAM
from tests.conftest import login, post


def db(app):
    return connect(app.state.settings.db_path)


@pytest.fixture()
def kyc(seeded_app):
    return seeded_app.state.manifests["kyc"]


@pytest.fixture()
def fresh(seeded_app, kyc):
    """A brand-new KYC case moved to in_review by Alice."""
    conn = db(seeded_app)
    item = service.create(conn, kyc, service.SYSTEM, {"customer_name": "Test Person", "risk_score": 50},
                          enforce_roles=False)
    service.transition(conn, kyc, ALICE, item.id, "start_review")
    return item.id


# ---- manifest validation ---------------------------------------------------

BASE = {"app": "x", "title": "X", "id_prefix": "X", "initial": "a", "states": {"a": {}, "b": {}},
        "fields": [{"name": "n"}], "permissions": {"view": ["admin"]}}


@pytest.mark.parametrize("transitions,msg", [
    ([{"name": "t", "from": "a", "to": "zzz", "roles": ["admin"]}], "unknown state"),
    ([{"name": "t", "from": "a", "to": "b", "roles": ["superuser"]}], "unknown roles"),
    ([{"name": "t", "from": "a", "to": "b", "roles": ["admin"], "four_eyes": True}], "approve_roles"),
])
def test_manifest_validation_fails_fast(transitions, msg):
    with pytest.raises(ManifestError, match=msg):
        parse_manifest({**BASE, "transitions": transitions})


def test_kyc_seeded_with_sample_data(seeded_app, kyc):
    conn = db(seeded_app)
    assert conn.execute("SELECT COUNT(*) FROM items WHERE app='kyc'").fetchone()[0] == 48
    item = store.get_item(conn, "kyc", "KYC-00001")
    for f in ("id_document_type", "ssn", "proof_of_address", "selfie_match_score", "source_of_funds", "company",
              "salary_range", "retention_until" if item.get("retention_until") else "submitted_at"):
        assert f in item.data
    assert verify_chain(conn).ok and store.verify_records(conn) == []


# ---- four-eyes (service layer) --------------------------------------------

def test_proposer_can_never_approve(seeded_app, kyc, fresh):
    conn = db(seeded_app)
    assert service.transition(conn, kyc, ALICE, fresh, "approve", "looks good") == "proposed"
    assert store.get_item(conn, "kyc", fresh).state == "in_review"  # nothing applied yet
    dec = store.open_decision(conn, fresh)
    with pytest.raises(service.EngineForbidden, match="four-eyes"):
        service.approve(conn, kyc, ALICE, fresh, dec["id"])
    service.approve(conn, kyc, KARIM, fresh, dec["id"], "agree")
    assert store.get_item(conn, "kyc", fresh).state == "approved"
    rows = conn.execute("SELECT action, actor FROM audit_log WHERE entity_id=? ORDER BY id", (fresh,)).fetchall()
    proposed = [r for r in rows if r["action"] == "item.proposed"][0]
    approved = [r for r in rows if r["action"] == "item.approved"][0]
    assert proposed["actor"] != approved["actor"]


def test_proposer_cannot_approve_even_after_role_change(seeded_app, kyc, fresh):
    conn = db(seeded_app)
    service.transition(conn, kyc, ALICE, fresh, "reject", "doc mismatch")
    dec = store.open_decision(conn, fresh)
    promoted = service.Actor(ALICE.subject, ALICE.email, ALICE.name, "ops_lead")
    with pytest.raises(service.EngineForbidden, match="four-eyes"):
        service.approve(conn, kyc, promoted, fresh, dec["id"])


def test_four_eyes_enforced_by_database_trigger(seeded_app, kyc, fresh):
    conn = db(seeded_app)
    service.transition(conn, kyc, ALICE, fresh, "approve", "ok")
    dec = store.open_decision(conn, fresh)
    with pytest.raises(sqlite3.DatabaseError, match="four-eyes"):
        conn.execute("UPDATE decisions SET status='approved', decided_by=proposed_by WHERE id=?", (dec["id"],))


def test_approver_role_from_manifest(seeded_app, kyc, fresh):
    conn = db(seeded_app)
    service.transition(conn, kyc, ALICE, fresh, "reject", "selfie mismatch")
    dec = store.open_decision(conn, fresh)
    with pytest.raises(service.EngineForbidden):  # reject needs an Ops Lead second pair of eyes
        service.approve(conn, kyc, KARIM, fresh, dec["id"])
    service.approve(conn, kyc, OLIVIA, fresh, dec["id"])
    assert store.get_item(conn, "kyc", fresh).state == "rejected"


def test_four_eyes_requires_justification(seeded_app, kyc, fresh):
    with pytest.raises(service.EngineError, match="justification"):
        service.transition(db(seeded_app), kyc, ALICE, fresh, "approve", "")


def test_no_other_action_while_decision_pending(seeded_app, kyc, fresh):
    conn = db(seeded_app)
    service.transition(conn, kyc, ALICE, fresh, "approve", "ok")
    with pytest.raises(service.EngineConflict):
        service.transition(conn, kyc, KARIM, fresh, "escalate", "hmm")


def test_withdraw_only_by_proposer(seeded_app, kyc, fresh):
    conn = db(seeded_app)
    service.transition(conn, kyc, ALICE, fresh, "approve", "ok")
    dec = store.open_decision(conn, fresh)
    with pytest.raises(service.EngineForbidden):
        service.withdraw(conn, kyc, KARIM, fresh, dec["id"])
    service.withdraw(conn, kyc, ALICE, fresh, dec["id"])
    assert store.open_decision(conn, fresh) is None


def test_stale_version_conflict(seeded_app, kyc, fresh):
    conn = db(seeded_app)
    with pytest.raises(service.EngineConflict, match="changed since"):
        service.transition(conn, kyc, ALICE, fresh, "escalate", "x", expected_version=1)


def test_role_not_in_manifest_cannot_transition(seeded_app, kyc, fresh):
    with pytest.raises(service.EngineForbidden):
        service.transition(db(seeded_app), kyc, SAM, fresh, "escalate", "x")


def test_items_and_comments_are_undeletable(seeded_app, kyc, fresh):
    conn = db(seeded_app)
    service.comment(conn, kyc, ALICE, fresh, "note")
    for sql in ("DELETE FROM items WHERE id=?", "DELETE FROM comments WHERE item_id=?",
                "UPDATE comments SET body='x' WHERE item_id=?"):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql, (fresh,))


def test_out_of_band_record_edit_is_detected(seeded_app, kyc, fresh):
    conn = db(seeded_app)
    data = store.get_item(conn, "kyc", fresh).data
    conn.execute("UPDATE items SET data=? WHERE id=?", (json.dumps({**data, "risk_score": 1}), fresh))
    issues = store.verify_records(conn)
    assert [i["item"] for i in issues] == [fresh]
    # ...even if the attacker also recomputes the row hash, the audit trail disagrees.
    item = store.get_item(conn, "kyc", fresh)
    conn.execute("UPDATE items SET content_hash=? WHERE id=?", (store.content_hash(item), fresh))
    assert "never recorded" in store.verify_records(conn)[0]["problem"]


# ---- HTTP layer ------------------------------------------------------------

def test_http_self_approval_is_refused_and_logged(sclient, seeded_app, kyc, fresh):
    login(sclient, U_ALICE)
    r = post(sclient, f"/apps/kyc/{fresh}/transition/approve", {"comment": "all good"})
    assert r.status_code == 303
    dec = store.open_decision(db(seeded_app), fresh)
    r = post(sclient, f"/apps/kyc/{fresh}/decision/{dec['id']}/approve")
    assert r.status_code == 403
    conn = db(seeded_app)
    assert store.get_item(conn, "kyc", fresh).state == "in_review"
    denied = conn.execute("SELECT details FROM audit_log WHERE action='access.denied' ORDER BY id DESC").fetchone()
    assert "four-eyes" in denied["details"]
    # the second analyst can approve
    post(sclient, "/logout")
    login(sclient, U_KARIM)
    assert post(sclient, f"/apps/kyc/{fresh}/decision/{dec['id']}/approve").status_code == 303
    assert store.get_item(db(seeded_app), "kyc", fresh).state == "approved"


def test_http_rbac_matrix(sclient, fresh):
    login(sclient, U_ERIN)  # engineers have no access to customer PII apps
    assert sclient.get("/apps/kyc").status_code == 403
    post(sclient, "/logout")
    login(sclient, U_SAM)   # support: read-only, masked
    page = sclient.get(f"/apps/kyc/{fresh}")
    assert page.status_code == 200 and "Start review" not in page.text
    assert post(sclient, f"/apps/kyc/{fresh}/transition/escalate", {"comment": "x"}).status_code == 403
    assert post(sclient, f"/apps/kyc/{fresh}/reveal").status_code == 403
    assert post(sclient, f"/apps/kyc/{fresh}/comment", {"body": "x"}).status_code == 403


def test_pii_masked_and_reveal_audited(sclient, seeded_app):
    conn = db(seeded_app)
    ssn = store.get_item(conn, "kyc", "KYC-00001").data["ssn"]
    login(sclient, U_ALICE)
    assert ssn not in sclient.get("/apps/kyc/KYC-00001").text
    r = post(sclient, "/apps/kyc/KYC-00001/reveal")
    assert ssn in r.text
    row = conn.execute("SELECT * FROM audit_log WHERE action='pii.revealed'").fetchone()
    assert row["entity_id"] == "KYC-00001" and row["actor"].startswith("alice")


def test_export_masked_and_audited(sclient, seeded_app):
    login(sclient, U_ALICE)
    assert sclient.get("/apps/kyc/export.csv").status_code == 403
    post(sclient, "/logout")
    login(sclient, U_OLIVIA)
    r = sclient.get("/apps/kyc/export.csv")
    conn = db(seeded_app)
    ssn = store.get_item(conn, "kyc", "KYC-00001").data["ssn"]
    assert r.status_code == 200 and ssn not in r.text and "KYC-00001" in r.text
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='data.exported'").fetchone()[0] == 1


def test_retention_filter(sclient, seeded_app):
    login(sclient, U_OLIVIA)
    today = date.today().isoformat()
    r = sclient.get(f"/apps/kyc?f_retention_until_to={today}")
    conn = db(seeded_app)
    expected = conn.execute("SELECT COUNT(*) FROM items WHERE app='kyc' AND json_extract(data,'$.retention_until') <= ?",
                            (today,)).fetchone()[0]
    assert expected > 0 and f"{expected} items" in r.text


def test_comment_and_claim_audited(sclient, seeded_app, fresh):
    login(sclient, U_KARIM)
    post(sclient, f"/apps/kyc/{fresh}/comment", {"body": "Called customer"})
    post(sclient, f"/apps/kyc/{fresh}/claim")
    actions = [r[0] for r in db(seeded_app).execute("SELECT action FROM audit_log WHERE entity_id=?", (fresh,))]
    assert "item.commented" in actions and "item.assigned" in actions


def test_verify_page_flags_tampered_record(sclient, seeded_app):
    db(seeded_app).execute("UPDATE items SET state='approved' WHERE id='KYC-00002'")
    login(sclient, U_OLIVIA)
    r = sclient.get("/audit/verify")
    assert "Integrity failure" in r.text and "KYC-00002" in r.text
