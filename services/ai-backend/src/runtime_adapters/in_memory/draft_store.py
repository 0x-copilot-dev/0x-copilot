"""In-memory ``DraftStorePort`` for tests and local development."""

from __future__ import annotations

from collections.abc import Sequence
from threading import RLock

from agent_runtime.persistence.ports import DraftOwnershipConflict, OptimisticConflict
from agent_runtime.persistence.records import (
    DraftEffectSupersession,
    DraftRecord,
    DraftStatus,
)


class InMemoryDraftStore:
    """Deterministic in-memory implementation of :class:`DraftStorePort`.

    The store is process-local and thread-safe via a single re-entrant lock.
    Tests assert against ``self.versions`` directly when they need to inspect
    the full history of a draft.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        # (org_id, draft_id) → list of DraftRecord ordered by version asc.
        self.versions: dict[tuple[str, str], list[DraftRecord]] = {}
        # The immutable stage correlation is deliberately indexed without a
        # host run. A legacy DraftRecord may be re-homed after an older v1
        # approval was issued; that must not make an F-006 supersession vanish.
        self.effect_supersessions: dict[
            tuple[str, str, str, str], DraftEffectSupersession
        ] = {}
        self._effect_stage_ids_by_draft: dict[tuple[str, str, str], set[str]] = {}

    async def insert_version(self, record: DraftRecord) -> DraftRecord:
        """Append a new version to the draft's history; raise :class:`OptimisticConflict` on duplicate version."""
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
            return record

    async def latest(self, *, org_id: str, draft_id: str) -> DraftRecord | None:
        """Return the most recent version of a draft, or ``None`` if not found."""
        with self._lock:
            history = self.versions.get((org_id, draft_id))
            return history[-1] if history else None

    async def get_version(
        self,
        *,
        org_id: str,
        draft_id: str,
        version: int,
    ) -> DraftRecord | None:
        """Return a specific version of a draft, or ``None`` if not found."""
        with self._lock:
            history = self.versions.get((org_id, draft_id), [])
            for record in history:
                if record.version == version:
                    return record
            return None

    async def latest_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> Sequence[DraftRecord]:
        """Return the most recent version of every draft in a conversation, ordered by creation time."""
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
        """Return a bounded, stable keyset page of every historic version.

        This is intentionally migration-only rather than an expansion of the
        normal draft UI port: E2 must inspect the full append-only history,
        never merely a draft's current version.
        """

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
        """Return the draft if its version and status match expectations; raise :class:`OptimisticConflict` otherwise."""
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
                # Surface as conflict — caller likely raced a status change.
                raise OptimisticConflict(
                    draft_id=draft_id,
                    expected_version=expected_version,
                    actual_version=latest.version,
                )
            return latest

    async def record_effect_supersession(
        self, record: DraftEffectSupersession
    ) -> DraftEffectSupersession:
        """Persist/replay one immutable F-006 correlation."""

        key = (record.org_id, record.user_id, record.draft_id, record.stage_id)
        with self._lock:
            existing = self.effect_supersessions.get(key)
            if existing is not None:
                if _same_effect_supersession(existing, record):
                    return existing
                raise ValueError(
                    "effect stage conflicts with an existing draft binding"
                )
            self.effect_supersessions[key] = record
            self._effect_stage_ids_by_draft.setdefault(key[:3], set()).add(
                record.stage_id
            )
            return record

    async def has_effect_supersession(
        self, *, org_id: str, user_id: str, draft_id: str
    ) -> bool:
        """Answer the direct owner-scoped F-006 safety lookup."""

        with self._lock:
            return bool(
                self._effect_stage_ids_by_draft.get((org_id, user_id, draft_id))
            )


def _same_effect_supersession(
    left: DraftEffectSupersession, right: DraftEffectSupersession
) -> bool:
    """Compare immutable facts while ignoring persisted write timestamp."""

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
