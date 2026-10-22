import re

import pytest

from app.db import connect
from app.engine.manifest import ManifestError, parse_manifest
from tests.conftest import login, post


def section(html, start, end):
    return html.split(start, 1)[1].split(end, 1)[0] if start in html else ""


def test_desk_ops_lead_sees_flags_across_apps_and_approvals(sclient):
    login(sclient, "u-1003")  # Olivia, Ops Lead
    html = sclient.get("/").text
    flagged = section(html, 'id="flagged"', "</table>")
    for prefix in ("KYC-", "RF-", "FRD-", "TRX-"):
        assert prefix in flagged
    approvals = section(html, 'id="approvals"', "</table>")
    assert "RF-" in approvals and "Approve refund" in approvals


def test_desk_never_leaks_apps_the_role_cannot_open(sclient):
    login(sclient, "u-1006")  # engineer: flags only
    html = sclient.get("/").text
    assert "FF-" in section(html, 'id="flagged"', "</table>")
    for prefix in ("KYC-", "RF-", "FRD-", "TRX-"):
        assert prefix not in html
    post(sclient, "/logout")
    login(sclient, "u-1008")  # admin: flags only, and has not voted on every open flag yet
    html = sclient.get("/").text
    assert "need your vote" in html and "KYC-" not in html


def test_desk_never_offers_your_own_proposals(sclient, seeded_app):
    conn = connect(seeded_app.state.settings.db_path)
    mine = [r[0] for r in conn.execute("SELECT item_id FROM decisions WHERE status='open' AND proposed_by='u-1001'")]
    assert mine
    login(sclient, "u-1001")  # Alice proposed these
    approvals = section(sclient.get("/").text, 'id="approvals"', "</table>")
    for item_id in mine:
        assert f"/{item_id}\"" not in approvals
    post(sclient, "/logout")
    login(sclient, "u-1002")  # Karim, the other analyst, is asked to review the KYC ones
    approvals = section(sclient.get("/").text, 'id="approvals"', "</table>")
    kyc_mine = [i for i in mine if i.startswith("KYC-")]
    assert kyc_mine and any(f"/{i}\"" in approvals for i in kyc_mine)


def test_desk_support_has_no_approval_queue(sclient):
    login(sclient, "u-1005")
    html = sclient.get("/").text
    assert 'id="approvals"' not in html and 'id="flagged"' in html


def test_desk_reason_fields_validated():
    with pytest.raises(ManifestError, match="desk"):
        parse_manifest({"app": "x", "title": "X", "id_prefix": "X", "initial": "a", "states": {"a": {}},
                        "fields": [{"name": "n"}], "desk": {"reason_fields": ["missing"]}})


def test_every_manifest_declares_desk_and_core_controls(seeded_app):
    for m in seeded_app.state.manifests.values():
        assert m.desk.get("reason_fields"), m.key
        assert m.view_roles and "admin" not in (m.reveal_roles | m.export_roles) or m.key == "flags", m.key
        assert any(t.four_eyes or t.four_eyes_if for t in m.transitions), f"{m.key} has no four-eyes decision"
