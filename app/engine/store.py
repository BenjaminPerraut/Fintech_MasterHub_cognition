"""Generic item storage for every queue app, plus DB-level guard rails."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, replace

from ..audit import canonical, utcnow
from ..db import register_schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           TEXT PRIMARY KEY,
    app          TEXT NOT NULL,
    state        TEXT NOT NULL,
    data         TEXT NOT NULL,
    priority     INTEGER NOT NULL DEFAULT 0,
    assignee     TEXT,
    version      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_items_app_state ON items(app, state);
CREATE TRIGGER IF NOT EXISTS items_no_delete BEFORE DELETE ON items
BEGIN SELECT RAISE(ABORT, 'items cannot be deleted; move them to a terminal state'); END;

CREATE TABLE IF NOT EXISTS decisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id          TEXT NOT NULL REFERENCES items(id),
    transition       TEXT NOT NULL,
    proposed_by      TEXT NOT NULL,
    proposed_by_name TEXT NOT NULL,
    proposed_role    TEXT NOT NULL,
    proposed_at      TEXT NOT NULL,
    comment          TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','approved','rejected','withdrawn')),
    decided_by       TEXT,
    decided_by_name  TEXT,
    decided_at       TEXT,
    decision_comment TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_open_decision ON decisions(item_id) WHERE status = 'open';
-- Four-eyes, enforced a second time by the database itself.
CREATE TRIGGER IF NOT EXISTS decisions_four_eyes BEFORE UPDATE ON decisions
WHEN NEW.status = 'approved' AND NEW.decided_by = OLD.proposed_by
BEGIN SELECT RAISE(ABORT, 'four-eyes violation: proposer cannot approve'); END;
CREATE TRIGGER IF NOT EXISTS decisions_closed_immutable BEFORE UPDATE ON decisions
WHEN OLD.status <> 'open'
BEGIN SELECT RAISE(ABORT, 'closed decisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;

CREATE TABLE IF NOT EXISTS comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     TEXT NOT NULL REFERENCES items(id),
    author      TEXT NOT NULL,
    author_name TEXT NOT NULL,
    author_role TEXT NOT NULL,
    body        TEXT NOT NULL,
    ts          TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS comments_no_update BEFORE UPDATE ON comments
BEGIN SELECT RAISE(ABORT, 'comments are append-only'); END;
CREATE TRIGGER IF NOT EXISTS comments_no_delete BEFORE DELETE ON comments
BEGIN SELECT RAISE(ABORT, 'comments are append-only'); END;

CREATE TABLE IF NOT EXISTS votes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id    TEXT NOT NULL REFERENCES items(id),
    voter      TEXT NOT NULL,
    voter_name TEXT NOT NULL,
    voter_role TEXT NOT NULL,
    vote       TEXT NOT NULL CHECK (vote IN ('up','down')),
    comment    TEXT NOT NULL DEFAULT '',
    ts         TEXT NOT NULL,
    UNIQUE (item_id, voter)
);
CREATE TRIGGER IF NOT EXISTS votes_no_update BEFORE UPDATE ON votes
BEGIN SELECT RAISE(ABORT, 'votes are final and append-only'); END;
CREATE TRIGGER IF NOT EXISTS votes_no_delete BEFORE DELETE ON votes
BEGIN SELECT RAISE(ABORT, 'votes are final and append-only'); END;
"""
register_schema(SCHEMA)


@dataclass(frozen=True)
class Item:
    id: str
    app: str
    state: str
    data: dict
    priority: int
    assignee: str | None
    version: int
    created_at: str
    updated_at: str
    content_hash: str

    def get(self, key, default=None):
        return self.data.get(key, default)


def content_hash(item: Item) -> str:
    body = canonical({"id": item.id, "app": item.app, "state": item.state, "data": item.data,
                      "priority": item.priority, "assignee": item.assignee, "version": item.version})
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _row_to_item(row: sqlite3.Row) -> Item:
    return Item(row["id"], row["app"], row["state"], json.loads(row["data"]), row["priority"], row["assignee"],
                row["version"], row["created_at"], row["updated_at"], row["content_hash"])


def get_item(conn: sqlite3.Connection, app: str, item_id: str) -> Item | None:
    row = conn.execute("SELECT * FROM items WHERE app = ? AND id = ?", (app, item_id)).fetchone()
    return _row_to_item(row) if row else None


def next_id(conn: sqlite3.Connection, app: str, prefix: str) -> str:
    n = conn.execute("SELECT COUNT(*) FROM items WHERE app = ?", (app,)).fetchone()[0]
    return f"{prefix}-{n + 1:05d}"


