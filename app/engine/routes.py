"""Generic HTTP surface for every manifest-driven queue app."""
from __future__ import annotations

import csv
import io
import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from ..db import transaction
from ..deps import User, audit, client_ip, current_user, deny, get_db
from . import service, store
from .manifest import Manifest, resolve_token
from .render import mask

router = APIRouter(prefix="/apps")
PAGE_SIZE = 25
SORTABLE = {"created_at", "updated_at", "priority", "id", "state"}


def get_manifest(request: Request, app_key: str) -> Manifest:
    m = request.app.state.manifests.get(app_key)
    if m is None:
        raise HTTPException(404, f"unknown app {app_key}")
    return m


def actor_of(user: User, request: Request) -> service.Actor:
    return service.Actor(user.subject, user.email, user.name, user.role, client_ip(request))


def guard_view(db, request: Request, user: User, m: Manifest) -> None:
    if user.role not in m.view_roles:
        raise deny(db, request, user, f"role {user.role} cannot view {m.key}")


def run(db, request: Request, user: User, fn):
    try:
        return fn()
    except service.EngineForbidden as exc:
        raise deny(db, request, user, str(exc))
    except service.EngineError as exc:
        raise HTTPException(exc.status_code, str(exc))


def flash(request: Request, kind: str, msg: str) -> None:
    request.session["flash"] = [kind, msg]


def _number(p: dict, key: str) -> float:
    try:
        return float(p[key])
    except ValueError:
        raise HTTPException(400, f"'{key}' must be a number")


def query_items(db: sqlite3.Connection, m: Manifest, params: dict, user: User, limit: int | None = PAGE_SIZE):
    p = {k: resolve_token(str(v)) for k, v in params.items() if v not in (None, "")}
    where, args = ["app = ?"], [m.key]
    if p.get("state"):
        where.append("state = ?")
        args.append(p["state"])
    if p.get("open") == "1":
        where.append(f"state IN ({','.join('?' * len(m.open_states))})")
        args.extend(m.open_states)
    if p.get("q"):
        where.append("(id LIKE ? OR json_extract(data, ?) LIKE ?)")
        args.extend([f"%{p['q']}%", f"$.{m.title_field}", f"%{p['q']}%"])
    if p.get("assignee") == "me":
        where.append("assignee = ?")
        args.append(user.subject)
    if p.get("pending") == "1":
        where.append("id IN (SELECT item_id FROM decisions WHERE status = 'open')")
    if p.get("risk_min"):
        where.append("priority >= ?")
        args.append(int(_number(p, "risk_min")))
    for f in m.fields:
        path = f"$.{f.name}"
        if f.type in ("date",):
            if p.get(f"f_{f.name}_from"):
                where.append("json_extract(data, ?) >= ?")
                args.extend([path, p[f"f_{f.name}_from"]])
            if p.get(f"f_{f.name}_to"):
                where.append("json_extract(data, ?) <= ?")
                args.extend([path, p[f"f_{f.name}_to"]])
        elif f.type in ("number", "money", "score"):
            for suffix, op in (("_min", ">="), ("_max", "<=")):
                if p.get(f"f_{f.name}{suffix}"):
                    where.append(f"CAST(json_extract(data, ?) AS REAL) {op} ?")
                    args.extend([path, _number(p, f"f_{f.name}{suffix}")])
        elif p.get(f"f_{f.name}"):
            if f.type == "enum":
                where.append("json_extract(data, ?) = ?")
                args.extend([path, p[f"f_{f.name}"]])
            else:
                where.append("json_extract(data, ?) LIKE ?")
                args.extend([path, f"%{p[f'f_{f.name}']}%"])
    clause = " AND ".join(where)
    sort = p.get("sort", "priority")
    direction = "ASC" if p.get("dir") == "asc" else "DESC"
    if sort in SORTABLE:
        order = f"{sort} {direction}"
    elif any(f.name == sort for f in m.fields):
        order = f"json_extract(data, '$.{sort}') {direction}"
    else:
        order = "priority DESC"
    total = db.execute(f"SELECT COUNT(*) FROM items WHERE {clause}", args).fetchone()[0]
    page = max(int(_number(p, "page")) if p.get("page") else 1, 1)
    sql = f"SELECT * FROM items WHERE {clause} ORDER BY {order}, id"
    if limit:
        sql += f" LIMIT {int(limit)} OFFSET {(page - 1) * int(limit)}"
    rows = [store._row_to_item(r) for r in db.execute(sql, args)]
    return rows, total, page


def people(db: sqlite3.Connection) -> dict[str, str]:
    return {r["subject"]: r["name"] for r in db.execute("SELECT subject, name FROM users")}


