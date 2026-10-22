"""Sample data only. Everything here is fictional and deterministic (fixed RNG seed)."""
from __future__ import annotations

import sqlite3

from ..audit import record_event, utcnow
from ..auth.provider import STUB_DIRECTORY
from ..auth.rbac import RoleResolutionError, role_from_groups
from ..db import transaction

SEED_IP = "seed-script"


def seed_users(conn: sqlite3.Connection) -> None:
    """Pre-provision the directory (what SCIM would do), so names resolve before first login."""
    with transaction(conn):
        for d in STUB_DIRECTORY:
            try:
                role = role_from_groups(d.groups)
            except RoleResolutionError:
                continue
            if conn.execute("SELECT 1 FROM users WHERE subject = ?", (d.subject,)).fetchone():
                continue
            conn.execute("INSERT INTO users (subject, email, name, role, active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
                         (d.subject, d.email, d.name, role.value, utcnow()))
            record_event(conn, actor="system@masterhub", actor_role="system", action="user.provisioned",
                         entity_type="user", entity_id=d.subject, details={"role": role.value, "via": "seed"},
                         ip=SEED_IP)


def seed_all(conn: sqlite3.Connection, manifests: dict) -> None:
    from . import flags, fraud, kyc, refunds
    seed_users(conn)
    for key, module in (("kyc", kyc), ("refunds", refunds), ("flags", flags)):
        if key in manifests and not conn.execute("SELECT 1 FROM items WHERE app = ? LIMIT 1", (key,)).fetchone():
            module.seed(conn, manifests[key])
    if "fraud" in manifests and not conn.execute("SELECT 1 FROM payments LIMIT 1").fetchone():
        fraud.seed(conn, manifests)
