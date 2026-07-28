"""Canonical run-event adapter for F10.3 model-invocation lineage."""

from __future__ import annotations

import hashlib

from pydantic import ValidationError

from agent_runtime.api.ports import EventStorePort
from agent_runtime.control_plane.contracts import RunControlSnapshot
from agent_runtime.control_plane.ports import (
    RunControlJournalCorruption,
    RunControlScopeConflict,
    RunControlSnapshotStorePort,
)
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.model_invocation.journal import (
    ModelAttemptAdmissionRecord,
    ModelAttemptFailedRecord,
    ModelAttemptLifecycleState,
    ModelAttemptStateRecord,
    ModelAttemptUsageRecord,
    ModelInvocationCompletedRecord,
    ModelInvocationConflict,
    ModelInvocationCorruption,
    ModelInvocationFailedRecord,
    ModelInvocationPlannedRecord,
    ModelInvocationRecord,
    ModelInvocationRecoveryRecord,
    ModelInvocationScopeConflict,
    ModelInvocationSnapshotConflict,
    ModelInvocationStorePort,
    ModelInvocationWrite,
    ModelRecoveryKind,
    ModelRecoveryOutcome,
    ModelRouteEligibleRecord,
    ModelRouteExcludedRecord,
    SequencedModelInvocationRecord,
)
from agent_runtime.execution.model_invocation.contracts import (
    ModelAttemptDecisionKind,
    ModelAttemptDecisionReason,
    ModelFailureClass,
    ModelInvocationBudget,
    ModelRouteEntry,
    ModelRouteExclusion,
    ModelRoutePlan,
)
from agent_runtime.persistence.ports import RuntimeEventIdempotencyConflict
from runtime_api.schemas import (
    ModelInvocationJournalPayload,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)


