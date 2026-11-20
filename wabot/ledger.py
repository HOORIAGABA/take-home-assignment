r"""Durable message ledger - the heart of "reply exactly once".

Every inbound message is written here *synchronously, inside the whatsmeow event
callback*, before control returns to the Go side (which then sends the delivery
receipt to WhatsApp). So a message is either in the ledger, or WhatsApp still
thinks we haven't received it and will re-deliver it on the next connect.

State machine of one inbound message (key = chat + message id):

    received --(policy says no)--------------------------> ignored
    received --(reply generated & stored)---------------> ready
    ready    --(durably marked BEFORE the network call)--> sending
    sending  --(send returned an id)--------------------> sent
    sending  --(crash / send error; resolved later)-----> sent   (a delivery receipt proves it went out)
                                                    \---> ready  (no receipt after the grace period -> resend)

Re-delivered duplicates hit the PRIMARY KEY and are dropped (INSERT OR IGNORE).
The reply text is stored before sending, so a resend is byte-identical.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS inbound (
    key TEXT PRIMARY KEY, msg_id TEXT NOT NULL, chat TEXT NOT NULL, sender TEXT, sender_pn TEXT,
    from_me INTEGER NOT NULL, is_group INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT,
    ts REAL NOT NULL, push_name TEXT, raw BLOB,
    status TEXT NOT NULL, reason TEXT, reply TEXT, out_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0, send_attempts INTEGER NOT NULL DEFAULT 0,
    sending_at REAL, sent_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS inbound_status ON inbound(status, ts);
CREATE TABLE IF NOT EXISTS receipts (
    chat TEXT NOT NULL, msg_id TEXT NOT NULL, type INTEGER NOT NULL, ts REAL NOT NULL,
    PRIMARY KEY (chat, msg_id, type)
);
CREATE TABLE IF NOT EXISTS outbound (out_id TEXT PRIMARY KEY, inbound_key TEXT, chat TEXT, sent_at REAL);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, detail TEXT);
"""

ACTIONABLE = ("received", "ready", "sending")


@dataclass
class Inbound:
    key: str
    msg_id: str
    chat: str
    sender: str | None
    sender_pn: str | None
    from_me: bool
    is_group: bool
    kind: str            # text | voice | other | system
    text: str | None
    ts: float
    push_name: str | None = None
    raw: bytes | None = None


class _Res:
    def __init__(self, cur: sqlite3.Cursor):
        self.rows = cur.fetchall()
        self.rowcount = cur.rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


