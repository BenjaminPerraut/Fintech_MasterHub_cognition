import sqlite3

import pytest

from app.audit import record_event, verify_chain
from app.db import connect, transaction
from tests.conftest import ADA, ALICE, ERIN, MAX, OLIVIA, SAM, csrf_from, login, post


def db_of(app):
    return connect(app.state.settings.db_path)


def test_anonymous_is_redirected_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_login_requires_csrf(client):
    r = client.post("/auth/callback", data={"subject": ALICE}, follow_redirects=False)
    assert r.status_code == 403


def test_login_and_logout_are_audited(client, app):
    assert login(client, ALICE).status_code == 303
    assert "Alice Martin" in client.get("/").text
    assert post(client, "/logout").status_code == 303
    actions = [r["action"] for r in db_of(app).execute("SELECT action FROM audit_log ORDER BY id")]
    assert actions == ["user.provisioned", "auth.login", "auth.logout"]


def test_unknown_account_and_dual_role_are_refused(client, app):
    assert login(client, "u-9999").status_code == 403
    r = login(client, MAX)
    assert r.status_code == 403 and "one role per person" in r.text
    assert client.get("/", follow_redirects=False).status_code == 303
    refused = db_of(app).execute("SELECT COUNT(*) FROM audit_log WHERE action='auth.login_refused'").fetchone()[0]
    assert refused == 2


def test_open_redirect_blocked(client):
    token = csrf_from(client)
    r = client.post("/auth/callback", data={"email": "alice.martin@northwindpay.example", "password": "Northwind-Demo-2026", "csrf_token": token,
                          "next": "//evil.example"},
                    follow_redirects=False)
    assert r.headers["location"] == "/"


@pytest.mark.parametrize("subject,status", [(SAM, 403), (ALICE, 403), (ERIN, 403), (OLIVIA, 200), (ADA, 200)])
def test_audit_log_rbac(client, subject, status):
    login(client, subject)
    assert client.get("/audit").status_code == status


def test_access_denied_is_recorded(client, app):
    login(client, SAM)
    client.get("/audit")
    row = db_of(app).execute("SELECT * FROM audit_log WHERE action='access.denied'").fetchone()
    assert row is not None and row["actor"].startswith("sam.rivera") and "audit:read" in row["details"]


def test_deactivated_user_loses_access_immediately(client, app):
    login(client, ALICE)
    assert client.get("/").status_code == 200
    conn = db_of(app)
    conn.execute("UPDATE users SET active = 0 WHERE subject = ?", (ALICE,))
    assert client.get("/", follow_redirects=False).status_code == 303
    assert login(client, ALICE).status_code == 403


def test_security_headers(client):
    r = client.get("/login")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert "httponly" in r.headers["set-cookie"].lower()


def test_no_api_docs_exposed(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


# ---- audit chain -----------------------------------------------------------

def _seed_events(conn, n=5):
    for i in range(n):
        with transaction(conn):
            record_event(conn, actor="t@example", actor_role="admin", action="test.event", details={"i": i})


def test_record_event_requires_transaction(app):
    with pytest.raises(RuntimeError):
        record_event(db_of(app), actor="x", actor_role="admin", action="x")


def test_triggers_block_update_and_delete(app):
    conn = db_of(app)
    _seed_events(conn, 2)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE audit_log SET actor = 'mallory' WHERE id = 1")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM audit_log WHERE id = 1")


def test_chain_verifies_and_detects_tampering(app):
    conn = db_of(app)
    _seed_events(conn, 5)
    assert verify_chain(conn).ok
    # An attacker with raw DB access drops the trigger and edits a row.
    conn.execute("DROP TRIGGER audit_log_no_update")
    conn.execute("UPDATE audit_log SET details = '{\"i\":99}' WHERE id = 3")
    result = verify_chain(conn)
    assert not result.ok and result.first_bad_id == 3 and not result.triggers_present


def test_chain_detects_deleted_row(app):
    conn = db_of(app)
    _seed_events(conn, 4)
    conn.execute("DROP TRIGGER audit_log_no_delete")
    conn.execute("DELETE FROM audit_log WHERE id = 2")
    result = verify_chain(conn)
    assert not result.ok and result.first_bad_id == 3


def test_verify_page(client, app):
    login(client, OLIVIA)
    r = client.get("/audit/verify")
    assert r.status_code == 200 and "Chain intact" in r.text
    conn = db_of(app)
    conn.execute("DROP TRIGGER audit_log_no_update")
    conn.execute("UPDATE audit_log SET actor = 'mallory' WHERE id = 1")
    r = client.get("/audit/verify")
    assert "Integrity failure" in r.text and "#1" in r.text
