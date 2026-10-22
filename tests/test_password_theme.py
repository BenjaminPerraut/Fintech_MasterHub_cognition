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


def test_lockout_after_five_failures(client):
    for _ in range(5):
        attempt(client, "sam.rivera@northwindpay.example", "nope")
    r = attempt(client, "sam.rivera@northwindpay.example", "Northwind-Demo-2026")
    assert r.status_code == 403 and "too many failed attempts" in r.text


def test_login_page_has_password_form_and_theme_toggle(client):
    page = client.get("/login").text
    assert 'type="password"' in page and "data-theme-toggle" in page and "/static/theme.js" in page
    assert 'name="subject"' not in page


def test_demo_account_buttons_fill_email_and_password(client):
    html = client.get("/login").text
    assert 'data-fill-email="alice.martin@northwindpay.example" data-fill-password="Northwind-Demo-2026"' in html
