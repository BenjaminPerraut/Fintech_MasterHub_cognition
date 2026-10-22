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
