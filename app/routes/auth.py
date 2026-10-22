from __future__ import annotations

import sqlite3
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..audit import utcnow
from ..auth.provider import AuthenticationError
from ..auth.rbac import RoleResolutionError, role_from_groups
from ..db import transaction
from ..deps import User, audit, current_user, get_db
from ..security import csrf_token
from ..web import render

router = APIRouter()


def safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


@router.get("/login")
def login_page(request: Request, next: str = "/"):
    idp = request.app.state.idp
    return render(request, "login.html", {"idp": idp.login_context(), "next": safe_next(next), "error": None})


@router.post("/auth/callback")
async def auth_callback(request: Request, db: sqlite3.Connection = Depends(get_db)):
    idp = request.app.state.idp
    form = await request.form()
    nxt = safe_next(str(form.get("next", "/")))
    identity = None
    try:
        identity = await idp.authenticate(request)
        role = role_from_groups(identity.groups)
    except (AuthenticationError, RoleResolutionError) as exc:
        actor = identity.email if identity else "anonymous"
        with transaction(db):
            audit(db, request, None, "auth.login_refused", actor=actor, details={"reason": str(exc)})
        return render(request, "login.html", {"idp": idp.login_context(), "next": nxt, "error": str(exc)},
                      status_code=403)

    with transaction(db):
        row = db.execute("SELECT * FROM users WHERE subject = ?", (identity.subject,)).fetchone()
        if row is not None and not row["active"]:
            audit(db, request, None, "auth.login_refused", actor=identity.email,
                  details={"reason": "account deactivated in Master Hub"})
            refused = True
        else:
            refused = False
            now = utcnow()
            if row is None:
                db.execute("INSERT INTO users (subject, email, name, role, active, created_at, last_login)"
                           " VALUES (?, ?, ?, ?, 1, ?, ?)",
                           (identity.subject, identity.email, identity.name, role.value, now, now))
                audit(db, request, None, "user.provisioned", actor=identity.email, actor_role=role.value,
                      entity_type="user", entity_id=identity.subject, details={"role": role.value, "via": idp.key})
            else:
                if row["role"] != role.value:
                    audit(db, request, None, "user.role_synced", actor=identity.email, actor_role=role.value,
                          entity_type="user", entity_id=identity.subject,
                          details={"from": row["role"], "to": role.value, "via": idp.key})
                db.execute("UPDATE users SET email = ?, name = ?, role = ?, last_login = ? WHERE subject = ?",
                           (identity.email, identity.name, role.value, now, identity.subject))
            audit(db, request, None, "auth.login", actor=identity.email, actor_role=role.value,
                  entity_type="user", entity_id=identity.subject, details={"idp": idp.key})
    if refused:
        return render(request, "login.html", {"idp": idp.login_context(), "next": nxt,
                                              "error": "account deactivated"}, status_code=403)

    request.session.clear()  # prevent session fixation
    request.session["user"] = {"sub": identity.subject, "iat": time.time()}
    csrf_token(request)
    return RedirectResponse(nxt, status_code=303)


@router.post("/logout")
def logout(request: Request, user: User = Depends(current_user), db: sqlite3.Connection = Depends(get_db)):
    with transaction(db):
        audit(db, request, user, "auth.logout", entity_type="user", entity_id=user.subject)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
