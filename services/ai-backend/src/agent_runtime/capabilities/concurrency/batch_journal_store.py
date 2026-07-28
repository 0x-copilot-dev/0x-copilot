"""Canonical run-event adapter for F6 batch plans.

Every durability guarantee here is inherited, not reinvented: per-run monotonic
sequencing, stable-event-id idempotency, tenant scoping, retention, deletion,
export, and desktop file-store crash safety all come from the run event store
this class wraps.  It adds only F6-specific validation on top, so a batch plan
is retained, exported, and deleted with the run it belongs to, and there is no
second lifecycle to keep in sync.

This module is the one place in F6 that knows about the transport schema.  The
record vocabulary lives in
:mod:`agent_runtime.capabilities.concurrency.batch_journal` so the schema can
import it without a cycle.
"""

from __future__ import annotations

import hashlib
from typing import ClassVar

from pydantic import ValidationError

from agent_runtime.api.ports import EventStorePort
from agent_runtime.capabilities.concurrency.batch_journal import (
    BatchJournalConflict,
    BatchJournalCorruption,
    BatchJournalRecord,
    BatchJournalScopeConflict,
    BatchJournalSnapshotConflict,
    BatchJournalWrite,
    BatchPlanStorePort,
    BatchRecoveryView,
    DurableBatchPlan,
    SequencedBatchJournalRecord,
)
from agent_runtime.control_plane.contracts import RunControlSnapshot
from agent_runtime.control_plane.ports import (
    RunControlJournalCorruption,
    RunControlScopeConflict,
    RunControlSnapshotStorePort,
)
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.persistence.ports import RuntimeEventIdempotencyConflict
from runtime_api.schemas import (
    OperationBatchJournalPayload,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)


