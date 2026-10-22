import dataclasses

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, load_settings
from app.db import connect
from app.engine import service, store
from app.seed.actors import OMAR, SAM
from tests.conftest import login, post


def test_stub_login_refused_outside_sandbox():
    with pytest.raises(ConfigError, match="stub"):
        load_settings(sandbox=False, idp="stub", secret_key="x" * 40, https_only=True, seed=False)


def test_production_needs_secret_and_https_and_no_seed():
    with pytest.raises(ConfigError) as exc:
        load_settings(sandbox=False, idp="entra", secret_key="", https_only=False, seed=True)
    for needle in ("MASTERHUB_SECRET_KEY", "MASTERHUB_HTTPS_ONLY", "seeding"):
        assert needle in str(exc.value)
    ok = load_settings(sandbox=False, idp="entra", secret_key="x" * 40, https_only=True, seed=False)
    assert ok.env_label == "PRODUCTION"


def test_logout_revokes_the_session_cookie(app, client):
    login(client, "u-1003")
    stolen = client.cookies.get("mh_session")
    with TestClient(app) as attacker:
        attacker.cookies.set("mh_session", stolen)
        assert attacker.get("/", follow_redirects=False).status_code == 200   # replay works while logged in
        post(client, "/logout")
        assert attacker.get("/", follow_redirects=False).status_code == 303   # and stops after logout


def test_refund_iban_cannot_be_typed(sclient, seeded_app):
    assert seeded_app.state.manifests["refunds"].field("iban").input is False
    login(sclient, "u-1005")
    r = post(sclient, "/apps/refunds/new", {"customer_name": "X", "customer_id": "C-9", "transaction_id": "TX-9",
                                             "amount": "499", "reason": "goodwill", "iban": "LT00 1111 2222"})
    rid = r.headers["location"].rsplit("/", 1)[-1]
    assert "iban" not in store.get_item(connect(seeded_app.state.settings.db_path), "refunds", rid).data
    post(sclient, f"/apps/refunds/{rid}/transition/start_review")
    page = sclient.get(f"/apps/refunds/{rid}").text
    assert "Approve refund" not in page                               # not offered to the creator...
    assert post(sclient, f"/apps/refunds/{rid}/transition/approve", {"comment": "x"}).status_code == 403  # ...nor allowed


def test_simulation_disabled_outside_sandbox(sclient, seeded_app):
    login(sclient, "u-1003")
    seeded_app.state.settings = dataclasses.replace(seeded_app.state.settings, sandbox=False)
    assert "Simulate traffic" not in sclient.get("/rules").text
    assert post(sclient, "/rules/simulate", {"kind": "large_transfer"}).status_code == 404


@pytest.mark.parametrize("url", ["/apps/kyc?risk_min=abc", "/apps/kyc?page=abc", "/apps/kyc?f_risk_score_min=x"])
def test_malformed_filters_are_400(sclient, url):
    login(sclient, "u-1003")
    assert sclient.get(url).status_code == 400


def test_malformed_version_is_400(sclient):
    login(sclient, "u-1001")
    r = post(sclient, "/apps/kyc/KYC-00001/transition/start_review", {"version": "abc"})
    assert r.status_code == 400


def test_legacy_refund_without_created_by_still_blocks_creator(seeded_app):
    from app.audit import record_event
    from app.db import transaction
    conn, m = connect(seeded_app.state.settings.db_path), seeded_app.state.manifests["refunds"]
    with transaction(conn):
        item = store.insert_item(conn, app="refunds", item_id="RF-LEGACY", state="in_review", priority=0,
                                 data={"customer_name": "Old", "customer_id": "C", "transaction_id": "T", "amount": 120,
                                       "reason": "goodwill"})
        record_event(conn, actor=SAM.email, actor_role="support", action="item.created", entity_type="item",
                     entity_id="RF-LEGACY", details={"app": "refunds", "after_hash": item.content_hash, "version": 1})
    assert store.creator(conn, "RF-LEGACY") == SAM.subject
    with pytest.raises(service.EngineForbidden, match="segregation"):
        service.transition(conn, m, SAM, "RF-LEGACY", "approve", "ok")


def test_creator_not_prompted_to_approve_but_can_reject(sclient, seeded_app):
    from app.seed.actors import OLIVIA
    conn, m = connect(seeded_app.state.settings.db_path), seeded_app.state.manifests["refunds"]
    item = service.create(conn, m, OMAR, {"customer_name": "Z", "customer_id": "C", "transaction_id": "T",
                                          "amount": 900, "reason": "goodwill"})
    service.transition(conn, m, OMAR, item.id, "start_review")
    service.transition(conn, m, OLIVIA, item.id, "approve", "ok")
    login(sclient, "u-1004")  # Omar, the creator
    desk = sclient.get("/").text
    approvals = desk.split('id="approvals"', 1)[1].split("</table>", 1)[0] if 'id="approvals"' in desk else ""
    assert f"/{item.id}\"" not in approvals
    page = sclient.get(f"/apps/refunds/{item.id}").text
    assert "Reject proposal" in page and "Approve as second reviewer" not in page
    assert post(sclient, f"/apps/refunds/{item.id}/decision/{store.open_decision(conn, item.id)['id']}/reject",
                {"comment": "wrong amount"}).status_code == 303


def test_expired_sessions_are_pruned_on_login(app, client):
    conn = connect(app.state.settings.db_path)
    conn.execute("INSERT INTO sessions (id, subject, created_at) VALUES ('old', 'u-1003', '2020-01-01T00:00:00+00:00')")
    login(client, "u-1003")
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE id = 'old'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_referrer_policy_keeps_origin_on_form_posts(client):
    # "no-referrer" makes browsers send Origin: null on POST forms, which proxies reject ("bad origin").
    assert client.get("/login").headers["referrer-policy"] == "same-origin"
