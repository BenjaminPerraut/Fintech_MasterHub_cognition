"""The only code path allowed to change queue items.

Every function: checks the manifest's roles server-side, runs in one transaction,
and writes exactly the audit events that describe the change.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..audit import record_event, utcnow
from ..db import transaction
from . import store
from .manifest import Manifest, TransitionDef

MAX_COMMENT = 4000


class EngineError(Exception):
    status_code = 400


class EngineForbidden(EngineError):
    status_code = 403


class EngineConflict(EngineError):
    status_code = 409


class EngineNotFound(EngineError):
    status_code = 404


@dataclass(frozen=True)
class Actor:
    subject: str
    email: str
    name: str
    role: str
    ip: str | None = None


SYSTEM = Actor("system", "system@masterhub", "Master Hub", "system")


def _audit(conn, actor: Actor, action: str, item: store.Item, **details) -> None:
    record_event(conn, actor=actor.email, actor_role=actor.role, action=action, entity_type="item",
                 entity_id=item.id, details={"app": item.app, **details}, ip=actor.ip)


def _load(conn, manifest: Manifest, item_id: str) -> store.Item:
    item = store.get_item(conn, manifest.key, item_id)
    if item is None:
        raise EngineNotFound(f"{item_id} not found")
    return item


def _clean_comment(comment: str | None, required: bool) -> str:
    comment = (comment or "").strip()
    if len(comment) > MAX_COMMENT:
        raise EngineError(f"comment longer than {MAX_COMMENT} characters")
    if required and not comment:
        raise EngineError("a justification comment is required for this action")
    return comment


def _coerce(manifest: Manifest, data: dict) -> dict:
    out = {}
    for f in manifest.fields:
        if f.name not in data or data[f.name] in (None, ""):
            if f.required:
                raise EngineError(f"{f.label} is required")
            continue
        v = data[f.name]
        if f.type in ("number", "money", "score"):
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise EngineError(f"{f.label} must be a number")
            if v < 0:
                raise EngineError(f"{f.label} cannot be negative")
            v = int(v) if f.type == "score" or float(v).is_integer() and f.type == "number" else round(v, 2)
        elif f.type == "enum" and f.options and v not in f.options:
            raise EngineError(f"{f.label} must be one of {', '.join(f.options)}")
        elif isinstance(v, str):
            v = v.strip()[:500]
        out[f.name] = v
    return out


def create(conn: sqlite3.Connection, manifest: Manifest, actor: Actor, data: dict, *, state: str | None = None,
           created_at: str | None = None, enforce_roles: bool = True) -> store.Item:
    if enforce_roles and actor.role not in manifest.create_roles:
        raise EngineForbidden(f"role {actor.role} cannot create {manifest.key} items")
    clean = _coerce(manifest, data)
    priority = int(clean.get(manifest.priority_field, 0) or 0) if manifest.priority_field else 0
    with transaction(conn):
        item = store.insert_item(conn, app=manifest.key, item_id=store.next_id(conn, manifest.key, manifest.id_prefix),
                                 state=state or manifest.initial, data=clean, priority=priority, created_at=created_at)
        _audit(conn, actor, "item.created", item, state=item.state, after_hash=item.content_hash, version=1)
    return item


def update_data(conn: sqlite3.Connection, manifest: Manifest, actor: Actor, item_id: str, changes: dict,
                reason: str) -> store.Item:
    """System-side field updates (e.g. rule engine enrichment). Recorded with before/after values."""
    with transaction(conn):
        item = _load(conn, manifest, item_id)
        data = {**item.data, **changes}
        priority = int(data.get(manifest.priority_field, 0) or 0) if manifest.priority_field else item.priority
        new = store.save_item(conn, item, data=data, priority=priority)
        _audit(conn, actor, "item.updated", new, reason=reason,
               changes={k: {"from": item.data.get(k), "to": v} for k, v in changes.items()},
               before_hash=item.content_hash, after_hash=new.content_hash, version=new.version)
    return new


def _check_version(item: store.Item, expected_version: int | None) -> None:
    if expected_version is not None and int(expected_version) != item.version:
        raise EngineConflict(f"{item.id} changed since you opened it (v{expected_version} → v{item.version}). Reload and retry.")


def _apply(conn, actor: Actor, item: store.Item, t: TransitionDef, action: str, **extra) -> store.Item:
    new = store.save_item(conn, item, state=t.to)
    _audit(conn, actor, action, new, transition=t.name, from_state=item.state, to_state=t.to,
           before_hash=item.content_hash, after_hash=new.content_hash, version=new.version, **extra)
    return new


def transition(conn: sqlite3.Connection, manifest: Manifest, actor: Actor, item_id: str, name: str,
               comment: str = "", expected_version: int | None = None) -> str:
    """Returns 'applied' or 'proposed' (four-eyes transitions only create a pending decision)."""
    try:
        t = manifest.transition(name)
    except KeyError:
        raise EngineNotFound(f"unknown action {name}")
    if actor.role not in t.roles:
        raise EngineForbidden(f"role {actor.role} cannot '{t.label}' on {manifest.key}")
    comment = _clean_comment(comment, t.require_comment or t.four_eyes)
    with transaction(conn):
        item = _load(conn, manifest, item_id)
        _check_version(item, expected_version)
        if item.state not in t.from_:
            raise EngineConflict(f"'{t.label}' is not allowed from state {item.state}")
        if store.open_decision(conn, item.id) is not None:
            raise EngineConflict(f"{item.id} has a decision awaiting second approval")
        if t.four_eyes:
            cur = conn.execute(
                "INSERT INTO decisions (item_id, transition, proposed_by, proposed_by_name, proposed_role, proposed_at, comment)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (item.id, t.name, actor.subject, actor.name, actor.role, utcnow(), comment))
            _audit(conn, actor, "item.proposed", item, transition=t.name, decision_id=cur.lastrowid,
                   from_state=item.state, to_state=t.to, comment=comment)
            return "proposed"
        if comment:
            _add_comment(conn, actor, item, comment)
        _apply(conn, actor, item, t, "item.transition", comment=comment)
        return "applied"


def _decision(conn, item: store.Item, decision_id: int) -> sqlite3.Row:
    d = store.open_decision(conn, item.id)
    if d is None or d["id"] != int(decision_id):
        raise EngineConflict("no matching open decision (already decided or withdrawn)")
    return d


def approve(conn: sqlite3.Connection, manifest: Manifest, actor: Actor, item_id: str, decision_id: int,
            comment: str = "") -> store.Item:
    comment = _clean_comment(comment, False)
    with transaction(conn):
        item = _load(conn, manifest, item_id)
        d = _decision(conn, item, decision_id)
        t = manifest.transition(d["transition"])
        if d["proposed_by"] == actor.subject:
            raise EngineForbidden("four-eyes: the person who proposed a decision cannot approve it")
        if actor.role not in t.approve_roles:
            raise EngineForbidden(f"role {actor.role} cannot approve '{t.label}'")
        if item.state not in t.from_:
            raise EngineConflict(f"item moved to {item.state}; decision is stale")
        conn.execute("UPDATE decisions SET status = 'approved', decided_by = ?, decided_by_name = ?, decided_at = ?,"
                     " decision_comment = ? WHERE id = ?", (actor.subject, actor.name, utcnow(), comment, d["id"]))
        if comment:
            _add_comment(conn, actor, item, comment)
        return _apply(conn, actor, item, t, "item.approved", decision_id=d["id"], proposed_by=d["proposed_by"],
                      proposed_comment=d["comment"], comment=comment)


def reject(conn: sqlite3.Connection, manifest: Manifest, actor: Actor, item_id: str, decision_id: int,
           comment: str = "") -> None:
    comment = _clean_comment(comment, True)
    with transaction(conn):
        item = _load(conn, manifest, item_id)
        d = _decision(conn, item, decision_id)
        t = manifest.transition(d["transition"])
        if d["proposed_by"] == actor.subject:
            raise EngineForbidden("use 'withdraw' for your own proposal")
        if actor.role not in t.approve_roles:
            raise EngineForbidden(f"role {actor.role} cannot decide on '{t.label}'")
        conn.execute("UPDATE decisions SET status = 'rejected', decided_by = ?, decided_by_name = ?, decided_at = ?,"
                     " decision_comment = ? WHERE id = ?", (actor.subject, actor.name, utcnow(), comment, d["id"]))
        _add_comment(conn, actor, item, comment)
        _audit(conn, actor, "item.proposal_rejected", item, decision_id=d["id"], transition=t.name,
               proposed_by=d["proposed_by"], comment=comment)


def withdraw(conn: sqlite3.Connection, manifest: Manifest, actor: Actor, item_id: str, decision_id: int) -> None:
    with transaction(conn):
        item = _load(conn, manifest, item_id)
        d = _decision(conn, item, decision_id)
        if d["proposed_by"] != actor.subject:
            raise EngineForbidden("only the proposer can withdraw a proposal")
        conn.execute("UPDATE decisions SET status = 'withdrawn', decided_by = ?, decided_by_name = ?, decided_at = ?"
                     " WHERE id = ?", (actor.subject, actor.name, utcnow(), d["id"]))
        _audit(conn, actor, "item.proposal_withdrawn", item, decision_id=d["id"], transition=d["transition"])


def _add_comment(conn, actor: Actor, item: store.Item, body: str) -> int:
    cur = conn.execute("INSERT INTO comments (item_id, author, author_name, author_role, body, ts) VALUES (?, ?, ?, ?, ?, ?)",
                       (item.id, actor.subject, actor.name, actor.role, body, utcnow()))
    return int(cur.lastrowid)


def comment(conn: sqlite3.Connection, manifest: Manifest, actor: Actor, item_id: str, body: str) -> None:
    if actor.role not in manifest.comment_roles:
        raise EngineForbidden(f"role {actor.role} cannot comment on {manifest.key}")
    body = _clean_comment(body, True)
    with transaction(conn):
        item = _load(conn, manifest, item_id)
        cid = _add_comment(conn, actor, item, body)
        _audit(conn, actor, "item.commented", item, comment_id=cid, body=body)


def worker_roles(manifest: Manifest) -> set[str]:
    return set().union(*(t.roles for t in manifest.transitions)) if manifest.transitions else set()


def claim(conn: sqlite3.Connection, manifest: Manifest, actor: Actor, item_id: str, release: bool = False) -> store.Item:
    if actor.role not in worker_roles(manifest):
        raise EngineForbidden(f"role {actor.role} cannot work {manifest.key} items")
    with transaction(conn):
        item = _load(conn, manifest, item_id)
        if manifest.states[item.state].terminal:
            raise EngineConflict("item is closed")
        if release:
            if item.assignee != actor.subject:
                raise EngineForbidden("you can only release items assigned to you")
            new = store.save_item(conn, item, assignee=None)
            _audit(conn, actor, "item.released", new, before_hash=item.content_hash, after_hash=new.content_hash,
                   version=new.version)
        else:
            new = store.save_item(conn, item, assignee=actor.subject)
            _audit(conn, actor, "item.assigned", new, assignee=actor.subject, previous=item.assignee,
                   before_hash=item.content_hash, after_hash=new.content_hash, version=new.version)
    return new


def available(manifest: Manifest, item: store.Item, actor: Actor, decision: sqlite3.Row | None) -> dict:
    """What the UI should offer. Purely cosmetic: every action is re-checked server-side."""
    out = {"transitions": [], "approve": False, "reject": False, "withdraw": False, "approve_block": None,
           "comment": actor.role in manifest.comment_roles, "claim": False,
           "reveal": actor.role in manifest.reveal_roles}
    if decision is not None:
        t = manifest.transition(decision["transition"])
        mine = decision["proposed_by"] == actor.subject
        out["withdraw"] = mine
        if actor.role in t.approve_roles:
            if mine:
                out["approve_block"] = "You proposed this decision: a second person must approve it (four-eyes)."
            else:
                out["approve"] = out["reject"] = True
        return out
    if not manifest.states[item.state].terminal and actor.role in worker_roles(manifest):
        out["claim"] = True
    out["transitions"] = [t for t in manifest.transitions if item.state in t.from_ and actor.role in t.roles]
    return out
