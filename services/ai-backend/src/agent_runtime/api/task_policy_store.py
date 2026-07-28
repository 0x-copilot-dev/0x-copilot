"""Canonical run-event adapter for F4 task-policy controller facts."""

from __future__ import annotations

import hashlib

from pydantic import ValidationError

from agent_runtime.api.ports import EventStorePort
from agent_runtime.capabilities.task_policy_journal import (
    SequencedTaskPolicyJournalRecord,
    TaskPolicyAdmissionRecordedRecord,
    TaskPolicyBudgetRecordedRecord,
    TaskPolicyFeedbackRecordedRecord,
    TaskPolicyIntentRecordedRecord,
    TaskPolicyJournalConflict,
    TaskPolicyJournalCorruption,
    TaskPolicyJournalRecord,
    TaskPolicyJournalRecordKind,
    TaskPolicyJournalScopeConflict,
    TaskPolicyJournalSnapshotConflict,
    TaskPolicyJournalStorePort,
    TaskPolicyJournalWrite,
    TaskPolicyOutcomeRecordedRecord,
    TaskPolicyPlanBoundRecord,
    TaskPolicyProfileSelectedRecord,
    TaskPolicyProgressRecordedRecord,
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
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
    TaskPolicyJournalPayload,
)


class EventJournalTaskPolicyStore(TaskPolicyJournalStorePort):
    """Fold strict F4 records from the existing tenant-scoped run event log."""

    def __init__(
        self,
        *,
        events: EventStorePort,
        snapshots: RunControlSnapshotStorePort,
    ) -> None:
        self._events = events
        self._snapshots = snapshots

    async def append(
        self,
        write: TaskPolicyJournalWrite,
    ) -> SequencedTaskPolicyJournalRecord:
        record = write.record
        snapshot = await self._load_snapshot(
            org_id=write.org_id,
            run_id=record.run_id,
            subject_fingerprint=write.subject_fingerprint,
        )
        if snapshot is None:
            raise TaskPolicyJournalCorruption(
                run_id=record.run_id,
                reason="a task-policy record cannot precede the control snapshot",
            )
        self._validate_snapshot_binding(snapshot=snapshot, record=record)

        # Validate the durable prefix before extending it.  The returned view
        # also proves that referenced intent/plan/budget facts already exist.
        prefix = await self.list_for_run(
            org_id=write.org_id,
            run_id=record.run_id,
            subject_fingerprint=write.subject_fingerprint,
        )
        existing_record = next(
            (item for item in prefix if item.record.record_id == record.record_id),
            None,
        )
        if existing_record is not None:
            if existing_record.record.record_digest != record.record_digest:
                raise TaskPolicyJournalConflict(
                    run_id=record.run_id,
                    record_id=record.record_id,
                )
            return existing_record
        self._validate_next_record(
            run_id=record.run_id,
            records=prefix,
            candidate=record,
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
            event_type=RuntimeApiEventType.TOOL_POLICY_JOURNAL,
            activity_kind=RuntimeActivityKind.EVENT,
            visibility=RuntimeEventVisibility.INTERNAL,
            redaction_state=RuntimeEventRedactionState.REDACTED,
            payload=self._payload(record),
        )
        try:
            envelope = await self._events.append_event(draft)
        except RuntimeEventIdempotencyConflict:
            existing = await self._record_by_event_id(
                org_id=write.org_id,
                run_id=record.run_id,
                event_id=event_id,
                expected_conversation_id=snapshot.conversation_id,
            )
            if existing is None:
                raise TaskPolicyJournalCorruption(
                    run_id=record.run_id,
                    reason="stable F4 event conflicted but was not readable",
                ) from None
            if existing.record.record_digest != record.record_digest:
                raise TaskPolicyJournalConflict(
                    run_id=record.run_id,
                    record_id=record.record_id,
                ) from None
            return existing

        durable = self._record_from_event(
            envelope,
            expected_conversation_id=snapshot.conversation_id,
        )
        if durable.record_digest != record.record_digest:
            raise TaskPolicyJournalCorruption(
                run_id=record.run_id,
                reason="appended F4 record does not match its canonical digest",
            )
        return SequencedTaskPolicyJournalRecord(
            sequence_no=envelope.sequence_no,
            record=durable,
        )

    async def list_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        after_sequence: int = 0,
    ) -> tuple[SequencedTaskPolicyJournalRecord, ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        snapshot = await self._load_snapshot(
            org_id=org_id,
            run_id=run_id,
            subject_fingerprint=subject_fingerprint,
        )
        if snapshot is None:
            return ()
        events = await self._events.list_events_after(
            org_id=org_id,
            run_id=run_id,
            after_sequence=0,
        )
        records = tuple(
            SequencedTaskPolicyJournalRecord(
                sequence_no=event.sequence_no,
                record=self._record_from_event(
                    event,
                    expected_conversation_id=snapshot.conversation_id,
                ),
            )
            for event in events
            if event.event_type is RuntimeApiEventType.TOOL_POLICY_JOURNAL
        )
        self._validate_replay(snapshot=snapshot, records=records)
        return tuple(item for item in records if item.sequence_no > after_sequence)

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
            raise TaskPolicyJournalScopeConflict(run_id=run_id) from exc
        except RunControlJournalCorruption as exc:
            raise TaskPolicyJournalCorruption(
                run_id=run_id,
                reason="the bound control snapshot is not replayable",
            ) from exc

    async def _record_by_event_id(
        self,
        *,
        org_id: str,
        run_id: str,
        event_id: str,
        expected_conversation_id: str,
    ) -> SequencedTaskPolicyJournalRecord | None:
        events = await self._events.list_events_after(
            org_id=org_id,
            run_id=run_id,
            after_sequence=0,
        )
        matches = tuple(
            SequencedTaskPolicyJournalRecord(
                sequence_no=event.sequence_no,
                record=self._record_from_event(
                    event,
                    expected_conversation_id=expected_conversation_id,
                ),
            )
            for event in events
            if event.event_type is RuntimeApiEventType.TOOL_POLICY_JOURNAL
            and event.event_id == event_id
        )
        if len(matches) > 1:
            raise TaskPolicyJournalCorruption(
                run_id=run_id,
                reason="duplicate stable F4 event identity",
            )
        return matches[0] if matches else None

    @classmethod
    def _record_from_event(
        cls,
        event: RuntimeEventEnvelope,
        *,
        expected_conversation_id: str,
    ) -> TaskPolicyJournalRecord:
        try:
            payload = TaskPolicyJournalPayload.model_validate(event.payload)
        except ValidationError as exc:
            raise TaskPolicyJournalCorruption(
                run_id=event.run_id,
                reason="malformed tool_policy.journal.v1 payload",
            ) from exc
        record = payload.record
        if event.run_id != record.run_id:
            raise TaskPolicyJournalCorruption(
                run_id=event.run_id,
                reason="F4 record run identity does not match its journal",
            )
        if event.conversation_id != expected_conversation_id:
            raise TaskPolicyJournalCorruption(
                run_id=event.run_id,
                reason="F4 event conversation identity does not match its run snapshot",
            )
        if event.event_id != cls._stable_event_id(record):
            raise TaskPolicyJournalCorruption(
                run_id=event.run_id,
                reason="F4 event does not use its stable identity",
            )
        if event.source is not StreamEventSource.RUNTIME:
            raise TaskPolicyJournalCorruption(
                run_id=event.run_id,
                reason="F4 event has a non-runtime source",
            )
        if event.activity_kind is not RuntimeActivityKind.EVENT:
            raise TaskPolicyJournalCorruption(
                run_id=event.run_id,
                reason="F4 event has a user activity projection",
            )
        if event.visibility is not RuntimeEventVisibility.INTERNAL:
            raise TaskPolicyJournalCorruption(
                run_id=event.run_id,
                reason="F4 event is not internal",
            )
        if event.redaction_state is not RuntimeEventRedactionState.REDACTED:
            raise TaskPolicyJournalCorruption(
                run_id=event.run_id,
                reason="F4 event is not body-free/redacted",
            )
        if event.created_at != record.created_at:
            raise TaskPolicyJournalCorruption(
                run_id=event.run_id,
                reason="F4 event time does not match the canonical record",
            )
        if event.metadata:
            raise TaskPolicyJournalCorruption(
                run_id=event.run_id,
                reason="F4 event contains non-canonical metadata",
            )
        if any(
            value is not None
            for value in (
                event.parent_task_id,
                event.task_id,
                event.subagent_id,
                event.display_title,
                event.summary,
            )
        ):
            raise TaskPolicyJournalCorruption(
                run_id=event.run_id,
                reason="F4 event contains non-canonical presentation data",
            )
        if event.payload != cls._payload(record):
            raise TaskPolicyJournalCorruption(
                run_id=event.run_id,
                reason="F4 event mirrors do not match the canonical record",
            )
        return record

    @classmethod
    def _validate_replay(
        cls,
        *,
        snapshot: RunControlSnapshot,
        records: tuple[SequencedTaskPolicyJournalRecord, ...],
    ) -> None:
        if any(
            left.sequence_no >= right.sequence_no
            for left, right in zip(records, records[1:], strict=False)
        ):
            raise TaskPolicyJournalCorruption(
                run_id=snapshot.run_id,
                reason="F4 event sequence is not strictly increasing",
            )
        seen_ids: set[str] = set()
        prefix: list[SequencedTaskPolicyJournalRecord] = []
        for item in records:
            record = item.record
            cls._validate_snapshot_binding(snapshot=snapshot, record=record)
            if record.record_id in seen_ids:
                raise TaskPolicyJournalCorruption(
                    run_id=snapshot.run_id,
                    reason="duplicate F4 record identity",
                )
            cls._validate_next_record(
                run_id=snapshot.run_id,
                records=tuple(prefix),
                candidate=record,
                replay=True,
            )
            seen_ids.add(record.record_id)
            prefix.append(item)

    @staticmethod
    def _validate_snapshot_binding(
        *,
        snapshot: RunControlSnapshot,
        record: TaskPolicyJournalRecord,
    ) -> None:
        if (
            record.run_id != snapshot.run_id
            or record.snapshot_id != snapshot.snapshot_id
        ):
            raise TaskPolicyJournalSnapshotConflict(run_id=record.run_id)
        if (
            isinstance(
                record,
                (
                    TaskPolicyProfileSelectedRecord,
                    TaskPolicyPlanBoundRecord,
                    TaskPolicyIntentRecordedRecord,
                ),
            )
            and record.selection_ref != snapshot.task_policy_selection_ref
        ):
            raise TaskPolicyJournalSnapshotConflict(run_id=record.run_id)

    @classmethod
    def _validate_next_record(
        cls,
        *,
        run_id: str,
        records: tuple[SequencedTaskPolicyJournalRecord, ...],
        candidate: TaskPolicyJournalRecord,
        replay: bool = False,
    ) -> None:
        prior = tuple(item.record for item in records)
        profiles = tuple(
            item for item in prior if isinstance(item, TaskPolicyProfileSelectedRecord)
        )
        if isinstance(candidate, TaskPolicyProfileSelectedRecord):
            if profiles:
                cls._invalid(
                    run_id=run_id,
                    record_id=candidate.record_id,
                    reason="more than one profile selection was recorded",
                    replay=replay,
                )
            if prior:
                cls._invalid(
                    run_id=run_id,
                    record_id=candidate.record_id,
                    reason="profile selection is not the first F4 record",
                    replay=replay,
                )
            return
        if len(profiles) != 1:
            cls._invalid(
                run_id=run_id,
                record_id=candidate.record_id,
                reason="F4 record does not follow exactly one profile selection",
                replay=replay,
            )
        profile = profiles[0]
        selection_digest = getattr(candidate, "selection_digest", None)
        if (
            selection_digest is not None
            and selection_digest != profile.selection_digest
        ):
            cls._invalid(
                run_id=run_id,
                record_id=candidate.record_id,
                reason="F4 record selection digest does not match the profile binding",
                replay=replay,
            )

        plans = {
            item.plan_id: item
            for item in prior
            if isinstance(item, TaskPolicyPlanBoundRecord)
        }
        intents = {
            item.record_id: item
            for item in prior
            if isinstance(item, TaskPolicyIntentRecordedRecord)
        }
        admissions = {
            item.record_id: item
            for item in prior
            if isinstance(item, TaskPolicyAdmissionRecordedRecord)
        }
        outcomes = {
            item.record_id: item
            for item in prior
            if isinstance(item, TaskPolicyOutcomeRecordedRecord)
        }
        budgets = {
            item.record_id: item
            for item in prior
            if isinstance(item, TaskPolicyBudgetRecordedRecord)
        }

        if isinstance(candidate, TaskPolicyPlanBoundRecord):
            existing = plans.get(candidate.plan_id)
            if existing is not None and existing.plan_digest != candidate.plan_digest:
                cls._invalid(
                    run_id=run_id,
                    record_id=candidate.record_id,
                    reason="plan identity was rebound to a different digest",
                    replay=replay,
                )
        elif isinstance(candidate, TaskPolicyIntentRecordedRecord):
            if candidate.plan_id is not None:
                plan = plans.get(candidate.plan_id)
                if plan is None or plan.plan_digest != candidate.plan_digest:
                    cls._invalid(
                        run_id=run_id,
                        record_id=candidate.record_id,
                        reason="intent refers to an unknown or different plan",
                        replay=replay,
                    )
        elif isinstance(
            candidate,
            (TaskPolicyAdmissionRecordedRecord, TaskPolicyOutcomeRecordedRecord),
        ):
            intent = intents.get(candidate.intent_record_id)
            if (
                intent is None
                or intent.record_digest != candidate.intent_digest
                or intent.operation_id != candidate.operation_id
                or intent.tool_call_id != candidate.tool_call_id
            ):
                cls._invalid(
                    run_id=run_id,
                    record_id=candidate.record_id,
                    reason="decision/outcome refers to an unknown or different intent",
                    replay=replay,
                )
        elif isinstance(candidate, TaskPolicyFeedbackRecordedRecord):
            admission = admissions.get(candidate.admission_record_id)
            if (
                admission is None
                or admission.operation_id != candidate.operation_id
                or admission.tool_call_id != candidate.tool_call_id
            ):
                cls._invalid(
                    run_id=run_id,
                    record_id=candidate.record_id,
                    reason="feedback refers to an unknown admission",
                    replay=replay,
                )
            if candidate.outcome_record_id is not None:
                outcome = outcomes.get(candidate.outcome_record_id)
                if (
                    outcome is None
                    or outcome.operation_id != candidate.operation_id
                    or outcome.tool_call_id != candidate.tool_call_id
                ):
                    cls._invalid(
                        run_id=run_id,
                        record_id=candidate.record_id,
                        reason="feedback refers to an unknown outcome",
                        replay=replay,
                    )
            if candidate.budget_record_id is not None:
                budget = budgets.get(candidate.budget_record_id)
                if budget is None or budget.record_digest != candidate.budget_digest:
                    cls._invalid(
                        run_id=run_id,
                        record_id=candidate.record_id,
                        reason="feedback refers to an unknown budget fact",
                        replay=replay,
                    )
        elif isinstance(candidate, TaskPolicyProgressRecordedRecord):
            plan = plans.get(candidate.plan_id)
            if plan is None or plan.plan_digest != candidate.plan_digest:
                cls._invalid(
                    run_id=run_id,
                    record_id=candidate.record_id,
                    reason="progress refers to an unknown or different plan",
                    replay=replay,
                )

    @staticmethod
    def _invalid(
        *,
        run_id: str,
        record_id: str,
        reason: str,
        replay: bool,
    ) -> None:
        if replay:
            raise TaskPolicyJournalCorruption(run_id=run_id, reason=reason)
        raise TaskPolicyJournalConflict(run_id=run_id, record_id=record_id)

    @staticmethod
    def _payload(record: TaskPolicyJournalRecord) -> dict[str, object]:
        return TaskPolicyJournalPayload(record=record).model_dump(mode="json")

    @staticmethod
    def _stable_event_id(record: TaskPolicyJournalRecord) -> str:
        # A run can bind exactly one profile selection even if racing builders
        # choose different record IDs.  All other facts key by their stable
        # domain identity within the run.
        identity = (
            f"{record.run_id}:profile"
            if record.record_kind is TaskPolicyJournalRecordKind.PROFILE_SELECTED
            else f"{record.run_id}:{record.record_id}"
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"tool_policy:{digest}"


__all__ = ["EventJournalTaskPolicyStore"]
