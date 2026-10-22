import re

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app


@pytest.fixture()
def app(tmp_path):
    settings = load_settings(db_path=str(tmp_path / "test.db"), secret_key="test-secret-key-not-for-prod", seed=False, sandbox=True)
    return create_app(settings)


@pytest.fixture()
def seeded_app(tmp_path):
    settings = load_settings(db_path=str(tmp_path / "seeded.db"), secret_key="test-secret-key-not-for-prod", seed=True, sandbox=True)
    return create_app(settings)


@pytest.fixture()
def sclient(seeded_app):
    with TestClient(seeded_app) as c:
        yield c


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


def csrf_from(client, path="/login"):
    html = client.get(path).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, f"no csrf token on {path}"
    return m.group(1)


def login(client, subject):
    token = csrf_from(client)
    r = client.post("/auth/callback", data={"subject": subject, "csrf_token": token, "next": "/"},
                    follow_redirects=False)
    return r


def post(client, path, data=None, follow_redirects=False):
    token = csrf_from(client, "/")
    return client.post(path, data={**(data or {}), "csrf_token": token}, follow_redirects=follow_redirects)


ALICE, KARIM, OLIVIA, OMAR, SAM, ERIN, ETHAN, ADA, MAX = (f"u-100{i}" for i in range(1, 10))
