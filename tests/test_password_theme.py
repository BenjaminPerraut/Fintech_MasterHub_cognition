from app.db import connect
from tests.conftest import csrf_from


def attempt(client, email, password):
    return client.post("/auth/callback", data={"email": email, "password": password, "csrf_token": csrf_from(client),
                                               "next": "/"}, follow_redirects=False)


def test_correct_password_signs_in(client):
    r = attempt(client, "olivia.chen@northwindpay.example", "Northwind-Demo-2026")
    assert r.status_code == 303 and client.get("/").status_code == 200


def test_wrong_password_and_unknown_email_refused_and_audited(app, client):
    for email in ("olivia.chen@northwindpay.example", "nobody@northwindpay.example"):
        r = attempt(client, email, "wrong-password")
        assert r.status_code == 403 and "invalid email or password" in r.text
    rows = connect(app.state.settings.db_path).execute(
        "SELECT details FROM audit_log WHERE action='auth.login_refused'").fetchall()
    assert len(rows) == 2 and "olivia.chen@northwindpay.example" in rows[0]["details"]


def test_lockout_after_five_failures_is_generic_and_audited(app, client):
    for _ in range(5):
        attempt(client, "sam.rivera@northwindpay.example", "nope")
    r = attempt(client, "sam.rivera@northwindpay.example", "Northwind-Demo-2026")
    assert r.status_code == 403 and "invalid email or password" in r.text and "too many" not in r.text
    last = connect(app.state.settings.db_path).execute(
        "SELECT details FROM audit_log WHERE action='auth.login_refused' ORDER BY id DESC LIMIT 1").fetchone()
    assert "locked out" in last["details"]


def test_lockout_lasts_five_minutes_from_the_fifth_failure(client, monkeypatch):
    import app.auth.provider as provider
    clock = [1_000_000.0]
    monkeypatch.setattr(provider.time, "time", lambda: clock[0])
    for t in (0, 60, 120, 180, 299):
        clock[0] = 1_000_000.0 + t
        attempt(client, "erin.novak@northwindpay.example", "nope")
    clock[0] = 1_000_000.0 + 300
    assert attempt(client, "erin.novak@northwindpay.example", "Northwind-Demo-2026").status_code == 403
    clock[0] = 1_000_000.0 + 299 + 300
    assert attempt(client, "erin.novak@northwindpay.example", "Northwind-Demo-2026").status_code == 303


def test_dark_mode_keeps_audit_details_and_verify_banner_readable(client):
    css = client.get("/static/style.css").text
    for sel in ('[data-theme="dark"] .table td.details code', '[data-theme="dark"] .verify-banner.ok',
                '[data-theme="dark"] .verify-banner.bad'):
        assert sel in css


def test_login_page_has_password_form_and_theme_toggle(client):
    page = client.get("/login").text
    assert 'type="password"' in page and "data-theme-toggle" in page and "/static/theme.js" in page
    assert 'name="subject"' not in page


def test_demo_account_buttons_fill_email_and_password(client):
    html = client.get("/login").text
    assert 'data-fill-email="alice.martin@northwindpay.example" data-fill-password="Northwind-Demo-2026"' in html