class EventJournalBatchPlanStore(BatchPlanStorePort):
    """Fold strict F6 plans from the existing tenant-scoped run event log."""

    EVENT_TYPE: ClassVar[RuntimeApiEventType] = (
        RuntimeApiEventType.OPERATION_BATCH_JOURNAL
    )
    EVENT_ID_PREFIX: ClassVar[str] = "operation_batch"

    def __init__(
        self,
        *,
        events: EventStorePort,
        snapshots: RunControlSnapshotStorePort,
    ) -> None:
        self._events = events
        self._snapshots = snapshots

    async def put_plan(self, write: BatchJournalWrite) -> DurableBatchPlan:
        """Append one plan, returning the durable handle F6.3 dispatches from.

        A retry of the identical decision returns the first durable record
        rather than writing a second plan.  A different decision under the same
        batch identity is an idempotency conflict, never a silent overwrite —
        which is what stops a restart from re-ordering work that already ran.
        """

        record = write.record
        snapshot = await self._load_snapshot(
            org_id=write.org_id,
            run_id=record.run_id,
            subject_fingerprint=write.subject_fingerprint,
        )
        if snapshot is None:
            raise BatchJournalCorruption(
                run_id=record.run_id,
                reason="a batch plan cannot precede the control snapshot",
            )
        self._validate_snapshot_binding(snapshot=snapshot, record=record)

        existing = await self._existing_record(
            org_id=write.org_id,
            run_id=record.run_id,
            subject_fingerprint=write.subject_fingerprint,
            record=record,
        )
        if existing is not None:
            return self._durable(existing)

        event_id = self._stable_event_id(record)
        draft = RuntimeEventDraft(
            org_id=write.org_id,
            event_id=event_id,
            created_at=record.created_at,
            run_id=record.run_id,
            conversation_id=snapshot.conversation_id,
            trace_id=write.trace_id,
            source=StreamEventSource.RUNTIME,
            event_type=self.EVENT_TYPE,
            activity_kind=RuntimeActivityKind.EVENT,
            visibility=RuntimeEventVisibility.INTERNAL,
            redaction_state=RuntimeEventRedactionState.REDACTED,
            payload=self._payload(record),
        )
        try:
            envelope = await self._events.append_event(draft)
        except RuntimeEventIdempotencyConflict:
            concurrent = await self._record_by_event_id(
                org_id=write.org_id,
                run_id=record.run_id,
                event_id=event_id,
                expected_conversation_id=snapshot.conversation_id,
            )
            if concurrent is None:
                raise BatchJournalCorruption(
                    run_id=record.run_id,
                    reason="stable F6 event conflicted but was not readable",
                ) from None
            if concurrent.record.record_digest != record.record_digest:
                raise BatchJournalConflict(
                    run_id=record.run_id,
                    record_id=record.record_id,
                ) from None
            return self._durable(concurrent)

        durable = self._record_from_event(
            envelope,
            expected_conversation_id=snapshot.conversation_id,
        )
        if durable.record_digest != record.record_digest:
            raise BatchJournalCorruption(
                run_id=record.run_id,
                reason="appended F6 record does not match its canonical digest",
            )
        return self._durable(
            SequencedBatchJournalRecord(
                sequence_no=envelope.sequence_no,
                record=durable,
            )
        )

    async def load_recovery_view(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        after_sequence: int = 0,
    ) -> BatchRecoveryView:
        """Rebuild every durable batch decision for one run from the journal."""

        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        snapshot = await self._load_snapshot(
            org_id=org_id,
            run_id=run_id,
            subject_fingerprint=subject_fingerprint,
        )
        if snapshot is None:
            raise BatchJournalCorruption(
                run_id=run_id,
                reason="a batch journal cannot be recovered without its snapshot",
            )
        records = await self._replay(org_id=org_id, snapshot=snapshot)
        return BatchRecoveryView(
            run_id=snapshot.run_id,
            snapshot_id=snapshot.snapshot_id,
            records=tuple(
                item for item in records if item.sequence_no > after_sequence
            ),
        )

    async def _existing_record(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        record: BatchJournalRecord,
    ) -> SequencedBatchJournalRecord | None:
        """Validate the durable prefix, returning an identical prior append."""

        view = await self.load_recovery_view(
            org_id=org_id,
            run_id=run_id,
            subject_fingerprint=subject_fingerprint,
        )
        durable = next(
            (
                item
                for item in view.records
                if item.record.record_id == record.record_id
            ),
            None,
        )
        if durable is None:
            return None
        if durable.record.record_digest != record.record_digest:
            raise BatchJournalConflict(run_id=run_id, record_id=record.record_id)
        return durable

    async def _load_snapshot(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
    ) -> RunControlSnapshot | None:
        try:
            return await self._snapshots.get(
                org_id=org_id,
                run_id=run_id,
                subject_fingerprint=subject_fingerprint,
            )
        except RunControlScopeConflict as exc:
            raise BatchJournalScopeConflict(run_id=run_id) from exc
        except RunControlJournalCorruption as exc:
            raise BatchJournalCorruption(
                run_id=run_id,
                reason="the bound control snapshot is not replayable",
            ) from exc

    async def _replay(
        self,
        *,
        org_id: str,
        snapshot: RunControlSnapshot,
    ) -> tuple[SequencedBatchJournalRecord, ...]:
        events = await self._events.list_events_after(
            org_id=org_id,
            run_id=snapshot.run_id,
            after_sequence=0,
        )
        records = tuple(
            SequencedBatchJournalRecord(
                sequence_no=event.sequence_no,
                record=self._record_from_event(
                    event,
                    expected_conversation_id=snapshot.conversation_id,
                ),
            )
            for event in events
            if event.event_type is self.EVENT_TYPE
        )
        self._validate_replay(snapshot=snapshot, records=records)
        return records

    async def _record_by_event_id(
        self,
        *,
        org_id: str,
        run_id: str,
        event_id: str,
        expected_conversation_id: str,
    ) -> SequencedBatchJournalRecord | None:
        events = await self._events.list_events_after(
            org_id=org_id,
            run_id=run_id,
            after_sequence=0,
        )
        matches = tuple(
            SequencedBatchJournalRecord(
                sequence_no=event.sequence_no,
                record=self._record_from_event(
                    event,
                    expected_conversation_id=expected_conversation_id,
                ),
            )
            for event in events
            if event.event_type is self.EVENT_TYPE and event.event_id == event_id
        )
        if len(matches) > 1:
            raise BatchJournalCorruption(
                run_id=run_id,
                reason="duplicate stable F6 event identity",
            )
        return matches[0] if matches else None

    @staticmethod
    def _durable(item: SequencedBatchJournalRecord) -> DurableBatchPlan:
        return DurableBatchPlan(sequence_no=item.sequence_no, record=item.record)

    @classmethod
    def _record_from_event(
        cls,
        event: RuntimeEventEnvelope,
        *,
        expected_conversation_id: str,
    ) -> BatchJournalRecord:
        try:
            payload = OperationBatchJournalPayload.model_validate(event.payload)
        except ValidationError as exc:
            raise BatchJournalCorruption(
                run_id=event.run_id,
                reason="malformed operation_batch.journal.v1 payload",
            ) from exc
        record = payload.record
        for failed, reason in cls._envelope_checks(
            event=event,
            record=record,
            expected_conversation_id=expected_conversation_id,
        ):
            if failed:
                raise BatchJournalCorruption(run_id=event.run_id, reason=reason)
        return record

    @classmethod
    def _envelope_checks(
        cls,
        *,
        event: RuntimeEventEnvelope,
        record: BatchJournalRecord,
        expected_conversation_id: str,
    ) -> tuple[tuple[bool, str], ...]:
        """Return every way one durable envelope could disagree with its record."""

        return (
            (
                event.run_id != record.run_id,
                "F6 record run identity does not match its journal",
            ),
            (
                event.conversation_id != expected_conversation_id,
                "F6 event conversation identity does not match its run snapshot",
            ),
            (
                event.event_id != cls._stable_event_id(record),
                "F6 event does not use its stable identity",
            ),
            (
                event.source is not StreamEventSource.RUNTIME,
                "F6 event has a non-runtime source",
            ),
            (
                event.activity_kind is not RuntimeActivityKind.EVENT,
                "F6 event has a user activity projection",
            ),
            (
                event.visibility is not RuntimeEventVisibility.INTERNAL,
                "F6 event is not internal",
            ),
            (
                event.redaction_state is not RuntimeEventRedactionState.REDACTED,
                "F6 event is not body-free/redacted",
            ),
            (
                event.created_at != record.created_at,
                "F6 event time does not match the canonical record",
            ),
            (bool(event.metadata), "F6 event contains non-canonical metadata"),
            (
                any(
                    value is not None
                    for value in (
                        event.parent_task_id,
                        event.task_id,
                        event.subagent_id,
                        event.display_title,
                        event.summary,
                    )
                ),
                "F6 event contains non-canonical presentation data",
            ),
            (
                event.payload != cls._payload(record),
                "F6 event mirrors do not match the canonical record",
            ),
        )

    @classmethod
    def _validate_replay(
        cls,
        *,
        snapshot: RunControlSnapshot,
        records: tuple[SequencedBatchJournalRecord, ...],
    ) -> None:
        if any(
            left.sequence_no >= right.sequence_no
            for left, right in zip(records, records[1:], strict=False)
        ):
            raise BatchJournalCorruption(
                run_id=snapshot.run_id,
                reason="F6 event sequence is not strictly increasing",
            )
        seen_records: set[str] = set()
        seen_batches: set[str] = set()
        for item in records:
            record = item.record
            cls._validate_snapshot_binding(snapshot=snapshot, record=record)
            if record.record_id in seen_records:
                raise BatchJournalCorruption(
                    run_id=snapshot.run_id,
                    reason="duplicate F6 record identity",
                )
            if record.batch_id in seen_batches:
                raise BatchJournalCorruption(
                    run_id=snapshot.run_id,
                    reason="a batch bound more than one plan",
                )
            seen_records.add(record.record_id)
            seen_batches.add(record.batch_id)

    @staticmethod
    def _validate_snapshot_binding(
        *,
        snapshot: RunControlSnapshot,
        record: BatchJournalRecord,
    ) -> None:
        if (
            record.run_id != snapshot.run_id
            or record.snapshot_id != snapshot.snapshot_id
            or record.concurrency_policy_revision
            != snapshot.policy_revisions.concurrency
        ):
            raise BatchJournalSnapshotConflict(run_id=record.run_id)

    @staticmethod
    def _payload(record: BatchJournalRecord) -> dict[str, object]:
        return OperationBatchJournalPayload(record=record).model_dump(mode="json")

    @classmethod
    def _stable_event_id(cls, record: BatchJournalRecord) -> str:
        """Return the one event identity this record may ever occupy.

        Keying on the record's stable domain identity — which for a plan is
        derived from the batch id — is what turns a concurrent or retried append
        into a detected duplicate rather than a second plan for the same batch.
        """

        identity = f"{record.run_id}:{record.record_id}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"{cls.EVENT_ID_PREFIX}:{digest}"


__all__ = ("EventJournalBatchPlanStore",)