def insert_item(conn: sqlite3.Connection, *, app: str, item_id: str, state: str, data: dict, priority: int,
                created_at: str | None = None) -> Item:
    now = utcnow()
    created = created_at or now
    item = Item(item_id, app, state, data, priority, None, 1, created, now, "")
    item = replace(item, content_hash=content_hash(item))
    conn.execute(
        "INSERT INTO items (id, app, state, data, priority, assignee, version, created_at, updated_at, content_hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (item.id, app, state, json.dumps(data, sort_keys=True), priority, None, 1, created, now, item.content_hash))
    return item


def save_item(conn: sqlite3.Connection, old: Item, **changes) -> Item:
    """Write a new version. The WHERE on version makes concurrent edits fail instead of overwrite."""
    new = replace(old, **changes, version=old.version + 1, updated_at=utcnow())
    new = replace(new, content_hash=content_hash(new))
    cur = conn.execute(
        "UPDATE items SET state = ?, data = ?, priority = ?, assignee = ?, version = ?, updated_at = ?, content_hash = ?"
        " WHERE id = ? AND version = ?",
        (new.state, json.dumps(new.data, sort_keys=True), new.priority, new.assignee, new.version, new.updated_at,
         new.content_hash, old.id, old.version))
    if cur.rowcount != 1:
        raise RuntimeError("concurrent modification")
    return new


def open_decision(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM decisions WHERE item_id = ? AND status = 'open'", (item_id,)).fetchone()


def decisions_for(conn: sqlite3.Connection, item_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM decisions WHERE item_id = ? ORDER BY id DESC", (item_id,)).fetchall()


def comments_for(conn: sqlite3.Connection, item_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM comments WHERE item_id = ? ORDER BY id ASC", (item_id,)).fetchall()


def votes_for(conn: sqlite3.Connection, item_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM votes WHERE item_id = ? ORDER BY id", (item_id,)).fetchall()


def vote_tally(conn: sqlite3.Connection, item_id: str) -> dict:
    tally = {"up": 0, "down": 0}
    for r in conn.execute("SELECT vote, COUNT(*) AS n FROM votes WHERE item_id = ? GROUP BY vote", (item_id,)):
        tally[r["vote"]] = r["n"]
    return tally


def tallies(conn: sqlite3.Connection, app: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT v.item_id, v.vote, COUNT(*) AS n FROM votes v JOIN items i ON i.id = v.item_id"
                          " WHERE i.app = ? GROUP BY v.item_id, v.vote", (app,)):
        out.setdefault(r["item_id"], {"up": 0, "down": 0})[r["vote"]] = r["n"]
    return out


def created_by(conn: sqlite3.Connection, item_id: str) -> str | None:
    """Creator according to the audit log (the source of truth), as an email."""
    row = conn.execute("SELECT actor FROM audit_log WHERE entity_type = 'item' AND entity_id = ?"
                       " AND action = 'item.created' ORDER BY id LIMIT 1", (item_id,)).fetchone()
    return row["actor"] if row else None


def creator(conn: sqlite3.Connection, item_id: str) -> str | None:
    """Creator's IdP subject (stable even if their email changes), from the item.created audit event."""
    row = conn.execute("SELECT actor, details FROM audit_log WHERE entity_type = 'item' AND entity_id = ?"
                       " AND action = 'item.created' ORDER BY id LIMIT 1", (item_id,)).fetchone()
    if row is None:
        return None
    subject = json.loads(row["details"]).get("created_by")
    if subject:
        return subject
    # Events written before created_by existed: resolve the actor's email (fail closed if unknown).
    user = conn.execute("SELECT subject FROM users WHERE email = ?", (row["actor"],)).fetchone()
    return user["subject"] if user else f"unknown:{row['actor']}"


def history_for(conn: sqlite3.Connection, item_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM audit_log WHERE entity_type = 'item' AND entity_id = ? ORDER BY id DESC",
                        (item_id,)).fetchall()


def verify_records(conn: sqlite3.Connection) -> list[dict]:
    """Detect records changed outside the engine: live row vs its own hash vs last audited snapshot."""
    last_audited: dict[str, str] = {}
    for row in conn.execute("SELECT entity_id, details FROM audit_log WHERE entity_type = 'item' ORDER BY id"):
        after = json.loads(row["details"]).get("after_hash")
        if after:
            last_audited[row["entity_id"]] = after
    issues = []
    for row in conn.execute("SELECT * FROM items ORDER BY id"):
        item = _row_to_item(row)
        actual = content_hash(item)
        if actual != item.content_hash:
            issues.append({"item": item.id, "problem": "row content does not match its stored hash (edited outside the app)"})
        elif last_audited.get(item.id) != item.content_hash:
            issues.append({"item": item.id, "problem": "current version was never recorded in the audit log"})
    return issues


def item_integrity(conn: sqlite3.Connection, item: Item) -> bool:
    row = conn.execute("SELECT details FROM audit_log WHERE entity_type = 'item' AND entity_id = ?"
                       " AND json_extract(details, '$.after_hash') IS NOT NULL ORDER BY id DESC LIMIT 1",
                       (item.id,)).fetchone()
    return content_hash(item) == item.content_hash and row is not None and \
        json.loads(row["details"])["after_hash"] == item.content_hash