class EventJournalModelInvocationStore(ModelInvocationStorePort):
    """Persist and fail-closed replay F10.3 records through ``EventStorePort``."""

    _EVENT_TYPE_BY_KIND = {
        "invocation_planned": RuntimeApiEventType.MODEL_INVOCATION_PLANNED,
        "route_eligible": RuntimeApiEventType.MODEL_INVOCATION_ROUTE,
        "route_excluded": RuntimeApiEventType.MODEL_INVOCATION_EXCLUSION,
        "attempt_admission": RuntimeApiEventType.MODEL_ATTEMPT_ADMISSION,
        "attempt_state": RuntimeApiEventType.MODEL_ATTEMPT_STATE,
        "attempt_usage": RuntimeApiEventType.MODEL_ATTEMPT_USAGE,
        "attempt_failed": RuntimeApiEventType.MODEL_ATTEMPT_FAILED,
        "invocation_recovery": RuntimeApiEventType.MODEL_INVOCATION_RECOVERY,
        "invocation_completed": RuntimeApiEventType.MODEL_INVOCATION_COMPLETED,
        "invocation_failed": RuntimeApiEventType.MODEL_INVOCATION_FAILED,
    }
    _EVENT_TYPES = frozenset(_EVENT_TYPE_BY_KIND.values())
    _STATE_RANK = {
        ModelAttemptLifecycleState.ADMITTED: 0,
        ModelAttemptLifecycleState.DISPATCHING: 1,
        ModelAttemptLifecycleState.ACCEPTED: 2,
        ModelAttemptLifecycleState.STREAM_STARTED: 3,
        ModelAttemptLifecycleState.VISIBLE_OUTPUT: 4,
        ModelAttemptLifecycleState.TOOL_CALL_CONTENT: 4,
        ModelAttemptLifecycleState.COMPLETED: 5,
        ModelAttemptLifecycleState.CANCELLED: 5,
        ModelAttemptLifecycleState.AMBIGUOUS: 5,
    }

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
        write: ModelInvocationWrite,
    ) -> SequencedModelInvocationRecord:
        record = write.record
        snapshot = await self._load_snapshot(
            org_id=write.org_id,
            run_id=record.run_id,
            subject_fingerprint=write.subject_fingerprint,
        )
        if snapshot is None:
            raise ModelInvocationCorruption(
                run_id=record.run_id,
                reason="an invocation record cannot precede the control snapshot",
            )
        self._validate_snapshot_binding(snapshot=snapshot, record=record)
        prefix = await self.list_for_run(
            org_id=write.org_id,
            run_id=record.run_id,
            subject_fingerprint=write.subject_fingerprint,
        )
        existing = next(
            (item for item in prefix if item.record.record_id == record.record_id),
            None,
        )
        if existing is not None:
            if existing.record.record_digest != record.record_digest:
                raise ModelInvocationConflict(
                    run_id=record.run_id,
                    record_id=record.record_id,
                )
            return existing
        self._validate_next(
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
            event_type=self._event_type(record),
            activity_kind=RuntimeActivityKind.EVENT,
            visibility=RuntimeEventVisibility.INTERNAL,
            redaction_state=RuntimeEventRedactionState.REDACTED,
            payload=self._payload(record),
        )
        try:
            envelope = await self._events.append_event(draft)
        except RuntimeEventIdempotencyConflict:
            durable = await self._record_by_event_id(
                org_id=write.org_id,
                run_id=record.run_id,
                event_id=event_id,
                expected_conversation_id=snapshot.conversation_id,
            )
            if durable is None:
                raise ModelInvocationCorruption(
                    run_id=record.run_id,
                    reason="stable F10.3 event conflicted but was not readable",
                ) from None
            if durable.record.record_digest != record.record_digest:
                raise ModelInvocationConflict(
                    run_id=record.run_id,
                    record_id=record.record_id,
                ) from None
            return durable

        durable_record = self._record_from_event(
            envelope,
            expected_conversation_id=snapshot.conversation_id,
        )
        if durable_record.record_digest != record.record_digest:
            raise ModelInvocationCorruption(
                run_id=record.run_id,
                reason="appended invocation record changed its canonical digest",
            )
        return SequencedModelInvocationRecord(
            sequence_no=envelope.sequence_no,
            record=durable_record,
        )

    async def list_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        after_sequence: int = 0,
    ) -> tuple[SequencedModelInvocationRecord, ...]:
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
            SequencedModelInvocationRecord(
                sequence_no=event.sequence_no,
                record=self._record_from_event(
                    event,
                    expected_conversation_id=snapshot.conversation_id,
                ),
            )
            for event in events
            if event.event_type in self._EVENT_TYPES
        )
        self._validate_replay(snapshot=snapshot, records=records)
        return tuple(item for item in records if item.sequence_no > after_sequence)

    async def list_for_invocation(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        invocation_id: str,
        after_sequence: int = 0,
    ) -> tuple[SequencedModelInvocationRecord, ...]:
        records = await self.list_for_run(
            org_id=org_id,
            run_id=run_id,
            subject_fingerprint=subject_fingerprint,
            after_sequence=after_sequence,
        )
        return tuple(
            item for item in records if item.record.invocation_id == invocation_id
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
            raise ModelInvocationScopeConflict(run_id=run_id) from exc
        except RunControlJournalCorruption as exc:
            raise ModelInvocationCorruption(
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
    ) -> SequencedModelInvocationRecord | None:
        events = await self._events.list_events_after(
            org_id=org_id,
            run_id=run_id,
            after_sequence=0,
        )
        matches = tuple(
            SequencedModelInvocationRecord(
                sequence_no=event.sequence_no,
                record=self._record_from_event(
                    event,
                    expected_conversation_id=expected_conversation_id,
                ),
            )
            for event in events
            if event.event_type in self._EVENT_TYPES and event.event_id == event_id
        )
        if len(matches) > 1:
            raise ModelInvocationCorruption(
                run_id=run_id,
                reason="duplicate stable F10.3 event identity",
            )
        return matches[0] if matches else None

    @classmethod
    def _record_from_event(
        cls,
        event: RuntimeEventEnvelope,
        *,
        expected_conversation_id: str,
    ) -> ModelInvocationRecord:
        try:
            record = ModelInvocationJournalPayload.model_validate(event.payload).record
        except ValidationError as exc:
            raise ModelInvocationCorruption(
                run_id=event.run_id,
                reason=f"malformed {event.event_type.value} payload",
            ) from exc
        expected_event_type = cls._EVENT_TYPE_BY_KIND.get(record.record_kind)
        if event.event_type is not expected_event_type:
            cls._corrupt(event, "F10.3 record kind does not match its event type")
        if event.run_id != record.run_id:
            cls._corrupt(event, "invocation run identity does not match its journal")
        if event.conversation_id != expected_conversation_id:
            cls._corrupt(
                event,
                "invocation conversation identity does not match its run snapshot",
            )
        if event.event_id != cls._stable_event_id(record):
            cls._corrupt(event, "invocation event does not use its stable identity")
        if event.source is not StreamEventSource.RUNTIME:
            cls._corrupt(event, "invocation event has a non-runtime source")
        if event.activity_kind is not RuntimeActivityKind.EVENT:
            cls._corrupt(event, "invocation event has a user activity projection")
        if event.visibility is not RuntimeEventVisibility.INTERNAL:
            cls._corrupt(event, "invocation event is not internal")
        if event.redaction_state is not RuntimeEventRedactionState.REDACTED:
            cls._corrupt(event, "invocation event is not body/secret-free")
        if event.created_at != record.created_at:
            cls._corrupt(event, "invocation event time differs from its record")
        if event.metadata:
            cls._corrupt(event, "invocation event contains non-canonical metadata")
        if any(
            value is not None
            for value in (
                event.parent_event_id,
                event.span_id,
                event.parent_span_id,
                event.parent_task_id,
                event.task_id,
                event.subagent_id,
                event.display_title,
                event.summary,
                event.status,
                event.presentation,
            )
        ):
            cls._corrupt(
                event,
                "invocation event contains non-canonical presentation data",
            )
        if event.payload != cls._payload(record):
            cls._corrupt(
                event,
                "invocation event payload differs from its canonical record",
            )
        return record

    @classmethod
    def _validate_replay(
        cls,
        *,
        snapshot: RunControlSnapshot,
        records: tuple[SequencedModelInvocationRecord, ...],
    ) -> None:
        if any(
            left.sequence_no >= right.sequence_no
            for left, right in zip(records, records[1:], strict=False)
        ):
            raise ModelInvocationCorruption(
                run_id=snapshot.run_id,
                reason="F10.3 event sequence is not strictly increasing",
            )
        seen_ids: set[str] = set()
        prefix: list[SequencedModelInvocationRecord] = []
        for item in records:
            cls._validate_snapshot_binding(snapshot=snapshot, record=item.record)
            if item.record.record_id in seen_ids:
                raise ModelInvocationCorruption(
                    run_id=snapshot.run_id,
                    reason="duplicate invocation record identity",
                )
            cls._validate_next(
                run_id=snapshot.run_id,
                records=tuple(prefix),
                candidate=item.record,
                replay=True,
            )
            seen_ids.add(item.record.record_id)
            prefix.append(item)

    @staticmethod
    def _validate_snapshot_binding(
        *,
        snapshot: RunControlSnapshot,
        record: ModelInvocationRecord,
    ) -> None:
        if (
            record.run_id != snapshot.run_id
            or record.snapshot_id != snapshot.snapshot_id
            or record.snapshot_digest != snapshot.snapshot_digest
        ):
            raise ModelInvocationSnapshotConflict(run_id=record.run_id)

    @classmethod
    def _validate_next(
        cls,
        *,
        run_id: str,
        records: tuple[SequencedModelInvocationRecord, ...],
        candidate: ModelInvocationRecord,
        replay: bool = False,
    ) -> None:
        prior_all = tuple(item.record for item in records)
        planned_by_call = {
            item.model_call_id: item
            for item in prior_all
            if isinstance(item, ModelInvocationPlannedRecord)
        }
        if isinstance(candidate, ModelInvocationPlannedRecord):
            if candidate.model_call_id in planned_by_call:
                cls._invalid(
                    run_id=run_id,
                    record_id=candidate.record_id,
                    reason="model call has more than one invocation identity",
                    replay=replay,
                )
            return

        invocation = next(
            (
                item
                for item in prior_all
                if isinstance(item, ModelInvocationPlannedRecord)
                and item.invocation_id == candidate.invocation_id
            ),
            None,
        )
        if invocation is None or (
            candidate.run_id != invocation.run_id
            or candidate.snapshot_id != invocation.snapshot_id
            or candidate.snapshot_digest != invocation.snapshot_digest
            or candidate.model_call_id != invocation.model_call_id
        ):
            cls._invalid(
                run_id=run_id,
                record_id=candidate.record_id,
                reason="record does not bind a prior invocation identity",
                replay=replay,
            )
            return

        prior = tuple(
            item for item in prior_all if item.invocation_id == candidate.invocation_id
        )
        terminals = tuple(
            item
            for item in prior
            if isinstance(
                item,
                (ModelInvocationCompletedRecord, ModelInvocationFailedRecord),
            )
        )
        if terminals:
            cls._invalid(
                run_id=run_id,
                record_id=candidate.record_id,
                reason="terminal invocation cannot accept another record",
                replay=replay,
            )
            return

        routes = tuple(
            item for item in prior if isinstance(item, ModelRouteEligibleRecord)
        )
        exclusions = tuple(
            item for item in prior if isinstance(item, ModelRouteExcludedRecord)
        )
        admissions = tuple(
            item for item in prior if isinstance(item, ModelAttemptAdmissionRecord)
        )
        admitted = tuple(
            item
            for item in admissions
            if item.decision is ModelAttemptDecisionKind.ADMIT
        )
        states = tuple(
            item for item in prior if isinstance(item, ModelAttemptStateRecord)
        )
        usages = tuple(
            item for item in prior if isinstance(item, ModelAttemptUsageRecord)
        )
        failures = tuple(
            item for item in prior if isinstance(item, ModelAttemptFailedRecord)
        )
        recoveries = tuple(
            item for item in prior if isinstance(item, ModelInvocationRecoveryRecord)
        )

        if isinstance(candidate, ModelRouteEligibleRecord):
            cls._validate_route(
                run_id=run_id,
                invocation=invocation,
                routes=routes,
                exclusions=exclusions,
                admissions=admissions,
                candidate=candidate,
                replay=replay,
            )
        elif isinstance(candidate, ModelRouteExcludedRecord):
            cls._validate_exclusion(
                run_id=run_id,
                invocation=invocation,
                routes=routes,
                exclusions=exclusions,
                admissions=admissions,
                candidate=candidate,
                replay=replay,
            )
        elif isinstance(candidate, ModelAttemptAdmissionRecord):
            cls._validate_admission(
                run_id=run_id,
                invocation=invocation,
                routes=routes,
                exclusions=exclusions,
                admissions=admissions,
                admitted=admitted,
                recoveries=recoveries,
                candidate=candidate,
                replay=replay,
            )
        elif isinstance(candidate, ModelAttemptStateRecord):
            cls._validate_state(
                run_id=run_id,
                admitted=admitted,
                states=states,
                failures=failures,
                candidate=candidate,
                replay=replay,
            )
        elif isinstance(candidate, ModelAttemptUsageRecord):
            cls._validate_usage(
                run_id=run_id,
                admitted=admitted,
                usages=usages,
                candidate=candidate,
                replay=replay,
            )
        elif isinstance(candidate, ModelAttemptFailedRecord):
            cls._validate_attempt_failure(
                run_id=run_id,
                admitted=admitted,
                states=states,
                failures=failures,
                candidate=candidate,
                replay=replay,
            )
        elif isinstance(candidate, ModelInvocationRecoveryRecord):
            cls._validate_recovery(
                run_id=run_id,
                failures=failures,
                states=states,
                usages=usages,
                recoveries=recoveries,
                candidate=candidate,
                replay=replay,
            )
        elif isinstance(candidate, ModelInvocationCompletedRecord):
            cls._validate_completion(
                run_id=run_id,
                admitted=admitted,
                states=states,
                usages=usages,
                recoveries=recoveries,
                candidate=candidate,
                replay=replay,
            )
        elif isinstance(candidate, ModelInvocationFailedRecord):
            cls._validate_invocation_failure(
                run_id=run_id,
                routes=routes,
                admissions=admissions,
                admitted=admitted,
                states=states,
                usages=usages,
                failures=failures,
                recoveries=recoveries,
                candidate=candidate,
                replay=replay,
            )

    @classmethod
    def _validate_route(
        cls,
        *,
        run_id: str,
        invocation: ModelInvocationPlannedRecord,
        routes: tuple[ModelRouteEligibleRecord, ...],
        exclusions: tuple[ModelRouteExcludedRecord, ...],
        admissions: tuple[ModelAttemptAdmissionRecord, ...],
        candidate: ModelRouteEligibleRecord,
        replay: bool,
    ) -> None:
        invalid = (
            bool(admissions)
            or candidate.route_plan_id != invocation.route_plan_id
            or candidate.route_digest != invocation.route_digest
            or candidate.route_ordinal != len(routes) + 1
            or candidate.route_ordinal > invocation.eligible_route_count
            or candidate.deployment_id
            in {
                *(route.deployment_id for route in routes),
                *(excluded.deployment_id for excluded in exclusions),
            }
        )
        if invalid:
            cls._invalid(
                run_id=run_id,
                record_id=candidate.record_id,
                reason="eligible route is late, duplicate, or outside its plan",
                replay=replay,
            )

    @classmethod
    def _validate_exclusion(
        cls,
        *,
        run_id: str,
        invocation: ModelInvocationPlannedRecord,
        routes: tuple[ModelRouteEligibleRecord, ...],
        exclusions: tuple[ModelRouteExcludedRecord, ...],
        admissions: tuple[ModelAttemptAdmissionRecord, ...],
        candidate: ModelRouteExcludedRecord,
        replay: bool,
    ) -> None:
        invalid = (
            bool(admissions)
            or candidate.route_plan_id != invocation.route_plan_id
            or candidate.route_digest != invocation.route_digest
            or len(exclusions) >= invocation.exclusion_count
            or candidate.deployment_id
            in {
                *(route.deployment_id for route in routes),
                *(excluded.deployment_id for excluded in exclusions),
            }
        )
        if invalid:
            cls._invalid(
                run_id=run_id,
                record_id=candidate.record_id,
                reason="route exclusion is late, duplicate, or outside its plan",
                replay=replay,
            )

    @classmethod
    def _validate_admission(
        cls,
        *,
        run_id: str,
        invocation: ModelInvocationPlannedRecord,
        routes: tuple[ModelRouteEligibleRecord, ...],
        exclusions: tuple[ModelRouteExcludedRecord, ...],
        admissions: tuple[ModelAttemptAdmissionRecord, ...],
        admitted: tuple[ModelAttemptAdmissionRecord, ...],
        recoveries: tuple[ModelInvocationRecoveryRecord, ...],
        candidate: ModelAttemptAdmissionRecord,
        replay: bool,
    ) -> None:
        expected_ordinal = len(admitted) + 1
        invalid = (
            len(routes) != invocation.eligible_route_count
            or len(exclusions) != invocation.exclusion_count
            or not cls._route_plan_reconciles(invocation, routes, exclusions)
            or bool(
                admissions and admissions[-1].decision is ModelAttemptDecisionKind.DENY
            )
            or candidate.admission_ordinal != expected_ordinal
            or candidate.prior_attempt_count != len(admitted)
            or bool(admitted and candidate.external_effect_observed)
        )
        if candidate.decision is ModelAttemptDecisionKind.ADMIT:
            invalid = invalid or (
                candidate.attempt_ordinal != expected_ordinal
                or candidate.deployment_id
                not in {route.deployment_id for route in routes}
                or len(admitted) >= invocation.max_attempts
                or sum(
                    item.deployment_id == candidate.deployment_id for item in admitted
                )
                >= invocation.max_same_deployment_attempts
            )
            admitted_recoveries = tuple(
                item
                for item in recoveries
                if item.outcome is ModelRecoveryOutcome.ADMITTED
            )
            if not admitted:
                invalid = invalid or (
                    candidate.reason is not ModelAttemptDecisionReason.FIRST_ATTEMPT
                )
            else:
                invalid = invalid or not admitted_recoveries
                if admitted_recoveries:
                    recovery = admitted_recoveries[-1]
                    source = cls._admission_for_attempt(
                        admitted,
                        recovery.source_attempt_id,
                    )
                    invalid = invalid or (
                        recovery.target_attempt_id != candidate.attempt_id
                        or source is None
                    )
                    if source is not None:
                        if recovery.kind is ModelRecoveryKind.SAME_DEPLOYMENT_RETRY:
                            invalid = invalid or (
                                candidate.deployment_id != source.deployment_id
                                or candidate.reason
                                is not ModelAttemptDecisionReason.SAFE_SAME_DEPLOYMENT_RETRY
                            )
                        elif recovery.kind is ModelRecoveryKind.ALTERNATE_ROUTE:
                            invalid = invalid or (
                                candidate.deployment_id == source.deployment_id
                                or candidate.reason
                                is not ModelAttemptDecisionReason.SAFE_ALTERNATE_ROUTE
                            )
        if invalid:
            cls._invalid(
                run_id=run_id,
                record_id=candidate.record_id,
                reason="attempt admission is incomplete, non-contiguous, or unauthorized",
                replay=replay,
            )

    @classmethod
    def _validate_state(
        cls,
        *,
        run_id: str,
        admitted: tuple[ModelAttemptAdmissionRecord, ...],
        states: tuple[ModelAttemptStateRecord, ...],
        failures: tuple[ModelAttemptFailedRecord, ...],
        candidate: ModelAttemptStateRecord,
        replay: bool,
    ) -> None:
        admission = cls._admission_for_attempt(admitted, candidate.attempt_id)
        attempt_states = tuple(
            item for item in states if item.attempt_id == candidate.attempt_id
        )
        last_state = attempt_states[-1] if attempt_states else None
        invalid = (
            admission is None
            or not cls._same_attempt(admission, candidate)
            or any(item.state is candidate.state for item in attempt_states)
            or any(item.attempt_id == candidate.attempt_id for item in failures)
        )
        if last_state is not None:
            invalid = invalid or (
                cls._STATE_RANK[candidate.state] < cls._STATE_RANK[last_state.state]
                or last_state.state
                in {
                    ModelAttemptLifecycleState.COMPLETED,
                    ModelAttemptLifecycleState.CANCELLED,
                    ModelAttemptLifecycleState.AMBIGUOUS,
                }
            )
        if invalid:
            cls._invalid(
                run_id=run_id,
                record_id=candidate.record_id,
                reason="attempt state is unbound, duplicate, or regressive",
                replay=replay,
            )

    @classmethod
    def _validate_usage(
        cls,
        *,
        run_id: str,
        admitted: tuple[ModelAttemptAdmissionRecord, ...],
        usages: tuple[ModelAttemptUsageRecord, ...],
        candidate: ModelAttemptUsageRecord,
        replay: bool,
    ) -> None:
        admission = cls._admission_for_attempt(admitted, candidate.attempt_id)
        if (
            admission is None
            or not cls._same_attempt(admission, candidate)
            or any(item.attempt_id == candidate.attempt_id for item in usages)
        ):
            cls._invalid(
                run_id=run_id,
                record_id=candidate.record_id,
                reason="attempt usage is unbound or already finalized",
                replay=replay,
            )

    @classmethod
    def _validate_attempt_failure(
        cls,
        *,
        run_id: str,
        admitted: tuple[ModelAttemptAdmissionRecord, ...],
        states: tuple[ModelAttemptStateRecord, ...],
        failures: tuple[ModelAttemptFailedRecord, ...],
        candidate: ModelAttemptFailedRecord,
        replay: bool,
    ) -> None:
        admission = cls._admission_for_attempt(admitted, candidate.attempt_id)
        attempt_states = tuple(
            item for item in states if item.attempt_id == candidate.attempt_id
        )
        if (
            admission is None
            or not cls._same_attempt(admission, candidate)
            or any(item.attempt_id == candidate.attempt_id for item in failures)
            or any(
                item.state is ModelAttemptLifecycleState.COMPLETED
                for item in attempt_states
            )
        ):
            cls._invalid(
                run_id=run_id,
                record_id=candidate.record_id,
                reason="attempt failure is unbound, duplicate, or follows completion",
                replay=replay,
            )

    @classmethod
    def _validate_recovery(
        cls,
        *,
        run_id: str,
        failures: tuple[ModelAttemptFailedRecord, ...],
        states: tuple[ModelAttemptStateRecord, ...],
        usages: tuple[ModelAttemptUsageRecord, ...],
        recoveries: tuple[ModelInvocationRecoveryRecord, ...],
        candidate: ModelInvocationRecoveryRecord,
        replay: bool,
    ) -> None:
        source_failure = next(
            (
                item
                for item in failures
                if item.attempt_id == candidate.source_attempt_id
            ),
            None,
        )
        source_ambiguous = any(
            item.attempt_id == candidate.source_attempt_id
            and item.state is ModelAttemptLifecycleState.AMBIGUOUS
            for item in states
        )
        invalid = candidate.recovery_ordinal != len(recoveries) + 1 or (
            source_failure is None and not source_ambiguous
        )
        if candidate.outcome is ModelRecoveryOutcome.ADMITTED:
            invalid = invalid or (
                source_failure is None
                or source_ambiguous
                or source_failure.failure_class
                not in {
                    ModelFailureClass.PRE_DISPATCH_TRANSIENT,
                    ModelFailureClass.PROVIDER_OVERLOADED,
                    ModelFailureClass.STREAM_INTERRUPTED_BEFORE_CONTENT,
                }
                or source_failure.visible_text_emitted
                or source_failure.tool_call_content_emitted
                or source_failure.external_effect_observed
                or not any(
                    item.attempt_id == candidate.source_attempt_id for item in usages
                )
            )
        if candidate.kind is ModelRecoveryKind.SAME_DEPLOYMENT_RETRY:
            invalid = invalid or (
                candidate.decision_reason
                is not ModelAttemptDecisionReason.SAFE_SAME_DEPLOYMENT_RETRY
            )
        elif candidate.kind is ModelRecoveryKind.ALTERNATE_ROUTE:
            invalid = invalid or (
                candidate.decision_reason
                is not ModelAttemptDecisionReason.SAFE_ALTERNATE_ROUTE
            )
        if invalid:
            cls._invalid(
                run_id=run_id,
                record_id=candidate.record_id,
                reason="recovery is non-contiguous or lacks a terminal source attempt",
                replay=replay,
            )

    @classmethod
    def _validate_completion(
        cls,
        *,
        run_id: str,
        admitted: tuple[ModelAttemptAdmissionRecord, ...],
        states: tuple[ModelAttemptStateRecord, ...],
        usages: tuple[ModelAttemptUsageRecord, ...],
        recoveries: tuple[ModelInvocationRecoveryRecord, ...],
        candidate: ModelInvocationCompletedRecord,
        replay: bool,
    ) -> None:
        completed = any(
            item.attempt_id == candidate.terminal_attempt_id
            and item.state is ModelAttemptLifecycleState.COMPLETED
            for item in states
        )
        reconciled_completed = any(
            item.source_attempt_id == candidate.terminal_attempt_id
            and item.outcome is ModelRecoveryOutcome.RECONCILED_COMPLETED
            for item in recoveries
        )
        usage_ids = {item.attempt_id for item in usages}
        attempt_ids = {item.attempt_id for item in admitted}
        invalid = (
            candidate.attempt_count != len(admitted)
            or candidate.terminal_attempt_id not in attempt_ids
            or usage_ids != attempt_ids
            or not (completed or reconciled_completed)
            or not cls._totals_match(candidate, usages)
        )
        if invalid:
            cls._invalid(
                run_id=run_id,
                record_id=candidate.record_id,
                reason="completion does not reconcile attempts, usage, and state",
                replay=replay,
            )

    @classmethod
    def _validate_invocation_failure(
        cls,
        *,
        run_id: str,
        routes: tuple[ModelRouteEligibleRecord, ...],
        admissions: tuple[ModelAttemptAdmissionRecord, ...],
        admitted: tuple[ModelAttemptAdmissionRecord, ...],
        states: tuple[ModelAttemptStateRecord, ...],
        usages: tuple[ModelAttemptUsageRecord, ...],
        failures: tuple[ModelAttemptFailedRecord, ...],
        recoveries: tuple[ModelInvocationRecoveryRecord, ...],
        candidate: ModelInvocationFailedRecord,
        replay: bool,
    ) -> None:
        terminal_id = candidate.terminal_attempt_id
        terminal_observed = terminal_id is None or (
            any(item.attempt_id == terminal_id for item in failures)
            or any(
                item.attempt_id == terminal_id
                and item.state
                in {
                    ModelAttemptLifecycleState.CANCELLED,
                    ModelAttemptLifecycleState.AMBIGUOUS,
                }
                for item in states
            )
        )
        zero_attempt_authority = not routes or bool(
            admissions and admissions[-1].decision is ModelAttemptDecisionKind.DENY
        )
        unresolved_admitted_recovery = bool(
            recoveries
            and recoveries[-1].outcome is ModelRecoveryOutcome.ADMITTED
            and recoveries[-1].target_attempt_id
            not in {item.attempt_id for item in admitted}
        )
        invalid = (
            candidate.attempt_count != len(admitted)
            or not terminal_observed
            or (not admitted and not zero_attempt_authority)
            or {item.attempt_id for item in usages}
            != {item.attempt_id for item in admitted}
            or unresolved_admitted_recovery
            or not cls._totals_match(candidate, usages)
        )
        if invalid:
            cls._invalid(
                run_id=run_id,
                record_id=candidate.record_id,
                reason="failure does not reconcile route, attempts, recovery, and usage",
                replay=replay,
            )

    @staticmethod
    def _admission_for_attempt(
        admitted: tuple[ModelAttemptAdmissionRecord, ...],
        attempt_id: str,
    ) -> ModelAttemptAdmissionRecord | None:
        return next((item for item in admitted if item.attempt_id == attempt_id), None)

    @staticmethod
    def _route_plan_reconciles(
        invocation: ModelInvocationPlannedRecord,
        routes: tuple[ModelRouteEligibleRecord, ...],
        exclusions: tuple[ModelRouteExcludedRecord, ...],
    ) -> bool:
        try:
            ModelRoutePlan(
                policy_revision=invocation.route_policy_revision,
                routes=tuple(
                    ModelRouteEntry.model_validate(
                        route.model_dump(
                            mode="python",
                            include=set(ModelRouteEntry.model_fields),
                        )
                    )
                    for route in routes
                ),
                exclusions=tuple(
                    ModelRouteExclusion(
                        deployment_id=exclusion.deployment_id,
                        reasons=exclusion.reasons,
                    )
                    for exclusion in exclusions
                ),
                fallback_policy=invocation.fallback_policy,
                budget=ModelInvocationBudget(
                    max_attempts=invocation.max_attempts,
                    max_same_deployment_attempts=(
                        invocation.max_same_deployment_attempts
                    ),
                    max_cost_microusd=invocation.max_cost_microusd,
                    max_input_tokens=invocation.max_input_tokens,
                    max_output_tokens=invocation.max_output_tokens,
                    deadline_at=invocation.deadline_at,
                ),
                route_digest=invocation.route_digest,
            )
        except ValidationError:
            return False
        return True

    @staticmethod
    def _same_attempt(
        admission: ModelAttemptAdmissionRecord,
        record: ModelAttemptStateRecord
        | ModelAttemptUsageRecord
        | ModelAttemptFailedRecord,
    ) -> bool:
        return (
            admission.attempt_id == record.attempt_id
            and admission.attempt_ordinal == record.attempt_ordinal
            and admission.deployment_id == record.deployment_id
        )

    @staticmethod
    def _totals_match(
        terminal: ModelInvocationCompletedRecord | ModelInvocationFailedRecord,
        usages: tuple[ModelAttemptUsageRecord, ...],
    ) -> bool:
        return (
            terminal.total_input_tokens == sum(item.input_tokens for item in usages)
            and terminal.total_output_tokens
            == sum(item.output_tokens for item in usages)
            and terminal.total_cost_microusd
            == sum(item.cost_microusd for item in usages)
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
            raise ModelInvocationCorruption(run_id=run_id, reason=reason)
        raise ModelInvocationConflict(run_id=run_id, record_id=record_id)

    @classmethod
    def _event_type(cls, record: ModelInvocationRecord) -> RuntimeApiEventType:
        return cls._EVENT_TYPE_BY_KIND[record.record_kind]

    @staticmethod
    def _payload(record: ModelInvocationRecord) -> dict[str, object]:
        return ModelInvocationJournalPayload(record=record).model_dump(mode="json")

    @staticmethod
    def _stable_event_id(record: ModelInvocationRecord) -> str:
        digest = hashlib.sha256(record.record_id.encode("utf-8")).hexdigest()
        return f"model_invocation:{digest}"

    @staticmethod
    def _corrupt(event: RuntimeEventEnvelope, reason: str) -> None:
        raise ModelInvocationCorruption(run_id=event.run_id, reason=reason)


__all__ = ["EventJournalModelInvocationStore"]
