from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request

from ..audit import verify_chain
from ..db import transaction
from ..deps import User, audit, get_db, require_perm
from ..web import render

router = APIRouter(prefix="/audit")

PAGE_SIZE = 50


@router.get("")
def audit_log(request: Request, actor: str = "", action: str = "", entity_type: str = "", entity_id: str = "",
              date_from: str = "", date_to: str = "", page: int = 1,
              user: User = Depends(require_perm("audit:read")), db: sqlite3.Connection = Depends(get_db)):
    where, params = [], []
    for col, val in (("actor", actor), ("action", action), ("entity_type", entity_type), ("entity_id", entity_id)):
        if val:
            where.append(f"{col} LIKE ?")
            params.append(f"%{val}%")
    if date_from:
        where.append("ts >= ?")
        params.append(date_from)
    if date_to:
        where.append("ts < date(?, '+1 day')")
        params.append(date_to)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.execute(f"SELECT COUNT(*) FROM audit_log {clause}", params).fetchone()[0]
    page = max(page, 1)
    rows = db.execute(f"SELECT * FROM audit_log {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                      [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
    actions = [r[0] for r in db.execute("SELECT DISTINCT action FROM audit_log ORDER BY action")]
    filters = dict(actor=actor, action=action, entity_type=entity_type, entity_id=entity_id,
                   date_from=date_from, date_to=date_to)
    return render(request, "audit.html", {"rows": rows, "total": total, "page": page, "page_size": PAGE_SIZE,
                                          "filters": filters, "actions": actions}, active="audit")


@router.get("/verify")
def audit_verify(request: Request, user: User = Depends(require_perm("audit:verify")),
                 db: sqlite3.Connection = Depends(get_db)):
    result = verify_chain(db)
    with transaction(db):
        audit(db, request, user, "audit.verified",
              details={"ok": result.ok, "count": result.count, "head": result.head_hash,
                       "first_bad_id": result.first_bad_id})
    return render(request, "audit_verify.html", {"result": result}, active="verify")
