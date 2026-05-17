"""In-memory ``TodoExtractionStorePort`` for tests and local development.

Thread-safe through a single re-entrant lock. Tests assert against
``self.rows`` directly when they need to inspect full state. The tenant-first
predicate ordering mirrors the postgres adapter so cross-tenant isolation
behaviour is identical in tests and production.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from threading import RLock

from agent_runtime.persistence.records import (
    TodoExtractionRecord,
    TodoExtractionState,
)


class InMemoryTodoExtractionStore:
    """Process-local implementation of :class:`TodoExtractionStorePort`."""

    def __init__(self) -> None:
        self._lock = RLock()
        # extraction_id → record. We key by id (not org+id) because every
        # public path passes org_id explicitly; the org check is enforced
        # at read time rather than encoded in the key.
        self.rows: dict[str, TodoExtractionRecord] = {}

    async def insert_many(
        self, records: Sequence[TodoExtractionRecord]
    ) -> Sequence[TodoExtractionRecord]:
        """Persist a batch of proposals; duplicate ids are rejected via ValueError."""
        with self._lock:
            for record in records:
                if record.id in self.rows:
                    raise ValueError(f"duplicate extraction id {record.id!r}")
            inserted: list[TodoExtractionRecord] = []
            for record in records:
                self.rows[record.id] = record
                inserted.append(record)
            return tuple(inserted)

    async def get_by_id(
        self, *, org_id: str, extraction_id: str
    ) -> TodoExtractionRecord | None:
        """Return the row iff it belongs to ``org_id``; ``None`` otherwise."""
        with self._lock:
            row = self.rows.get(extraction_id)
            if row is None or row.org_id != org_id:
                return None
            return row

    async def list_pending(
        self,
        *,
        org_id: str,
        owner_user_id: str,
        limit: int,
    ) -> Sequence[TodoExtractionRecord]:
        """Return the caller's pending proposals, newest first."""
        if limit <= 0:
            return ()
        with self._lock:
            matching = [
                row
                for row in self.rows.values()
                if row.org_id == org_id
                and row.owner_user_id == owner_user_id
                and row.state == TodoExtractionState.PENDING
            ]
            matching.sort(key=lambda r: r.created_at, reverse=True)
            return tuple(matching[:limit])

    async def update_state(
        self,
        *,
        org_id: str,
        extraction_id: str,
        state: TodoExtractionState,
        resolved_at: datetime,
    ) -> TodoExtractionRecord | None:
        """Transition the proposal's state; idempotent under repeat-of-same."""
        with self._lock:
            current = self.rows.get(extraction_id)
            if current is None or current.org_id != org_id:
                return None
            updated = current.model_copy(
                update={"state": state, "resolved_at": resolved_at}
            )
            self.rows[extraction_id] = updated
            return updated