def chat_user(jid: str) -> str:
    """'923001234567@s.whatsapp.net' / '923001234567:12@s.whatsapp.net' -> '923001234567'."""
    return jid.split("@", 1)[0].split(":", 1)[0]


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()   # written from the Go callback thread AND the asyncio loop
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")   # an fsync per commit: cheap at chat volume
        self.db.executescript(SCHEMA)

    def _x(self, sql: str, args: tuple = ()) -> "_Res":
        with self._lock:  # rows are fetched while holding the lock
            return _Res(self.db.execute(sql, args))

    # ------------------------------------------------------------- meta / events
    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self._x("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value: Any) -> None:
        self._x("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    def event(self, kind: str, detail: str = "") -> None:
        self._x("INSERT INTO events (ts, kind, detail) VALUES (?,?,?)", (time.time(), kind, detail[:1000]))

    # ------------------------------------------------------------- inbound
    def record_inbound(self, m: Inbound) -> bool:
        """Idempotent insert. Returns True if this message is new."""
        now = time.time()
        cur = self._x(
            "INSERT OR IGNORE INTO inbound (key, msg_id, chat, sender, sender_pn, from_me, is_group, kind, text, "
            "ts, push_name, raw, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'received',?,?)",
            (m.key, m.msg_id, m.chat, m.sender, m.sender_pn, int(m.from_me), int(m.is_group), m.kind, m.text,
             m.ts, m.push_name, m.raw, now, now),
        )
        return cur.rowcount == 1

    def next_actionable(self) -> sqlite3.Row | None:
        return self._x(
            "SELECT * FROM inbound WHERE status IN ('received','ready') ORDER BY ts, created_at LIMIT 1"
        ).fetchone()

    def get(self, key: str) -> sqlite3.Row | None:
        return self._x("SELECT * FROM inbound WHERE key=?", (key,)).fetchone()

    def _set(self, key: str, expect: tuple[str, ...], **fields: Any) -> bool:
        """Compare-and-set on status, so two code paths can never both advance one message."""
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        q = f"UPDATE inbound SET {cols} WHERE key=? AND status IN ({','.join('?' * len(expect))})"
        return self._x(q, (*fields.values(), key, *expect)).rowcount == 1

    def ignore(self, key: str, reason: str) -> bool:
        return self._set(key, ("received", "ready"), status="ignored", reason=reason)

    def fail(self, key: str, reason: str) -> bool:
        return self._set(key, ("received", "ready", "sending"), status="failed", reason=reason)

    def bump_attempts(self, key: str, reason: str) -> int:
        self._x("UPDATE inbound SET attempts=attempts+1, reason=?, updated_at=? WHERE key=?",
                (reason[:500], time.time(), key))
        return self.get(key)["attempts"]

    def set_ready(self, key: str, reply: dict) -> bool:
        return self._set(key, ("received",), status="ready", reply=json.dumps(reply))

    def mark_sending(self, key: str) -> bool:
        with self._lock:
            now = time.time()
            return self.db.execute(
                "UPDATE inbound SET status='sending', sending_at=?, send_attempts=send_attempts+1, updated_at=? "
                "WHERE key=? AND status='ready'", (now, now, key)).rowcount == 1

    def mark_sent(self, key: str, out_id: str | None, chat: str) -> bool:
        with self._lock:
            now = time.time()
            self.db.execute("BEGIN IMMEDIATE")
            try:
                ok = self.db.execute(
                    "UPDATE inbound SET status='sent', out_id=?, sent_at=?, updated_at=? "
                    "WHERE key=? AND status='sending'", (out_id, now, now, key)).rowcount == 1
                if out_id:
                    self.db.execute("INSERT OR IGNORE INTO outbound VALUES (?,?,?,?)", (out_id, key, chat, now))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            return ok

    def back_to_ready(self, key: str, reason: str) -> bool:
        return self._set(key, ("sending",), status="ready", reason=reason)

    # ------------------------------------------------------------- receipts / ambiguity resolution
    def record_receipt(self, chats: list[str], msg_ids: list[str], rtype: int, ts: float) -> None:
        """`chats`: every identity of the peer we know (PN jid, LID jid) - WhatsApp may address
        the same person by either, so we index the receipt under each."""
        users = {chat_user(c) for c in chats if c}
        with self._lock:
            self.db.executemany("INSERT OR IGNORE INTO receipts VALUES (?,?,?,?)",
                                [(u, mid, rtype, ts) for u in users for mid in msg_ids])

    def stuck_sending(self, older_than_s: float) -> list[sqlite3.Row]:
        return self._x("SELECT * FROM inbound WHERE status='sending' AND sending_at <= ? ORDER BY ts",
                       (time.time() - older_than_s,)).fetchall()

    def unexplained_receipt(self, chats: list[str], since: float) -> str | None:
        """A receipt from this peer, at/after `since`, for a message id we never recorded as
        sent. If we crashed between the network send and mark_sent, this is that message.
        Receipts for the peer's *own* messages never land here (those are ours to send)."""
        users = sorted({chat_user(c) for c in chats if c})
        if not users:
            return None
        row = self._x(
            "SELECT r.msg_id FROM receipts r LEFT JOIN outbound o ON o.out_id = r.msg_id "
            f"WHERE r.chat IN ({','.join('?' * len(users))}) AND r.ts >= ? AND o.out_id IS NULL "
            "AND r.msg_id NOT IN (SELECT msg_id FROM inbound) ORDER BY r.ts LIMIT 1",
            (*users, since - 2),
        ).fetchone()
        return row[0] if row else None

    def claim_receipt(self, key: str, out_id: str, chat: str) -> bool:
        return self.mark_sent(key, out_id, chat)

    # ------------------------------------------------------------- pacing / history / stats
    def sent_since(self, seconds: float) -> int:
        return self._x("SELECT COUNT(*) FROM inbound WHERE status='sent' AND sent_at >= ?",
                       (time.time() - seconds,)).fetchone()[0]

    def counts(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self._x("SELECT status, COUNT(*) FROM inbound GROUP BY status")}
