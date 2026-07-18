"""File-backed ``CitationStorePort`` — durable sibling of the in-memory store.

Rows are journaled append-only to ``state/citations.jsonl`` and folded into an
in-memory index on construction. Idempotency key mirrors migration 0015:
``(run_id, source_connector, source_doc_id)``.
"""

from __future__ import annotations

from collections.abc import Sequence
from threading import RLock

from agent_runtime.persistence.records import CitationRecord
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file._state_ledger import StateLedger


class FileCitationStore:
    """Durable, single-writer citation store backed by one JSONL ledger."""

    _TABLE = "citations"

    def __init__(self, layout: FileStoreLayout) -> None:
        self._lock = RLock()
        self._ledger = StateLedger(layout.state_path(self._TABLE))
        self._index: dict[tuple[str, str, str], CitationRecord] = {}
        self._rows: list[CitationRecord] = []
        self._load()

    def _load(self) -> None:
        for record_json in self._ledger.load_puts():
            record = CitationRecord.model_validate(record_json)
            key = (record.run_id, record.source_connector, record.source_doc_id)
            if key in self._index:
                continue
            self._index[key] = record
            self._rows.append(record)

    @property
    def rows(self) -> tuple[CitationRecord, ...]:
        with self._lock:
            return tuple(self._rows)

    async def insert_many_or_get(
        self, records: Sequence[CitationRecord]
    ) -> Sequence[CitationRecord]:
        if not records:
            return ()
        with self._lock:
            persisted: list[CitationRecord] = []
            for record in records:
                key = (record.run_id, record.source_connector, record.source_doc_id)
                existing = self._index.get(key)
                if existing is not None:
                    persisted.append(existing)
                    continue
                self._index[key] = record
                self._rows.append(record)
                self._ledger.append_put(record.model_dump(mode="json"))
                persisted.append(record)
            return tuple(persisted)

    async def list_for_run(
        self, *, org_id: str, run_id: str
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

    async def list_for_conversation(
        self, *, org_id: str, conversation_id: str
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


__all__ = ("FileCitationStore",)
