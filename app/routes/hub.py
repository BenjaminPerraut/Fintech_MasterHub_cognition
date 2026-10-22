from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request

from ..deps import User, get_db, require_perm
from ..web import render
from .audit import full_verify

router = APIRouter()


@router.get("/")
def hub(request: Request, user: User = Depends(require_perm("hub:view")), db: sqlite3.Connection = Depends(get_db)):
    chain = full_verify(db) if user.can("audit:verify") else None
    recent = db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 8").fetchall() if user.can("audit:read") else []
    return render(request, "hub.html", {"chain": chain, "recent": recent}, active="hub")


@router.get("/healthz")
def healthz():
    return {"status": "ok"}
