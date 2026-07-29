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

from collections.abc import Sequence
from dataclasses import dataclass, field
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


@dataclass(slots=True)
class _RunJournal:
    """One run's F6 prefix, folded once and carried forward across appends.

    This is a *projection*, not a cache of answers: it holds exactly the state
    the replay rules fold left-to-right over the journal, so extending it by
    one record applies the same checks reading the whole journal would.  It
    exists because the alternative — re-reading every event the run has ever
    produced before each append — made F6's cost quadratic in appends and made
    it scale with model deltas and tool events F6 does not even look at.

    ``read_through`` is the contract that keeps it honest: the projection has
    folded the run's log *contiguously* up to that sequence and no further, so
    the next read asks only for what comes after it.  Nothing here is ever
    trusted across a process boundary — a new store instance starts empty and
    rebuilds from the durable log, which is what recovery and restart do.
    """

    snapshot: RunControlSnapshot
    read_through: int = 0
    records: list[SequencedBatchJournalRecord] = field(default_factory=list)
    by_record_id: dict[str, SequencedBatchJournalRecord] = field(default_factory=dict)
    by_event_id: dict[str, SequencedBatchJournalRecord] = field(default_factory=dict)
    planned: dict[str, frozenset[str]] = field(default_factory=dict)
    intents: set[tuple[str, str]] = field(default_factory=set)
    last_sequence: int = 0
    bindings: int = 0

    @property
    def next_sequence(self) -> int:
        """Return a provisional sequence one past the durable prefix.

        Only ever used to place a not-yet-written candidate at the end of the
        journal for validation. The real sequence is assigned by the event
        store.
        """

        return self.last_sequence + 1