@router.get("/{app_key}")
def list_items(request: Request, app_key: str, user: User = Depends(current_user), db=Depends(get_db)):
    from ..web import render
    m = get_manifest(request, app_key)
    guard_view(db, request, user, m)
    params = dict(request.query_params)
    items, total, page = query_items(db, m, params, user)
    counts = {r["state"]: r["n"] for r in db.execute(
        "SELECT state, COUNT(*) AS n FROM items WHERE app = ? GROUP BY state", (m.key,))}
    pending_ids = {r["item_id"]: r for r in db.execute(
        "SELECT d.* FROM decisions d JOIN items i ON i.id = d.item_id WHERE i.app = ? AND d.status = 'open'", (m.key,))}
    awaiting_me = sum(1 for d in pending_ids.values()
                      if d["proposed_by"] != user.subject and user.role in m.transition(d["transition"]).approve_roles
                      and not (m.transition(d["transition"]).not_by_creator and store.creator(db, d["item_id"]) == user.subject))
    base_q = {k: v for k, v in params.items() if k != "page"}
    return render(request, "app_list.html", {
        "m": m, "items": items, "total": total, "page": page, "page_size": PAGE_SIZE, "params": params,
        "counts": counts, "pending": pending_ids, "awaiting_me": awaiting_me, "people": people(db),
        "base_q": base_q, "tallies": store.tallies(db, m.key) if m.voting else {}, "can_create": user.role in m.create_roles, "can_export": user.role in m.export_roles,
    }, active=m.key)


@router.get("/{app_key}/export.csv")
def export_csv(request: Request, app_key: str, user: User = Depends(current_user), db=Depends(get_db)):
    m = get_manifest(request, app_key)
    guard_view(db, request, user, m)
    if user.role not in m.export_roles:
        raise deny(db, request, user, f"role {user.role} cannot export {m.key}")
    params = dict(request.query_params)
    items, total, _ = query_items(db, m, params, user, limit=5000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "state", *[f.name for f in m.fields], "updated_at"])
    for it in items:
        vals = []
        for f in m.fields:
            v = it.data.get(f.name, "")
            v = mask(v) if (f.pii and v) else v
            s = str(v)
            vals.append("'" + s if s[:1] in ("=", "+", "-", "@") else s)  # CSV formula-injection guard
        w.writerow([it.id, it.state, *vals, it.updated_at])
    with transaction(db):
        audit(db, request, user, "data.exported", entity_type="app", entity_id=m.key,
              details={"rows": len(items), "filters": params, "pii": "masked"})
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"content-disposition": f'attachment; filename="{m.key}-export.csv"'})


@router.get("/{app_key}/new")
def new_form(request: Request, app_key: str, user: User = Depends(current_user), db=Depends(get_db)):
    from ..web import render
    m = get_manifest(request, app_key)
    guard_view(db, request, user, m)
    if user.role not in m.create_roles:
        raise deny(db, request, user, f"role {user.role} cannot create {m.key} items")
    return render(request, "app_new.html", {"m": m, "values": {}, "error": None}, active=m.key)


@router.post("/{app_key}/new")
async def create_item(request: Request, app_key: str, user: User = Depends(current_user), db=Depends(get_db)):
    m = get_manifest(request, app_key)
    guard_view(db, request, user, m)
    form = await request.form()
    data = {f.name: form.get(f.name) for f in m.input_fields}
    item = run(db, request, user, lambda: service.create(db, m, actor_of(user, request), data))
    flash(request, "ok", f"{item.id} created.")
    return RedirectResponse(f"/apps/{m.key}/{item.id}", status_code=303)


def _detail_ctx(db, request: Request, user: User, m: Manifest, item_id: str, revealed: bool = False) -> dict:
    item = store.get_item(db, m.key, item_id)
    if item is None:
        raise HTTPException(404, f"{item_id} not found")
    decision = store.open_decision(db, item.id)
    actor = actor_of(user, request)
    votes = store.votes_for(db, item.id) if m.voting else []
    tally = store.vote_tally(db, item.id) if m.voting else {"up": 0, "down": 0}
    can_vote, vote_block = False, None
    if m.voting and user.role in m.voting.roles and item.state in m.voting.states:
        if store.creator(db, item.id) == user.subject:
            vote_block = "You proposed this item: votes must come from other people."
        elif any(v["voter"] == user.subject for v in votes):
            vote_block = "You have voted. Votes are final."
        else:
            can_vote = True
    acts = service.available(m, item, actor, decision)
    if any(t.not_by_creator for t in m.transitions) and store.creator(db, item.id) == user.subject:
        acts["transitions"] = [t for t in acts["transitions"] if not t.not_by_creator]
        if decision is not None and m.transition(decision["transition"]).not_by_creator and acts["approve"]:
            acts["approve"] = False
            acts["approve_block"] = "You created this item: someone else must approve it (segregation of duties). You can still reject the proposal."
    return {
        "votes": votes, "tally": tally, "can_vote": can_vote, "vote_block": vote_block,
        "quorum_met": m.voting.passed(tally) if m.voting else True,
        "m": m, "item": item, "decision": decision, "acts": acts,
        "comments": store.comments_for(db, item.id), "history": store.history_for(db, item.id),
        "decisions": store.decisions_for(db, item.id), "intact": store.item_integrity(db, item),
        "revealed": revealed, "people": people(db),
    }


