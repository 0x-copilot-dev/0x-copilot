"""File-backed ``DraftStorePort`` — durable versioned draft artifacts.

Each version is journaled append-only to ``state/drafts.jsonl`` and folded into
per-``(org_id, draft_id)`` version histories on construction. Optimistic
concurrency mirrors the in-memory / Postgres contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from threading import RLock

from agent_runtime.persistence.ports import DraftOwnershipConflict, OptimisticConflict
from agent_runtime.persistence.records import (
    DraftEffectSupersession,
    DraftRecord,
    DraftStatus,
)
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file._state_ledger import StateLedger


class FileDraftStore:
    """Durable, single-writer append-only draft store backed by one ledger."""

    _TABLE = "drafts"
    _SUPERSESSIONS_TABLE = "draft_effect_supersessions"

    def __init__(self, layout: FileStoreLayout) -> None:
        self._lock = RLock()
        self._ledger = StateLedger(layout.state_path(self._TABLE))
        self._supersessions_ledger = StateLedger(
            layout.state_path(self._SUPERSESSIONS_TABLE)
        )
        self.versions: dict[tuple[str, str], list[DraftRecord]] = {}
        self.effect_supersessions: dict[
            tuple[str, str, str, str], DraftEffectSupersession
        ] = {}
        self._effect_stage_ids_by_draft: dict[tuple[str, str, str], set[str]] = {}
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
        for record_json in self._supersessions_ledger.load_puts():
            record = DraftEffectSupersession.model_validate(record_json)
            key = (record.org_id, record.user_id, record.draft_id, record.stage_id)
            existing = self.effect_supersessions.get(key)
            if existing is not None:
                if not _same_effect_supersession(existing, record):
                    raise ValueError(
                        "effect stage conflicts with an existing draft binding"
                    )
                continue
            self.effect_supersessions[key] = record
            self._effect_stage_ids_by_draft.setdefault(key[:3], set()).add(
                record.stage_id
            )

    async def insert_version(self, record: DraftRecord) -> DraftRecord:
        with self._lock:
            key = (record.org_id, record.draft_id)
            history = self.versions.setdefault(key, [])
            if history and history[-1].user_id != record.user_id:
                raise DraftOwnershipConflict(draft_id=record.draft_id)
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

    async def list_versions_for_migration(
        self,
        *,
        org_id: str,
        after: tuple[str, int] | None,
        limit: int,
    ) -> Sequence[DraftRecord]:
        """Return a bounded, stable keyset page of every historic version."""

        if limit <= 0:
            return ()
        with self._lock:
            records = [
                record
                for (record_org_id, _), history in self.versions.items()
                if record_org_id == org_id
                for record in history
            ]
            records.sort(key=lambda record: (record.draft_id, record.version))
            if after is not None:
                records = [
                    record
                    for record in records
                    if (record.draft_id, record.version) > after
                ]
            return tuple(records[:limit])

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

    async def record_effect_supersession(
        self, record: DraftEffectSupersession
    ) -> DraftEffectSupersession:
        """Persist/replay a direct F-006 draft-to-stage correlation."""

        key = (record.org_id, record.user_id, record.draft_id, record.stage_id)
        with self._lock:
            existing = self.effect_supersessions.get(key)
            if existing is not None:
                if _same_effect_supersession(existing, record):
                    return existing
                raise ValueError(
                    "effect stage conflicts with an existing draft binding"
                )
            self._supersessions_ledger.append_put(record.model_dump(mode="json"))
            self.effect_supersessions[key] = record
            self._effect_stage_ids_by_draft.setdefault(key[:3], set()).add(
                record.stage_id
            )
            return record

    async def has_effect_supersession(
        self, *, org_id: str, user_id: str, draft_id: str
    ) -> bool:
        """Answer a durable direct lookup without scanning host-run history."""

        with self._lock:
            return bool(
                self._effect_stage_ids_by_draft.get((org_id, user_id, draft_id))
            )


def _same_effect_supersession(
    left: DraftEffectSupersession, right: DraftEffectSupersession
) -> bool:
    return (
        left.org_id,
        left.user_id,
        left.draft_id,
        left.stage_id,
        left.host_run_id,
        left.artifact_id,
        left.proposal_digest,
        left.target_digest,
    ) == (
        right.org_id,
        right.user_id,
        right.draft_id,
        right.stage_id,
        right.host_run_id,
        right.artifact_id,
        right.proposal_digest,
        right.target_digest,
    )


__all__ = ("FileDraftStore",)
