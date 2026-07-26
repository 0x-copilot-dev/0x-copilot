"""File-backed typed queue CAS and source reservations for E2 D5."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from typing import Iterator
from uuid import uuid4
from datetime import UTC, datetime

from agent_runtime.api.legacy_stage_migration_runtime import (
    LegacyCanonicalStageEvidence,
    LegacyQueueInventoryState,
    legacy_stage_source_digest,
)
from agent_runtime.api.legacy_stage_migration_service import (
    LegacyQueueNeutralizationOutcome,
    LegacySourceFenceOutcome,
)
from agent_runtime.persistence.records import OutboxStatus
from agent_runtime.surfaces_v2.staging import StagedWriteFold
from agent_runtime.surfaces_v2.legacy_stage_materialization import (
    LegacyStageMaterializationRecord,
    LegacyStageMaterializationRejected,
    LegacyStageMaterializationState,
    LegacyStageReconciliationRecord,
    LegacyStageReconciliationState,
    materialization_fence_from_metadata,
)
from runtime_adapters.file._advisory_lock import acquire_exclusive, release_exclusive
from runtime_api.schemas import RuntimeApiEventType


class FileLegacyStageReservationStore:
    """Crash-safe, lock-protected exact-source reservation ledger."""

    _SUBDIR = "e2_legacy_stage_reservations"
    _EVIDENCE_SUBDIR = "e2_legacy_stage_evidence"
    _RECONCILIATION_SUBDIR = "e2_legacy_stage_reconciliations"

    def __init__(self, *, store: object, root: str | Path) -> None:
        self._store = store
        base = Path(root).expanduser().resolve()
        self._dir = base / self._SUBDIR
        self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._evidence_dir = base / self._EVIDENCE_SUBDIR
        self._evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._reconciliation_dir = base / self._RECONCILIATION_SUBDIR
        self._reconciliation_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._lock_path = self._dir / ".lock"
        self._lock = asyncio.Lock()
        # Event append obtains this exact control object from the store and
        # consumes the durable reservation in the same store state lock.
        setattr(store, "_e2_legacy_stage_reservation_control", self)

    async def verify_and_reserve(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
        idempotency_key: str,
        canonical_stage_id: str,
    ) -> LegacySourceFenceOutcome:
        async with self._store._state_lock:  # noqa: SLF001
            run = self._store.runs.get(run_id)  # noqa: SLF001
            if run is None or getattr(run, "org_id", None) != org_id:
                return LegacySourceFenceOutcome.SOURCE_CHANGED
            state = StagedWriteFold.fold(
                self._store.events_by_run.get(run_id, ())  # noqa: SLF001
            ).get(legacy_stage_id)
            if state is None:
                return LegacySourceFenceOutcome.SOURCE_CHANGED
            source_digest = legacy_stage_source_digest(run_id=run_id, state=state)
            if source_digest != expected_source_digest:
                return LegacySourceFenceOutcome.SOURCE_CHANGED
        material = "\0".join((org_id, run_id, legacy_stage_id)).encode()
        path = self._dir / f"{hashlib.sha256(material).hexdigest()}.json"
        record = LegacyStageMaterializationRecord(
            org_id=org_id,
            run_id=run_id,
            legacy_stage_id=legacy_stage_id,
            source_digest=source_digest,
            idempotency_key=idempotency_key,
            canonical_stage_id=canonical_stage_id,
            state=LegacyStageMaterializationState.RESERVED,
            revision=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        async with self._lock:
            with self._exclusive_lock():
                if path.exists():
                    existing = self._read(path)
                    if (
                        existing.source_digest != source_digest
                        or existing.idempotency_key != idempotency_key
                        or existing.canonical_stage_id != canonical_stage_id
                    ):
                        return LegacySourceFenceOutcome.SOURCE_CHANGED
                    if existing.state is LegacyStageMaterializationState.RESERVED:
                        # A power loss may land after the event stream is
                        # fsynced but before this sidecar advances.  The
                        # canonical stage id is deterministic, and an exact
                        # event proves the append passed its source fence, so
                        # recover ``reserved → staged`` instead of wedging.
                        if self._canonical_stage_exists(
                            run_id=run_id,
                            canonical_stage_id=canonical_stage_id,
                        ):
                            self._write(
                                path,
                                existing.model_copy(
                                    update={
                                        "state": LegacyStageMaterializationState.STAGED,
                                        "revision": existing.revision + 1,
                                        "updated_at": datetime.now(UTC),
                                    }
                                ),
                            )
                            return LegacySourceFenceOutcome.STAGED
                        return LegacySourceFenceOutcome.ALREADY_RESERVED
                    if existing.state in {
                        LegacyStageMaterializationState.STAGED,
                        LegacyStageMaterializationState.MAPPED,
                    }:
                        return LegacySourceFenceOutcome.STAGED
                    return LegacySourceFenceOutcome.SOURCE_CHANGED
                temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
                try:
                    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(
                            json.dumps(
                                {"record": record.model_dump(mode="json")},
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                        )
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, path)
                    return LegacySourceFenceOutcome.RESERVED
                finally:
                    temporary.unlink(missing_ok=True)

    async def mark_mapped(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
        canonical_stage_id: str,
    ) -> None:
        await self._transition(
            org_id=org_id,
            run_id=run_id,
            legacy_stage_id=legacy_stage_id,
            expected_source_digest=expected_source_digest,
            canonical_stage_id=canonical_stage_id,
            from_states={
                LegacyStageMaterializationState.STAGED,
                LegacyStageMaterializationState.MAPPED,
            },
            to_state=LegacyStageMaterializationState.MAPPED,
        )

    async def quarantine(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
    ) -> None:
        await self._transition(
            org_id=org_id,
            run_id=run_id,
            legacy_stage_id=legacy_stage_id,
            expected_source_digest=expected_source_digest,
            canonical_stage_id=None,
            from_states={LegacyStageMaterializationState.RESERVED},
            to_state=LegacyStageMaterializationState.QUARANTINED,
        )

    def assert_append_fence(self, *, event: object) -> object | None:
        """Verify under FileRuntimeApiStore's state lock before its append."""

        metadata = getattr(event, "metadata", {})
        fence = materialization_fence_from_metadata(metadata)
        if fence is None:
            return None
        if (
            getattr(event, "org_id", None) != fence.org_id
            or getattr(event, "run_id", None) != fence.run_id
            or getattr(event, "payload", {}).get("stage_id") != fence.canonical_stage_id
        ):
            raise LegacyStageMaterializationRejected(
                "legacy materialization fence does not match append"
            )
        record = self._read(
            self._path(fence.org_id, fence.run_id, fence.legacy_stage_id)
        )
        if (
            record.state is not LegacyStageMaterializationState.RESERVED
            or record.source_digest != fence.source_digest
            or record.idempotency_key != fence.idempotency_key
            or record.canonical_stage_id != fence.canonical_stage_id
            or not self._source_matches(
                org_id=fence.org_id,
                run_id=fence.run_id,
                legacy_stage_id=fence.legacy_stage_id,
                expected_source_digest=fence.source_digest,
            )
        ):
            raise LegacyStageMaterializationRejected("legacy source changed")
        return fence

    def mark_append_staged(self, *, fence: object) -> None:
        path = self._path(fence.org_id, fence.run_id, fence.legacy_stage_id)
        record = self._read(path)
        if record.state is not LegacyStageMaterializationState.RESERVED:
            raise LegacyStageMaterializationRejected("legacy source is not reserved")
        self._write(
            path,
            record.model_copy(
                update={
                    "state": LegacyStageMaterializationState.STAGED,
                    "revision": record.revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            ),
        )

    def _source_matches(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
    ) -> bool:
        """Re-fold the source at the append-time fence, never from inventory."""

        run = self._store.runs.get(run_id)  # noqa: SLF001
        if run is None or getattr(run, "org_id", None) != org_id:
            return False
        state = StagedWriteFold.fold(
            self._store.events_by_run.get(run_id, ())  # noqa: SLF001
        ).get(legacy_stage_id)
        return (
            state is not None
            and legacy_stage_source_digest(run_id=run_id, state=state)
            == expected_source_digest
        )

    async def _transition(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
        canonical_stage_id: str | None,
        from_states: set[LegacyStageMaterializationState],
        to_state: LegacyStageMaterializationState,
    ) -> None:
        async with self._lock:
            with self._exclusive_lock():
                path = self._path(org_id, run_id, legacy_stage_id)
                record = self._read(path)
                if record.source_digest != expected_source_digest or (
                    canonical_stage_id is not None
                    and record.canonical_stage_id != canonical_stage_id
                ):
                    raise RuntimeError("legacy materialization facts changed")
                if record.state is to_state:
                    return
                if record.state not in from_states:
                    raise RuntimeError("legacy materialization transition is invalid")
                self._write(
                    path,
                    record.model_copy(
                        update={
                            "state": to_state,
                            "revision": record.revision + 1,
                            "updated_at": datetime.now(UTC),
                        }
                    ),
                )

    async def load_candidate_evidence(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        source_digest: str,
    ) -> LegacyCanonicalStageEvidence | None:
        path = self._evidence_path(org_id, run_id, legacy_stage_id, source_digest)
        async with self._lock:
            with self._exclusive_lock():
                if not path.exists():
                    return None
                try:
                    return LegacyCanonicalStageEvidence.model_validate(
                        json.loads(path.read_text(encoding="utf-8"))["evidence"]
                    )
                except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                    return None

    def _evidence_path(
        self, org_id: str, run_id: str, legacy_stage_id: str, source_digest: str
    ) -> Path:
        material = "\0".join((org_id, run_id, legacy_stage_id, source_digest)).encode()
        return self._evidence_dir / f"{hashlib.sha256(material).hexdigest()}.json"

    async def checkpoint_reconciliation(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        source_digest: str,
        state: LegacyStageReconciliationState,
        operator_ref: str,
        migration_job_id: str,
    ) -> LegacyStageReconciliationRecord:
        material = "\0".join((org_id, run_id, legacy_stage_id)).encode()
        path = self._reconciliation_dir / f"{hashlib.sha256(material).hexdigest()}.json"
        now = datetime.now(UTC)
        async with self._lock:
            with self._exclusive_lock():
                if path.exists():
                    try:
                        existing = LegacyStageReconciliationRecord.model_validate(
                            json.loads(path.read_text(encoding="utf-8"))["record"]
                        )
                    except (
                        KeyError,
                        OSError,
                        TypeError,
                        ValueError,
                        json.JSONDecodeError,
                    ) as exc:
                        raise RuntimeError(
                            "legacy reconciliation state is invalid"
                        ) from exc
                    record = existing.model_copy(
                        update={
                            "source_digest": source_digest,
                            "state": state,
                            "checkpoint_revision": existing.checkpoint_revision + 1,
                            "operator_ref": operator_ref,
                            "migration_job_id": migration_job_id,
                            "reassessed_at": now,
                            "terminal_at": (
                                now
                                if state is not LegacyStageReconciliationState.FROZEN
                                else None
                            ),
                        }
                    )
                else:
                    record = LegacyStageReconciliationRecord(
                        org_id=org_id,
                        run_id=run_id,
                        legacy_stage_id=legacy_stage_id,
                        source_digest=source_digest,
                        state=state,
                        checkpoint_revision=0,
                        operator_ref=operator_ref,
                        migration_job_id=migration_job_id,
                        reassessed_at=now,
                        terminal_at=(
                            now
                            if state is not LegacyStageReconciliationState.FROZEN
                            else None
                        ),
                    )
                self._write_record(path, record)
                return record

    @staticmethod
    def _write_record(path: Path, record: LegacyStageReconciliationRecord) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with os.fdopen(
                os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write(
                    json.dumps(
                        {"record": record.model_dump(mode="json")},
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _path(self, org_id: str, run_id: str, legacy_stage_id: str) -> Path:
        material = "\0".join((org_id, run_id, legacy_stage_id)).encode()
        return self._dir / f"{hashlib.sha256(material).hexdigest()}.json"

    def _canonical_stage_exists(self, *, run_id: str, canonical_stage_id: str) -> bool:
        """Recognize only the exact canonical append, never a guessed stage."""

        for event in self._store.events_by_run.get(run_id, ()):  # noqa: SLF001
            event_type = getattr(getattr(event, "event_type", None), "value", None)
            if (
                event_type == RuntimeApiEventType.EFFECT_STAGED.value
                and getattr(event, "payload", {}).get("stage_id") == canonical_stage_id
            ):
                return True
        return False

    @staticmethod
    def _read(path: Path) -> LegacyStageMaterializationRecord:
        try:
            return LegacyStageMaterializationRecord.model_validate(
                json.loads(path.read_text(encoding="utf-8"))["record"]
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LegacyStageMaterializationRejected(
                "legacy materialization state is invalid"
            ) from exc

    @staticmethod
    def _write(path: Path, record: LegacyStageMaterializationRecord) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with os.fdopen(
                os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write(
                    json.dumps(
                        {"record": record.model_dump(mode="json")},
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        acquired = False
        try:
            acquire_exclusive(fd)
            acquired = True
            yield
        finally:
            if acquired:
                release_exclusive(fd)
            os.close(fd)


class FileLegacyStageQueueControl:
    """Uses FileRuntimeApiStore's durable queue mutation lock and JSONL log."""

    def __init__(self, *, store: object) -> None:
        self._store = store

    async def state_for_stage(
        self, *, org_id: str, run_id: str, legacy_stage_id: str
    ) -> str:
        async with self._store._state_lock:  # noqa: SLF001
            statuses = self._statuses(
                org_id=org_id, run_id=run_id, legacy_stage_id=legacy_stage_id
            )
            if OutboxStatus.CLAIMED in statuses:
                return LegacyQueueInventoryState.CLAIMED
            if any(
                status in {OutboxStatus.PENDING, OutboxStatus.RETRY}
                for status in statuses
            ):
                return LegacyQueueInventoryState.UNCLAIMED
            return LegacyQueueInventoryState.NONE

    async def cancel_unclaimed(
        self, *, org_id: str, run_id: str, legacy_stage_id: str, source_digest: str
    ) -> LegacyQueueNeutralizationOutcome:
        async with self._store._state_lock:  # noqa: SLF001
            if not self._source_matches(
                org_id=org_id,
                run_id=run_id,
                legacy_stage_id=legacy_stage_id,
                expected_source_digest=source_digest,
            ):
                return LegacyQueueNeutralizationOutcome.SOURCE_CHANGED
            command_ids = self._commands(
                org_id=org_id, run_id=run_id, legacy_stage_id=legacy_stage_id
            )
            if not command_ids:
                return LegacyQueueNeutralizationOutcome.SOURCE_CHANGED
            statuses = tuple(
                self._store._queue_statuses.get(command_id)  # noqa: SLF001
                for command_id in command_ids
            )
            if OutboxStatus.CLAIMED in statuses:
                return LegacyQueueNeutralizationOutcome.CLAIMED
            active = tuple(
                command_id
                for command_id, status in zip(command_ids, statuses, strict=True)
                if status in {OutboxStatus.PENDING, OutboxStatus.RETRY}
            )
            if not active:
                if all(status is OutboxStatus.CANCELLED for status in statuses):
                    return LegacyQueueNeutralizationOutcome.ALREADY_CANCELLED
                return LegacyQueueNeutralizationOutcome.SOURCE_CHANGED
            for command_id in active:
                self._store._queue_statuses[command_id] = OutboxStatus.CANCELLED  # noqa: SLF001
                self._store._queue_claims.pop(command_id, None)  # noqa: SLF001
                self._store._append_queue_status(  # noqa: SLF001
                    command_id, OutboxStatus.CANCELLED, None
                )
            return LegacyQueueNeutralizationOutcome.CANCELLED

    def _statuses(self, **kwargs: str) -> tuple[OutboxStatus | None, ...]:
        return tuple(
            self._store._queue_statuses.get(command_id)  # noqa: SLF001
            for command_id in self._commands(**kwargs)
        )

    def _commands(
        self, *, org_id: str, run_id: str, legacy_stage_id: str
    ) -> tuple[str, ...]:
        return tuple(
            command_id
            for command_id, payload in self._store._queue_payloads.items()  # noqa: SLF001
            if (
                payload.get("org_id") == org_id
                and payload.get("run_id") == run_id
                and payload.get("command_type") == "stage_commit_requested"
                and payload.get("stage_id") == legacy_stage_id
            )
        )

    def _source_matches(
        self,
        *,
        org_id: str,
        run_id: str,
        legacy_stage_id: str,
        expected_source_digest: str,
    ) -> bool:
        run = self._store.runs.get(run_id)  # noqa: SLF001
        if run is None or getattr(run, "org_id", None) != org_id:
            return False
        state = StagedWriteFold.fold(
            self._store.events_by_run.get(run_id, ())  # noqa: SLF001
        ).get(legacy_stage_id)
        return (
            state is not None
            and legacy_stage_source_digest(run_id=run_id, state=state)
            == expected_source_digest
        )


__all__ = ["FileLegacyStageQueueControl", "FileLegacyStageReservationStore"]