@router.get("/{app_key}/{item_id}")
def detail(request: Request, app_key: str, item_id: str, user: User = Depends(current_user), db=Depends(get_db)):
    from ..web import render
    m = get_manifest(request, app_key)
    guard_view(db, request, user, m)
    return render(request, "app_detail.html", _detail_ctx(db, request, user, m, item_id), active=m.key)


@router.post("/{app_key}/{item_id}/reveal")
def reveal(request: Request, app_key: str, item_id: str, user: User = Depends(current_user), db=Depends(get_db)):
    from ..web import render
    m = get_manifest(request, app_key)
    guard_view(db, request, user, m)
    if user.role not in m.reveal_roles:
        raise deny(db, request, user, f"role {user.role} cannot reveal PII on {m.key}")
    ctx = _detail_ctx(db, request, user, m, item_id, revealed=True)
    with transaction(db):
        audit(db, request, user, "pii.revealed", entity_type="item", entity_id=item_id,
              details={"app": m.key, "fields": [f.name for f in m.fields if f.pii]})
    ctx["history"] = store.history_for(db, item_id)
    return render(request, "app_detail.html", ctx, active=m.key)


async def _form(request: Request) -> dict:
    form = await request.form()
    return {k: str(v) for k, v in form.items()}


@router.post("/{app_key}/{item_id}/transition/{name}")
async def do_transition(request: Request, app_key: str, item_id: str, name: str,
                        user: User = Depends(current_user), db=Depends(get_db)):
    m = get_manifest(request, app_key)
    guard_view(db, request, user, m)
    f = await _form(request)
    result = run(db, request, user, lambda: service.transition(
        db, m, actor_of(user, request), item_id, name, f.get("comment", ""), f.get("version") or None))
    label = m.transition(name).label
    flash(request, "ok", f"'{label}' proposed: a second person must approve it." if result == "proposed"
          else f"'{label}' applied.")
    return RedirectResponse(f"/apps/{m.key}/{item_id}", status_code=303)


@router.post("/{app_key}/{item_id}/decision/{decision_id}/{verb}")
async def do_decision(request: Request, app_key: str, item_id: str, decision_id: int, verb: str,
                      user: User = Depends(current_user), db=Depends(get_db)):
    m = get_manifest(request, app_key)
    guard_view(db, request, user, m)
    f = await _form(request)
    a = actor_of(user, request)
    fns = {
        "approve": lambda: service.approve(db, m, a, item_id, decision_id, f.get("comment", "")),
        "reject": lambda: service.reject(db, m, a, item_id, decision_id, f.get("comment", "")),
        "withdraw": lambda: service.withdraw(db, m, a, item_id, decision_id),
    }
    if verb not in fns:
        raise HTTPException(404, "unknown decision action")
    run(db, request, user, fns[verb])
    flash(request, "ok", {"approve": "Decision approved and applied.", "reject": "Proposal rejected.",
                          "withdraw": "Proposal withdrawn."}[verb])
    return RedirectResponse(f"/apps/{m.key}/{item_id}", status_code=303)


@router.post("/{app_key}/{item_id}/vote")
async def do_vote(request: Request, app_key: str, item_id: str, user: User = Depends(current_user),
                  db=Depends(get_db)):
    m = get_manifest(request, app_key)
    guard_view(db, request, user, m)
    f = await _form(request)
    run(db, request, user, lambda: service.vote(db, m, actor_of(user, request), item_id, f.get("vote", ""),
                                                f.get("comment", "")))
    flash(request, "ok", "Vote recorded. Votes are final and audited.")
    return RedirectResponse(f"/apps/{m.key}/{item_id}", status_code=303)


@router.post("/{app_key}/{item_id}/comment")
async def do_comment(request: Request, app_key: str, item_id: str, user: User = Depends(current_user),
                     db=Depends(get_db)):
    m = get_manifest(request, app_key)
    guard_view(db, request, user, m)
    f = await _form(request)
    run(db, request, user, lambda: service.comment(db, m, actor_of(user, request), item_id, f.get("body", "")))
    flash(request, "ok", "Comment added.")
    return RedirectResponse(f"/apps/{m.key}/{item_id}#comments", status_code=303)


@router.post("/{app_key}/{item_id}/claim")
async def do_claim(request: Request, app_key: str, item_id: str, user: User = Depends(current_user),
                   db=Depends(get_db)):
    m = get_manifest(request, app_key)
    guard_view(db, request, user, m)
    f = await _form(request)
    release = f.get("release") == "1"
    run(db, request, user, lambda: service.claim(db, m, actor_of(user, request), item_id, release=release))
    flash(request, "ok", "Released." if release else "Assigned to you.")
    return RedirectResponse(f"/apps/{m.key}/{item_id}", status_code=303)


def json_details(raw: str) -> dict:
    try:
        return json.loads(raw)
    except ValueError:
        return {}
