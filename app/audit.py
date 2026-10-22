"""Append-only, hash-chained audit log.

Each row stores prev_hash and hash = sha256(prev_hash || canonical_json(row)).
SQLite triggers reject UPDATE/DELETE. verify_chain() recomputes everything.
This is tamper-*evident*, not tamper-proof: see docs/DECISIONS.md (D-004).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .db import APPEND_ONLY_TRIGGERS

GENESIS_HASH = "0" * 64
HASHED_FIELDS = ("ts", "actor", "actor_role", "action", "entity_type", "entity_id", "details", "ip")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_hash(prev_hash: str, payload: dict) -> str:
    body = canonical({k: payload.get(k) for k in HASHED_FIELDS})
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def record_event(
    conn: sqlite3.Connection,
    *,
    actor: str,
    actor_role: str,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict | None = None,
    ip: str | None = None,
) -> int:
    """Append one event. Must run inside db.transaction() together with the change it describes."""
    if not conn.in_transaction:
        raise RuntimeError("record_event must be called inside a transaction")
    row = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    prev_hash = row["hash"] if row else GENESIS_HASH
    payload = {
        "ts": utcnow(),
        "actor": actor,
        "actor_role": actor_role,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "details": canonical(details or {}),
        "ip": ip,
    }
    digest = compute_hash(prev_hash, payload)
    cur = conn.execute(
        "INSERT INTO audit_log (ts, actor, actor_role, action, entity_type, entity_id, details, ip, prev_hash, hash)"
        " VALUES (:ts, :actor, :actor_role, :action, :entity_type, :entity_id, :details, :ip, :prev_hash, :hash)",
        {**payload, "prev_hash": prev_hash, "hash": digest},
    )
    return int(cur.lastrowid)


@dataclass
class VerifyResult:
    ok: bool
    count: int
    head_hash: str
    first_bad_id: int | None = None
    reason: str | None = None
    triggers_present: bool = True
    missing_triggers: list[str] = field(default_factory=list)
    checked_at: str = field(default_factory=utcnow)
    record_issues: list[dict] = field(default_factory=list)


def verify_chain(conn: sqlite3.Connection) -> VerifyResult:
    existing = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='audit_log'")
    }
    missing = [t for t in APPEND_ONLY_TRIGGERS if t not in existing]
    expected_prev = GENESIS_HASH
    last_id = 0
    count = 0
    for row in conn.execute("SELECT * FROM audit_log ORDER BY id ASC"):
        count += 1
        row_d = dict(row)
        if row_d["id"] != last_id + 1:
            return VerifyResult(False, count, expected_prev, row_d["id"],
                                f"gap in sequence after id {last_id} (row deleted?)", not missing, missing)
        if row_d["prev_hash"] != expected_prev:
            return VerifyResult(False, count, expected_prev, row_d["id"],
                                "prev_hash does not match previous row (chain broken)", not missing, missing)
        if compute_hash(row_d["prev_hash"], row_d) != row_d["hash"]:
            return VerifyResult(False, count, expected_prev, row_d["id"],
                                "row content does not match its hash (row modified)", not missing, missing)
        expected_prev = row_d["hash"]
        last_id = row_d["id"]
    ok = not missing
    reason = None if ok else "append-only triggers missing: " + ", ".join(missing)
    return VerifyResult(ok, count, expected_prev, None, reason, not missing, missing)
