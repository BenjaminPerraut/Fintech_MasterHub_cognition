"""Master Hub desk: top flagged issues and pending approvals across every app the user's role can see."""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, Request

from ..deps import User, get_db, require_perm
from ..engine import store
from ..web import render
from .audit import full_verify

router = APIRouter()
TOP_N = 15


def desk(db: sqlite3.Connection, manifests: dict, user: User) -> dict:
    tiles, flagged, approvals = [], [], []
    for m in manifests.values():
        if user.role not in m.view_roles:  # the desk never shows items from apps the role can't open
            continue
        states = m.open_states
        ph = ",".join("?" * len(states))
        min_p = int(m.desk.get("min_priority", 70))
        open_n = db.execute(f"SELECT COUNT(*) FROM items WHERE app = ? AND state IN ({ph})", (m.key, *states)).fetchone()[0]
        hot = [store._row_to_item(r) for r in db.execute(
            f"SELECT * FROM items WHERE app = ? AND state IN ({ph}) AND priority >= ? ORDER BY priority DESC, updated_at DESC",
            (m.key, *states, min_p))]
        reasons = [m.field(f) for f in m.desk.get("reason_fields", [])]
        flagged += [{"m": m, "item": it, "reasons": reasons} for it in hot[:TOP_N]]
        decs = db.execute("SELECT d.*, i.data AS idata FROM decisions d JOIN items i ON i.id = d.item_id"
                          " WHERE i.app = ? AND d.status = 'open' ORDER BY d.proposed_at", (m.key,)).fetchall()
        mine = [d for d in decs if d["proposed_by"] != user.subject and user.role in m.transition(d["transition"]).approve_roles]
        approvals += [{"m": m, "d": d, "title": json.loads(d["idata"]).get(m.title_field, d["item_id"])} for d in mine]
        votes = 0
        if m.voting and user.role in m.voting.roles:
            vph = ",".join("?" * len(m.voting.states))
            for r in db.execute(f"SELECT id FROM items WHERE app = ? AND state IN ({vph})", (m.key, *m.voting.states)):
                if store.creator(db, r["id"]) != user.subject and not db.execute(
                        "SELECT 1 FROM votes WHERE item_id = ? AND voter = ?", (r["id"], user.subject)).fetchone():
                    votes += 1
        tiles.append({"m": m, "open": open_n, "hot": len(hot), "pending": len(decs), "mine": len(mine), "votes": votes})
    flagged.sort(key=lambda f: (f["item"].priority, f["item"].updated_at), reverse=True)
    return {"tiles": tiles, "flagged": flagged[:TOP_N], "approvals": approvals}


@router.get("/")
def hub(request: Request, user: User = Depends(require_perm("hub:view")), db: sqlite3.Connection = Depends(get_db)):
    chain = full_verify(db) if user.can("audit:verify") else None
    recent = db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 8").fetchall() if user.can("audit:read") else []
    return render(request, "hub.html", {"chain": chain, "recent": recent, **desk(db, request.app.state.manifests, user)},
                  active="hub")


@router.get("/healthz")
def healthz():
    return {"status": "ok"}
