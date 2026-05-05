"""In-memory ``CitationStorePort`` for tests and local development."""

from __future__ import annotations

from collections.abc import Sequence
from threading import RLock

from agent_runtime.persistence.records import CitationRecord


class InMemoryCitationStore:
    """Deterministic in-memory implementation of :class:`CitationStorePort`.

    The store is process-local and thread-safe via a single re-entrant lock.
    Tests assert against ``self.rows`` directly when they need to inspect the
    persisted state.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        # (run_id, source_connector, source_doc_id) → CitationRecord — the
        # idempotency key that mirrors the unique index from migration 0015.
        self._index: dict[tuple[str, str, str], CitationRecord] = {}
        # Insertion-ordered list keyed by (run_id, ordinal) for list_for_run.
        self._rows: list[CitationRecord] = []

    @property
    def rows(self) -> tuple[CitationRecord, ...]:
        with self._lock:
            return tuple(self._rows)

    def insert_or_get(self, record: CitationRecord) -> CitationRecord:
        with self._lock:
            key = (record.run_id, record.source_connector, record.source_doc_id)
            existing = self._index.get(key)
            if existing is not None:
                return existing
            self._index[key] = record
            self._rows.append(record)
            return record

    def list_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> Sequence[CitationRecord]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        row
                        for row in self._rows
                        if row.org_id == org_id and row.run_id == run_id
                    ),
                    key=lambda row: row.ordinal,
                )
            )

    def list_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> Sequence[CitationRecord]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        row
                        for row in self._rows
                        if row.org_id == org_id
                        and row.conversation_id == conversation_id
                    ),
                    key=lambda row: row.created_at,
                )
            )
