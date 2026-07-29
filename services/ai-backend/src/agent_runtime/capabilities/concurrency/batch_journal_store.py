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
    BatchChildPhase,
    BatchChildTransitionRecord,
    BatchChildTransitionWrite,
    BatchJournalConflict,
    BatchJournalCorruption,
    BatchJournalRecord,
    BatchJournalScopeConflict,
    BatchJournalSnapshotConflict,
    BatchJournalWrite,
    BatchPlanBoundRecord,
    BatchPlanStorePort,
    BatchRecoveryView,
    DurableBatchPlan,
    DurableChildTransition,
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


def _next_sequence(view: BatchRecoveryView) -> int:
    """Return a provisional sequence one past the durable prefix.

    Only ever used to place a not-yet-written candidate at the end of the
    journal for validation. The real sequence is assigned by the event store.
    """

    return max((item.sequence_no for item in view.records), default=0) + 1


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

        if not isinstance(write.record, BatchPlanBoundRecord):
            raise BatchJournalCorruption(
                run_id=write.record.run_id,
                reason="put_plan accepts only a batch plan record",
            )
        durable = await self._append(
            org_id=write.org_id,
            trace_id=write.trace_id,
            subject_fingerprint=write.subject_fingerprint,
            record=write.record,
            missing_snapshot_reason=(
                "a batch plan cannot precede the control snapshot"
            ),
        )
        return self._durable(durable)

    async def append_child_transition(
        self,
        write: BatchChildTransitionWrite,
    ) -> DurableChildTransition:
        """Append one child-lifecycle fact, returning its durable handle.

        The caller awaits this *before* dispatching the child, so the append
        completing is the event that authorizes the body to run.  That ordering
        is the whole point: a torn or unfinished append leaves the coroutine
        suspended, so a child with no durable intent record provably never ran.

        Replay validation (:meth:`_validate_replay`) enforces the rest of the
        discipline — a transition that precedes its batch's plan, names an
        operation the plan never planned, or settles a child that never declared
        an intent is corruption, on the original append and on every restart.
        """

        durable = await self._append(
            org_id=write.org_id,
            trace_id=write.trace_id,
            subject_fingerprint=write.subject_fingerprint,
            record=write.record,
            missing_snapshot_reason=(
                "a child transition cannot precede the control snapshot"
            ),
        )
        if not isinstance(durable.record, BatchChildTransitionRecord):
            raise BatchJournalCorruption(
                run_id=write.record.run_id,
                reason="a child transition identity resolved to another record",
            )
        return DurableChildTransition(
            sequence_no=durable.sequence_no,
            record=durable.record,
        )

    async def _append(
        self,
        *,
        org_id: str,
        trace_id: str,
        subject_fingerprint: str,
        record: BatchJournalRecord,
        missing_snapshot_reason: str,
    ) -> SequencedBatchJournalRecord:
        """Append one strict F6 record under its stable canonical identity.

        The candidate is validated against the durable prefix *before* it is
        written, using the same rules replay applies afterwards. Deferring the
        check to read time would let an invalid record land and only surface as
        an unrecoverable journal at the moment recovery is most needed.
        """

        snapshot = await self._load_snapshot(
            org_id=org_id,
            run_id=record.run_id,
            subject_fingerprint=subject_fingerprint,
        )
        if snapshot is None:
            raise BatchJournalCorruption(
                run_id=record.run_id,
                reason=missing_snapshot_reason,
            )
        self._validate_snapshot_binding(snapshot=snapshot, record=record)

        view = await self.load_recovery_view(
            org_id=org_id,
            run_id=record.run_id,
            subject_fingerprint=subject_fingerprint,
        )
        existing = self._identical_prior(view=view, record=record)
        if existing is not None:
            return existing
        self._validate_candidate(snapshot=snapshot, view=view, record=record)

        write = BatchJournalWrite(
            org_id=org_id,
            trace_id=trace_id,
            subject_fingerprint=subject_fingerprint,
            record=record,
        )
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
            return concurrent

        durable = self._record_from_event(
            envelope,
            expected_conversation_id=snapshot.conversation_id,
        )
        if durable.record_digest != record.record_digest:
            raise BatchJournalCorruption(
                run_id=record.run_id,
                reason="appended F6 record does not match its canonical digest",
            )
        return SequencedBatchJournalRecord(
            sequence_no=envelope.sequence_no,
            record=durable,
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

    @staticmethod
    def _identical_prior(
        *,
        view: BatchRecoveryView,
        record: BatchJournalRecord,
    ) -> SequencedBatchJournalRecord | None:
        """Return an identical prior append, or refuse a changed one."""

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
            raise BatchJournalConflict(
                run_id=record.run_id,
                record_id=record.record_id,
            )
        return durable

    @classmethod
    def _validate_candidate(
        cls,
        *,
        snapshot: RunControlSnapshot,
        view: BatchRecoveryView,
        record: BatchJournalRecord,
    ) -> None:
        """Apply the replay rules to a record that has not been written yet."""

        cls._validate_replay(
            snapshot=snapshot,
            records=(
                *view.records,
                SequencedBatchJournalRecord(
                    sequence_no=_next_sequence(view),
                    record=record,
                ),
            ),
        )

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
        """Refuse any journal that could not have been produced legitimately.

        The child-transition rules below are the durable half of "nothing runs
        ahead of the plan".  Because they are checked in *sequence* order on
        every replay, a journal in which a child started before its batch was
        ordered, a child the plan never named ran, or a child settled without
        ever declaring an intent cannot be recovered from at all — it is
        corruption, not a recoverable state.
        """

        if any(
            left.sequence_no >= right.sequence_no
            for left, right in zip(records, records[1:], strict=False)
        ):
            raise BatchJournalCorruption(
                run_id=snapshot.run_id,
                reason="F6 event sequence is not strictly increasing",
            )
        seen_records: set[str] = set()
        planned: dict[str, frozenset[str]] = {}
        intents: set[tuple[str, str]] = set()
        for item in records:
            record = item.record
            cls._validate_snapshot_binding(snapshot=snapshot, record=record)
            if record.record_id in seen_records:
                raise BatchJournalCorruption(
                    run_id=snapshot.run_id,
                    reason="duplicate F6 record identity",
                )
            seen_records.add(record.record_id)
            if isinstance(record, BatchPlanBoundRecord):
                if record.batch_id in planned:
                    raise BatchJournalCorruption(
                        run_id=snapshot.run_id,
                        reason="a batch bound more than one plan",
                    )
                planned[record.batch_id] = frozenset(
                    record.rebuild_plan().operation_ids
                )
                continue
            cls._validate_transition(
                snapshot=snapshot,
                record=record,
                planned=planned,
                intents=intents,
            )

    @staticmethod
    def _validate_transition(
        *,
        snapshot: RunControlSnapshot,
        record: BatchChildTransitionRecord,
        planned: dict[str, frozenset[str]],
        intents: set[tuple[str, str]],
    ) -> None:
        """Check one child fact against the plan prefix that precedes it."""

        operations = planned.get(record.batch_id)
        if operations is None:
            raise BatchJournalCorruption(
                run_id=snapshot.run_id,
                reason="a child transition precedes its batch plan",
            )
        if record.operation_id not in operations:
            raise BatchJournalCorruption(
                run_id=snapshot.run_id,
                reason="a child transition names an unplanned operation",
            )
        child = (record.batch_id, record.operation_id)
        if record.phase is BatchChildPhase.DISPATCH_INTENT:
            intents.add(child)
            return
        if child not in intents:
            raise BatchJournalCorruption(
                run_id=snapshot.run_id,
                reason="a child settled without a durable dispatch intent",
            )

    @staticmethod
    def _validate_snapshot_binding(
        *,
        snapshot: RunControlSnapshot,
        record: BatchJournalRecord,
    ) -> None:
        """Refuse a record that does not bind this run's frozen control snapshot.

        The concurrency policy revision is only checked on the plan record: it
        is the record that made a *decision* under that revision.  A child
        transition records what happened to work the plan already ordered, and
        binding it to a revision it never consulted would be a second, weaker
        copy of a fact the plan already holds authoritatively.
        """

        if (
            record.run_id != snapshot.run_id
            or record.snapshot_id != snapshot.snapshot_id
        ):
            raise BatchJournalSnapshotConflict(run_id=record.run_id)
        if (
            isinstance(record, BatchPlanBoundRecord)
            and record.concurrency_policy_revision
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
