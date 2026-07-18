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

import re
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path

from runtime_adapters.file._jsonl import JsonlIo

# Message roles whose redacted text is safe to index for search. Tool and
# system turns carry connector payloads / prompt scaffolding and are never
# indexed, so a secret parked in a tool result can never surface via search.
_SEARCHABLE_ROLES = frozenset({"user", "assistant"})

_FTS_KIND_TITLE = "title"
_FTS_KIND_MESSAGE = "message"

# Word-ish run extractor: neutralises FTS5 query syntax (quotes, ``*``, ``:``,
# ``AND``/``OR`` operators, column filters) by keeping only token characters,
# so raw user input can never form a malformed or injected MATCH expression.
_QUERY_TOKEN = re.compile(r"\w+", re.UNICODE)

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

# FTS5 lives in its own statement because ``CREATE VIRTUAL TABLE`` fails hard on
# a SQLite build without the FTS5 module. We create it separately so that a
# missing module disables *search only* (per the AC2 contract) without taking
# down the listing index or blocking any direct read/write. ``text`` is the one
# indexed column; the rest are UNINDEXED sidecars used for scoping, dedup, and
# incremental row maintenance. Only conversation title + redacted user/assistant
# message text is ever written here.
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS conversation_fts USING fts5(
    conversation_id UNINDEXED,
    org_id          UNINDEXED,
    ref_id          UNINDEXED,
    kind            UNINDEXED,
    text,
    tokenize = 'unicode61'
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
        self._fts_available = False

    # ----- lifecycle -----------------------------------------------------

    def connect(self) -> None:
        """Open (creating if needed) the SQLite db with WAL + NORMAL sync.

        The index is **disposable**: every row is derivable from the canonical
        JSONL. A crash mid-commit (or bit-rot) can leave the SQLite file torn —
        ``sqlite3.DatabaseError: file is not a database``. Because the caller
        rebuilds the index from JSONL immediately after ``connect()``, a corrupt
        file must not brick startup: we discard it and recreate an empty schema,
        exactly as if the index were missing. Canonical data is never at risk.
        """

        self._db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            conn = self._open_schema()
        except sqlite3.DatabaseError:
            self._discard_corrupt_db()
            conn = self._open_schema()
        self._conn = conn
        self._fts_available = self._try_enable_fts(conn)

    def _open_schema(self) -> sqlite3.Connection:
        """Open the db and apply the base schema; may raise on a torn file."""

        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            conn.commit()
        except sqlite3.DatabaseError:
            conn.close()
            raise
        return conn

    def _discard_corrupt_db(self) -> None:
        """Delete a torn/corrupt disposable index (plus its WAL/SHM sidecars).

        Safe because the index carries no canonical data — ``rebuild`` repopulates
        every row from the JSONL folders on the next open.
        """

        for suffix in ("", "-wal", "-shm"):
            sidecar = self._db_path.with_name(self._db_path.name + suffix)
            sidecar.unlink(missing_ok=True)

    @staticmethod
    def _try_enable_fts(conn: sqlite3.Connection) -> bool:
        """Create the FTS5 table; return ``False`` if the module is absent.

        A SQLite build without FTS5 raises here — we swallow that and leave the
        index fully functional for direct reads. Search then degrades to empty
        results (AC2: "FTS unavailability disables search only").
        """

        try:
            conn.executescript(_FTS_SCHEMA)
            conn.commit()
        except sqlite3.OperationalError:
            return False
        return True

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
        if self._fts_available:
            conn.execute("DELETE FROM conversation_fts")
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

    def delete_conversation_cascade(self, conversation_id: str) -> None:
        """Drop a conversation and every message / run / event row under it.

        Mirrors a physical session deletion in the disposable index so live
        listing reads stop returning the purged conversation without waiting
        for the next ``rebuild`` on reopen.
        """

        conn = self._c
        conn.execute("DELETE FROM events WHERE conversation_id=?", (conversation_id,))
        conn.execute("DELETE FROM runs WHERE conversation_id=?", (conversation_id,))
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
        conn.execute(
            "DELETE FROM conversations WHERE conversation_id=?", (conversation_id,)
        )
        if self._fts_available:
            conn.execute(
                "DELETE FROM conversation_fts WHERE conversation_id=?",
                (conversation_id,),
            )
        conn.commit()

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
        self._index_title(doc)

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
        self._index_message(doc)

    # ----- FTS maintenance (write-through) ------------------------------

    def _fts_replace(
        self,
        *,
        conversation_id: str,
        org_id: str,
        ref_id: str,
        kind: str,
        text: str | None,
    ) -> None:
        """Idempotently (re)index one row's searchable text.

        Drops any prior row for this ``(ref_id, kind)`` — FTS5 has no UPDATE, so
        an edited title/message is a delete-then-insert — and re-inserts only
        when ``text`` is non-empty. Called for every conversation/message write
        *and* during rebuild, so incremental writes and a from-JSONL rebuild
        converge on the same index contents.
        """

        if not self._fts_available:
            return
        self._c.execute(
            "DELETE FROM conversation_fts WHERE ref_id=? AND kind=?", (ref_id, kind)
        )
        clean = (text or "").strip()
        if not clean:
            return
        self._c.execute(
            "INSERT INTO conversation_fts"
            " (conversation_id, org_id, ref_id, kind, text) VALUES (?,?,?,?,?)",
            (conversation_id, org_id, ref_id, kind, clean),
        )

    def _index_title(self, doc: dict) -> None:
        self._fts_replace(
            conversation_id=doc["conversation_id"],
            org_id=doc["org_id"],
            ref_id=doc["conversation_id"],
            kind=_FTS_KIND_TITLE,
            text=doc.get("title") if doc.get("deleted_at") is None else None,
        )

    def _index_message(self, doc: dict) -> None:
        # Only redacted user/assistant text is searchable. Tool/system turns —
        # which carry connector payloads and prompt scaffolding — and deleted
        # messages are dropped from the index rather than indexed.
        indexable = (
            doc.get("role") in _SEARCHABLE_ROLES and doc.get("deleted_at") is None
        )
        self._fts_replace(
            conversation_id=doc["conversation_id"],
            org_id=doc["org_id"],
            ref_id=doc["message_id"],
            kind=_FTS_KIND_MESSAGE,
            text=doc.get("content_text") if indexable else None,
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

    def search_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        query: str,
        limit: int,
        include_archived: bool,
        include_deleted: bool,
    ) -> list[tuple[str, float]]:
        """Return ``(conversation doc, score)`` ranked best-first.

        Matches the query against indexed title + user/assistant message text,
        collapses the per-row FTS hits to one entry per conversation (keeping the
        strongest match), then joins to ``conversations`` so the same
        org/user/archived/deleted scoping as :meth:`list_conversations` applies.
        Returns ``[]`` when FTS is unavailable or the query has no usable tokens.
        """

        if not self._fts_available:
            return []
        match = self._to_match_query(query)
        if match is None:
            return []
        # bm25() returns a score where a smaller (more negative) value is a
        # better match; we keep the best (min) score per conversation.
        rows = self._c.execute(
            "SELECT conversation_id, bm25(conversation_fts) AS score"
            " FROM conversation_fts"
            " WHERE conversation_fts MATCH ? AND org_id = ?",
            (match, org_id),
        ).fetchall()
        best: dict[str, float] = {}
        for conversation_id, score in rows:
            if conversation_id not in best or score < best[conversation_id]:
                best[conversation_id] = float(score)
        if not best:
            return []

        placeholders = ",".join("?" for _ in best)
        sql = (
            f"SELECT conversation_id, doc FROM conversations"
            f" WHERE conversation_id IN ({placeholders})"
            f" AND org_id=? AND user_id=?"
        )
        params: list[object] = [*best.keys(), org_id, user_id]
        if not include_archived:
            sql += " AND status != 'archived'"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        doc_by_id = {
            conversation_id: doc
            for conversation_id, doc in self._c.execute(sql, params).fetchall()
        }
        ranked = sorted(doc_by_id, key=lambda cid: (best[cid], cid))
        return [(doc_by_id[cid], best[cid]) for cid in ranked[:limit]]

    @staticmethod
    def _to_match_query(query: str) -> str | None:
        """Build a safe FTS5 MATCH expression from raw user input.

        Extracts word tokens (dropping every FTS operator/quote/wildcard char),
        turns each into a prefix term, and ANDs them together. Returns ``None``
        when nothing indexable remains, so a blank or punctuation-only query
        yields no results rather than a SQL error.
        """

        tokens = _QUERY_TOKEN.findall(query or "")
        if not tokens:
            return None
        return " ".join(f'"{token}"*' for token in tokens)


__all__ = ("CatalogIndex",)