class EventJournalBatchPlanStore(BatchPlanStorePort):
    """Fold strict F6 plans from the existing tenant-scoped run event log."""

    EVENT_TYPE: ClassVar[RuntimeApiEventType] = (
        RuntimeApiEventType.OPERATION_BATCH_JOURNAL
    )
    EVENT_ID_PREFIX: ClassVar[str] = "operation_batch"
    SNAPSHOT_EVENT_TYPE: ClassVar[RuntimeApiEventType] = (
        RuntimeApiEventType.QUALITY_CONTROL_BOUND
    )

    def __init__(
        self,
        *,
        events: EventStorePort,
        snapshots: RunControlSnapshotStorePort,
    ) -> None:
        self._events = events
        self._snapshots = snapshots
        self._journals: dict[tuple[str, str, str], _RunJournal] = {}

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

        journal = await self._synced(
            org_id=org_id,
            run_id=record.run_id,
            subject_fingerprint=subject_fingerprint,
        )
        if journal is None:
            raise BatchJournalCorruption(
                run_id=record.run_id,
                reason=missing_snapshot_reason,
            )
        snapshot = journal.snapshot
        self._validate_snapshot_binding(snapshot=snapshot, record=record)

        existing = journal.by_record_id.get(record.record_id)
        if existing is not None:
            return self._identical_prior(existing=existing, record=record)
        self._validate_candidate(journal=journal, record=record)

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
                subject_fingerprint=subject_fingerprint,
                event_id=event_id,
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
        return self._fold(journal=journal, event=envelope, record=durable)

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
        journal = await self._synced(
            org_id=org_id,
            run_id=run_id,
            subject_fingerprint=subject_fingerprint,
        )
        if journal is None:
            raise BatchJournalCorruption(
                run_id=run_id,
                reason="a batch journal cannot be recovered without its snapshot",
            )
        return BatchRecoveryView(
            run_id=journal.snapshot.run_id,
            snapshot_id=journal.snapshot.snapshot_id,
            records=tuple(
                item for item in journal.records if item.sequence_no > after_sequence
            ),
        )

    @staticmethod
    def _identical_prior(
        *,
        existing: SequencedBatchJournalRecord,
        record: BatchJournalRecord,
    ) -> SequencedBatchJournalRecord:
        """Return an identical prior append, or refuse a changed one."""

        if existing.record.record_digest != record.record_digest:
            raise BatchJournalConflict(
                run_id=record.run_id,
                record_id=record.record_id,
            )
        return existing

    @classmethod
    def _validate_candidate(
        cls,
        *,
        journal: _RunJournal,
        record: BatchJournalRecord,
    ) -> None:
        """Apply the replay rules to a record that has not been written yet.

        The candidate is checked against the folded prefix and *not* folded
        into it: a record that has not reached the store is not part of the
        journal, and a refused one must leave no trace behind.
        """

        cls._admit(
            journal=journal,
            item=SequencedBatchJournalRecord(
                sequence_no=journal.next_sequence,
                record=record,
            ),
        )

    async def _synced(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
    ) -> _RunJournal | None:
        """Return this run's projection, folded up to the log's visible end.

        The control snapshot is loaded once per run rather than once per
        append.  That is not an optimisation of a mutable read: the snapshot is
        bound exactly once under a stable event id, a divergent rebind is
        refused by the control store, and :meth:`_advance` drops this whole
        projection the moment a second binding appears in the log — so the
        memo can never serve a value the durable journal disagrees with.
        """

        key = (org_id, run_id, subject_fingerprint)
        for _attempt in range(2):
            journal = self._journals.get(key)
            if journal is None:
                snapshot = await self._load_snapshot(
                    org_id=org_id,
                    run_id=run_id,
                    subject_fingerprint=subject_fingerprint,
                )
                if snapshot is None:
                    return None
                journal = _RunJournal(snapshot=snapshot)
                self._journals[key] = journal
            if await self._advance(journal=journal, org_id=org_id):
                return journal
            # The run bound a second control snapshot. Nothing folded under the
            # old one may be trusted, so discard it and rebuild from the log —
            # where the control store is the authority on what that means.
            del self._journals[key]
        raise BatchJournalCorruption(
            run_id=run_id,
            reason="the bound control snapshot is not replayable",
        )

    async def _advance(self, *, journal: _RunJournal, org_id: str) -> bool:
        """Fold every event this run appended since the last read.

        Returns ``False`` when the run rebound its control snapshot, which
        invalidates the projection rather than extending it.

        The fold stops at the first gap in the sequence.  Sequence allocation
        is not the same thing as commit order — the postgres adapter appends
        without a per-run row lock, so a later event can become visible while
        its predecessor is still in flight.  Folding past that hole would drop
        the missing record from this projection for good; stopping means the
        next read covers it.
        """

        events = await self._events.list_events_after(
            org_id=org_id,
            run_id=journal.snapshot.run_id,
            after_sequence=journal.read_through,
        )
        expected = self._first_expected(journal=journal, events=events)
        for event in events:
            if event.sequence_no != expected:
                return True
            if event.event_type is self.SNAPSHOT_EVENT_TYPE:
                # The run's own binding is expected — it is the event the
                # memoized snapshot was read from. A *second* one means the
                # snapshot this projection folded under is no longer the only
                # answer, which only the control store may adjudicate.
                journal.bindings += 1
                if journal.bindings > 1:
                    return False
            if event.event_type is self.EVENT_TYPE:
                self._fold(
                    journal=journal,
                    event=event,
                    record=self._record_from_event(
                        event,
                        expected_conversation_id=journal.snapshot.conversation_id,
                    ),
                )
            journal.read_through = event.sequence_no
            expected += 1
        return True

    @staticmethod
    def _first_expected(
        *,
        journal: _RunJournal,
        events: Sequence[RuntimeEventEnvelope],
    ) -> int:
        """Return the sequence the next folded event must carry.

        A projection that has folded nothing anchors on whatever the store
        hands back first, so a run whose earliest events retention has already
        removed still folds — reading exactly what a full replay would read.
        Once anything has been folded, the contiguity rule takes over.
        """

        if journal.read_through or not events:
            return journal.read_through + 1
        return events[0].sequence_no

    def _fold(
        self,
        *,
        journal: _RunJournal,
        event: RuntimeEventEnvelope,
        record: BatchJournalRecord,
    ) -> SequencedBatchJournalRecord:
        """Extend the folded prefix by one durable F6 record, checking it first."""

        item = SequencedBatchJournalRecord(
            sequence_no=event.sequence_no,
            record=record,
        )
        self._admit(journal=journal, item=item)
        if event.event_id in journal.by_event_id:
            raise BatchJournalCorruption(
                run_id=journal.snapshot.run_id,
                reason="duplicate stable F6 event identity",
            )
        self._commit(journal=journal, item=item)
        journal.by_event_id[event.event_id] = item
        # Advancing across our own append is only safe when nothing landed
        # between the last read and it: the store allocates per-run sequences
        # without gaps, so a contiguous sequence proves no concurrent writer
        # slipped a record in that this projection has not seen.
        if event.sequence_no == journal.read_through + 1:
            journal.read_through = event.sequence_no
        return item

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

    async def _record_by_event_id(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        event_id: str,
    ) -> SequencedBatchJournalRecord | None:
        """Read the record a concurrent writer put at this stable identity.

        Reached only when the store refused our append because that identity
        was already taken, which means the winner's record is by definition one
        this projection has not folded.  So the projection is discarded and
        rebuilt from the durable log: a losing racer must never answer from
        state that predates the race it just lost.
        """

        self._journals.pop((org_id, run_id, subject_fingerprint), None)
        journal = await self._synced(
            org_id=org_id,
            run_id=run_id,
            subject_fingerprint=subject_fingerprint,
        )
        if journal is None:
            return None
        return journal.by_event_id.get(event_id)

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

        The rules live in :meth:`_admit` and are applied here by folding this
        journal through a throwaway projection, one record at a time in
        sequence order.  There is deliberately no second copy of them: the
        checks a restart applies to a durable journal and the checks an append
        applies to a candidate are the same checks, so they cannot drift.
        """

        journal = _RunJournal(snapshot=snapshot)
        for item in records:
            cls._admit(journal=journal, item=item)
            cls._commit(journal=journal, item=item)

    @staticmethod
    def _commit(*, journal: _RunJournal, item: SequencedBatchJournalRecord) -> None:
        """Extend the folded prefix by one record :meth:`_admit` accepted."""

        record = item.record
        journal.records.append(item)
        journal.by_record_id[record.record_id] = item
        journal.last_sequence = item.sequence_no
        if isinstance(record, BatchPlanBoundRecord):
            journal.planned[record.batch_id] = frozenset(
                record.rebuild_plan().operation_ids
            )
        elif record.phase is BatchChildPhase.DISPATCH_INTENT:
            journal.intents.add((record.batch_id, record.operation_id))

    @classmethod
    def _admit(cls, *, journal: _RunJournal, item: SequencedBatchJournalRecord) -> None:
        """Check one record against the folded prefix that precedes it.

        Pure by construction — it decides, it never records.  That is what lets
        the same rules serve replay, where the record is already durable, and
        an append, where refusing must leave the projection untouched.

        The child-transition rules are the durable half of "nothing runs ahead
        of the plan".  Because they are checked in *sequence* order, a journal
        in which a child started before its batch was ordered, a child the plan
        never named ran, or a child settled without ever declaring an intent
        cannot be recovered from at all — it is corruption, not a recoverable
        state.
        """

        snapshot = journal.snapshot
        record = item.record
        if item.sequence_no <= journal.last_sequence:
            raise BatchJournalCorruption(
                run_id=snapshot.run_id,
                reason="F6 event sequence is not strictly increasing",
            )
        cls._validate_snapshot_binding(snapshot=snapshot, record=record)
        if record.record_id in journal.by_record_id:
            raise BatchJournalCorruption(
                run_id=snapshot.run_id,
                reason="duplicate F6 record identity",
            )
        if isinstance(record, BatchPlanBoundRecord):
            if record.batch_id in journal.planned:
                raise BatchJournalCorruption(
                    run_id=snapshot.run_id,
                    reason="a batch bound more than one plan",
                )
            return
        cls._validate_transition(journal=journal, record=record)

    @staticmethod
    def _validate_transition(
        *,
        journal: _RunJournal,
        record: BatchChildTransitionRecord,
    ) -> None:
        """Check one child fact against the plan prefix that precedes it."""

        snapshot = journal.snapshot
        operations = journal.planned.get(record.batch_id)
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
        if record.phase is BatchChildPhase.DISPATCH_INTENT:
            return
        if (record.batch_id, record.operation_id) not in journal.intents:
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
