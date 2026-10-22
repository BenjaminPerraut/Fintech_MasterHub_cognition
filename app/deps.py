"""Request-scoped dependencies: DB connection, current user, permission guards, audit helper."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Iterator

from fastapi import Depends, HTTPException, Request

from .audit import record_event
from .auth.rbac import ROLE_LABELS, Role, has_perm
from .db import connect, transaction


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


@dataclass(frozen=True)
class User:
    subject: str
    email: str
    name: str
    role: str

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(Role(self.role), self.role)

    @property
    def initials(self) -> str:
        return "".join(p[0] for p in self.name.split()[:2]).upper()

    def can(self, perm: str) -> bool:
        return has_perm(self.role, perm)


class LoginRequired(Exception):
    pass


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def current_user(request: Request, db: sqlite3.Connection = Depends(get_db)) -> User:
    sess = request.session.get("user")
    if not sess:
        raise LoginRequired()
    if time.time() - float(sess.get("iat", 0)) > request.app.state.settings.session_max_age:
        request.session.clear()
        raise LoginRequired()
    srow = db.execute("SELECT subject, revoked_at FROM sessions WHERE id = ?", (sess.get("sid"),)).fetchone()
    if srow is None or srow["revoked_at"] or srow["subject"] != sess.get("sub"):
        request.session.clear()  # logged out elsewhere, or a replayed cookie
        raise LoginRequired()
    # The DB row is the source of truth on every request: deactivation / role change is immediate.
    row = db.execute("SELECT * FROM users WHERE subject = ?", (sess.get("sub"),)).fetchone()
    if row is None or not row["active"]:
        request.session.clear()
        raise LoginRequired()
    user = User(row["subject"], row["email"], row["name"], row["role"])
    request.state.user = user
    return user


def audit(db: sqlite3.Connection, request: Request, user: User | None, action: str, *,
          entity_type: str | None = None, entity_id: str | None = None, details: dict | None = None,
          actor: str | None = None, actor_role: str | None = None) -> int:
    return record_event(
        db,
        actor=actor or (user.email if user else "anonymous"),
        actor_role=actor_role or (user.role if user else "none"),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
        ip=client_ip(request),
    )


def deny(db: sqlite3.Connection, request: Request, user: User, reason: str) -> HTTPException:
    """Record the refusal in the audit log and return the exception to raise."""
    with transaction(db):
        audit(db, request, user, "access.denied",
              details={"reason": reason, "path": request.url.path, "method": request.method})
    return HTTPException(status_code=403, detail=reason)


def require_perm(perm: str):
    def guard(request: Request, user: User = Depends(current_user), db: sqlite3.Connection = Depends(get_db)) -> User:
        if not has_perm(user.role, perm):
            raise deny(db, request, user, f"missing permission {perm}")
        return user

    guard.__name__ = f"require_perm_{perm.replace(':', '_')}"
    return guard


def require_role(*roles: Role):
    allowed = {Role(r).value for r in roles}

    def guard(request: Request, user: User = Depends(current_user), db: sqlite3.Connection = Depends(get_db)) -> User:
        if user.role not in allowed:
            raise deny(db, request, user, f"role {user.role} not in {sorted(allowed)}")
        return user

    return guard
