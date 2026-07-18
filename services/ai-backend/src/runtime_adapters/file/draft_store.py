"""File-backed ``DraftStorePort`` — durable versioned draft artifacts.

Each version is journaled append-only to ``state/drafts.jsonl`` and folded into
per-``(org_id, draft_id)`` version histories on construction. Optimistic
concurrency mirrors the in-memory / Postgres contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from threading import RLock

from agent_runtime.persistence.ports import OptimisticConflict
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file._state_ledger import StateLedger


class FileDraftStore:
    """Durable, single-writer append-only draft store backed by one ledger."""

    _TABLE = "drafts"

    def __init__(self, layout: FileStoreLayout) -> None:
        self._lock = RLock()
        self._ledger = StateLedger(layout.state_path(self._TABLE))
        self.versions: dict[tuple[str, str], list[DraftRecord]] = {}
        self._load()

    def _load(self) -> None:
        for record_json in self._ledger.load_puts():
            record = DraftRecord.model_validate(record_json)
            history = self.versions.setdefault((record.org_id, record.draft_id), [])
            if any(existing.version == record.version for existing in history):
                continue
            history.append(record)
        for history in self.versions.values():
            history.sort(key=lambda record: record.version)

    async def insert_version(self, record: DraftRecord) -> DraftRecord:
        with self._lock:
            key = (record.org_id, record.draft_id)
            history = self.versions.setdefault(key, [])
            if any(existing.version == record.version for existing in history):
                latest_version = history[-1].version if history else 0
                raise OptimisticConflict(
                    draft_id=record.draft_id,
                    expected_version=record.version,
                    actual_version=latest_version,
                )
            history.append(record)
            self._ledger.append_put(record.model_dump(mode="json"))
            return record

    async def latest(self, *, org_id: str, draft_id: str) -> DraftRecord | None:
        with self._lock:
            history = self.versions.get((org_id, draft_id))
            return history[-1] if history else None

    async def get_version(
        self, *, org_id: str, draft_id: str, version: int
    ) -> DraftRecord | None:
        with self._lock:
            history = self.versions.get((org_id, draft_id), [])
            for record in history:
                if record.version == version:
                    return record
            return None

    async def latest_for_conversation(
        self, *, org_id: str, conversation_id: str
    ) -> Sequence[DraftRecord]:
        with self._lock:
            results: list[DraftRecord] = []
            for (record_org_id, _), history in self.versions.items():
                if record_org_id != org_id or not history:
                    continue
                latest = history[-1]
                if latest.conversation_id != conversation_id:
                    continue
                results.append(latest)
            results.sort(key=lambda record: record.created_at)
            return tuple(results)

    async def expect_status(
        self,
        *,
        org_id: str,
        draft_id: str,
        expected_version: int,
        expected_status: DraftStatus | None = None,
    ) -> DraftRecord:
        with self._lock:
            latest = await self.latest(org_id=org_id, draft_id=draft_id)
            if latest is None:
                raise KeyError(draft_id)
            if latest.version != expected_version:
                raise OptimisticConflict(
                    draft_id=draft_id,
                    expected_version=expected_version,
                    actual_version=latest.version,
                )
            if expected_status is not None and latest.status != expected_status:
                raise OptimisticConflict(
                    draft_id=draft_id,
                    expected_version=expected_version,
                    actual_version=latest.version,
                )
            return latest


__all__ = ("FileDraftStore",)
