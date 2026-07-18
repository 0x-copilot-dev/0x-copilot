"""Disposable SQLite catalog index over the canonical JSONL session folders.

The index answers the *listing / lookup* surface — list conversations, list
messages, list a run's events after a sequence, latest sequence for a run — so
those queries don't fan out across the JSONL tree on every call. It stores the
full record JSON in a ``doc`` column plus a few indexed columns for filtering
and ordering; the store rehydrates the typed record from ``doc``.

It is **disposable**: every row is derivable from the JSONL folders. Deleting
``index/`` and reopening rebuilds it byte-for-byte (see ``rebuild``). The
canonical data lives only in the JSONL — losing the index never loses data.

WAL + ``synchronous=NORMAL`` per the desktop single-writer profile: durable
enough (canonical data is the JSONL anyway) and fast for interactive use.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path

from runtime_adapters.file._jsonl import JsonlIo

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    status          TEXT NOT NULL,
    deleted_at      TEXT,
    updated_at      TEXT NOT NULL,
    doc             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_conversations_scope
    ON conversations (org_id, user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    message_id      TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    deleted_at      TEXT,
    doc             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_conversation
    ON messages (org_id, conversation_id, created_at ASC);

CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    status          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    doc             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_runs_conversation
    ON runs (org_id, conversation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS events (
    run_id          TEXT NOT NULL,
    sequence_no     INTEGER NOT NULL,
    org_id          TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    doc             TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence_no)
);
"""


class CatalogIndex:
    """SQLite-backed listing index. All methods are synchronous.

    The store owns concurrency (single-writer ``asyncio.Lock``s); this class
    only owns the SQL. Reads return raw ``doc`` JSON strings — the store
    rehydrates typed records so no schema knowledge leaks here.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ----- lifecycle -----------------------------------------------------

    def connect(self) -> None:
        """Open (creating if needed) the SQLite db with WAL + NORMAL sync."""

        self._db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        self._conn = conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def _c(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("CatalogIndex used before connect()")
        return self._conn

    def is_empty(self) -> bool:
        """Return ``True`` when the catalog has no conversations indexed."""

        row = self._c.execute("SELECT COUNT(*) FROM conversations").fetchone()
        return int(row[0]) == 0

    # ----- rebuild -------------------------------------------------------

    def rebuild(
        self,
        *,
        conversations: Iterable[dict],
        messages: Iterable[dict],
        runs: Iterable[dict],
        events: Iterable[dict],
    ) -> None:
        """Wipe and repopulate every table from canonical record dicts."""

        conn = self._c
        conn.execute("DELETE FROM conversations")
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM runs")
        conn.execute("DELETE FROM events")
        for doc in conversations:
            self._insert_conversation(doc)
        for doc in messages:
            self._insert_message(doc)
        for doc in runs:
            self._insert_run(doc)
        for doc in events:
            self._insert_event(doc)
        conn.commit()

    # ----- upserts (write-through) --------------------------------------

    def upsert_conversation(self, doc: dict) -> None:
        self._insert_conversation(doc)
        self._c.commit()

    def upsert_message(self, doc: dict) -> None:
        self._insert_message(doc)
        self._c.commit()

    def upsert_run(self, doc: dict) -> None:
        self._insert_run(doc)
        self._c.commit()

    def insert_events(self, docs: Sequence[dict]) -> None:
        for doc in docs:
            self._insert_event(doc)
        self._c.commit()

    def _insert_conversation(self, doc: dict) -> None:
        self._c.execute(
            "INSERT OR REPLACE INTO conversations"
            " (conversation_id, org_id, user_id, status, deleted_at, updated_at, doc)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                doc["conversation_id"],
                doc["org_id"],
                doc["user_id"],
                doc.get("status", "active"),
                doc.get("deleted_at"),
                doc["updated_at"],
                JsonlIo.dumps(doc),
            ),
        )

    def _insert_message(self, doc: dict) -> None:
        self._c.execute(
            "INSERT OR REPLACE INTO messages"
            " (message_id, org_id, conversation_id, created_at, deleted_at, doc)"
            " VALUES (?,?,?,?,?,?)",
            (
                doc["message_id"],
                doc["org_id"],
                doc["conversation_id"],
                doc["created_at"],
                doc.get("deleted_at"),
                JsonlIo.dumps(doc),
            ),
        )

    def _insert_run(self, doc: dict) -> None:
        self._c.execute(
            "INSERT OR REPLACE INTO runs"
            " (run_id, org_id, conversation_id, status, created_at, doc)"
            " VALUES (?,?,?,?,?,?)",
            (
                doc["run_id"],
                doc["org_id"],
                doc["conversation_id"],
                doc.get("status", "queued"),
                doc["created_at"],
                JsonlIo.dumps(doc),
            ),
        )

    def _insert_event(self, doc: dict) -> None:
        # ``org_id`` is an index-only column: ``RuntimeEventEnvelope`` forbids
        # extra fields, so the stored ``doc`` must be the pure envelope JSON.
        org_id = doc.get("org_id", "")
        envelope_doc = {key: value for key, value in doc.items() if key != "org_id"}
        self._c.execute(
            "INSERT OR REPLACE INTO events"
            " (run_id, sequence_no, org_id, conversation_id, doc)"
            " VALUES (?,?,?,?,?)",
            (
                envelope_doc["run_id"],
                int(envelope_doc["sequence_no"]),
                org_id,
                envelope_doc["conversation_id"],
                JsonlIo.dumps(envelope_doc),
            ),
        )

    # ----- queries -------------------------------------------------------

    def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int,
        include_archived: bool,
        include_deleted: bool,
    ) -> list[str]:
        sql = "SELECT doc FROM conversations WHERE org_id=? AND user_id=?"
        params: list[object] = [org_id, user_id]
        if not include_archived:
            sql += " AND status != 'archived'"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return [row[0] for row in self._c.execute(sql, params).fetchall()]

    def list_messages(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
        include_deleted: bool,
    ) -> list[str]:
        sql = "SELECT doc FROM messages WHERE org_id=? AND conversation_id=?"
        params: list[object] = [org_id, conversation_id]
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        sql += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)
        return [row[0] for row in self._c.execute(sql, params).fetchall()]

    def list_events_after(self, *, run_id: str, after_sequence: int) -> list[str]:
        rows = self._c.execute(
            "SELECT doc FROM events WHERE run_id=? AND sequence_no>?"
            " ORDER BY sequence_no ASC",
            (run_id, after_sequence),
        ).fetchall()
        return [row[0] for row in rows]

    def latest_sequence(self, *, run_id: str) -> int:
        row = self._c.execute(
            "SELECT MAX(sequence_no) FROM events WHERE run_id=?", (run_id,)
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0


__all__ = ("CatalogIndex",)
