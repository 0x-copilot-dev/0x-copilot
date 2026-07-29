"""Canonical run-event adapter for immutable control snapshots and decisions.

All supported runtime backends already implement the same stable-id,
tenant-scoped event journal. Reusing it here gives file, in-memory, and
Postgres logical parity without a new table, JSONL ledger, or checkpointer-only
truth.
"""

from __future__ import annotations

import hashlib

from pydantic import ValidationError

from agent_runtime.api.ports import EventStorePort
from agent_runtime.control_plane.contracts import (
    DECISION_COUNT_FIELDS,
    RunControlDecision,
    RunControlSnapshot,
)
from agent_runtime.control_plane.ports import (
    RunControlDecisionStorePort,
    RunControlDecisionConflict,
    RunControlDecisionWrite,
    RunControlJournalCorruption,
    RunControlScopeConflict,
    RunControlSnapshotConflict,
    RunControlSnapshotStorePort,
    RunControlSnapshotWrite,
    SequencedRunControlDecision,
)
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.persistence.ports import RuntimeEventIdempotencyConflict
from runtime_api.schemas import (
    QualityControlBoundPayload,
    QualityDecisionPayload,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)


class EventJournalRunControlStore(
    RunControlSnapshotStorePort,
    RunControlDecisionStorePort,
):
    """Fold immutable control records from the existing run event stream."""

    def __init__(self, events: EventStorePort) -> None:
        self._events = events

    async def get(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
    ) -> RunControlSnapshot | None:
        events = await self._events.list_events_after(
            org_id=org_id,
            run_id=run_id,
            after_sequence=0,
        )
        snapshots = tuple(
            self._snapshot_from_event(event)
            for event in events
            if event.event_type is RuntimeApiEventType.QUALITY_CONTROL_BOUND
        )
        if not snapshots:
            return None
        if len(snapshots) != 1:
            raise RunControlJournalCorruption(
                run_id=run_id,
                reason="expected exactly one quality.control_bound.v1 event",
            )
        snapshot = snapshots[0]
        if snapshot.run_id != run_id:
            raise RunControlJournalCorruption(
                run_id=run_id,
                reason="snapshot run identity does not match its journal",
            )
        if snapshot.subject_fingerprint != subject_fingerprint:
            raise RunControlScopeConflict(run_id=run_id)
        return snapshot

    async def get_or_create(
        self,
        write: RunControlSnapshotWrite,
    ) -> RunControlSnapshot:
        snapshot = write.snapshot
        existing = await self.get(
            org_id=write.org_id,
            run_id=snapshot.run_id,
            subject_fingerprint=snapshot.subject_fingerprint,
        )
        if existing is not None:
            return self._same_snapshot_or_conflict(existing, snapshot)

        draft = RuntimeEventDraft(
            org_id=write.org_id,
            event_id=self._stable_event_id("control", snapshot.run_id),
            created_at=snapshot.created_at,
            run_id=snapshot.run_id,
            conversation_id=snapshot.conversation_id,
            trace_id=write.trace_id,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.QUALITY_CONTROL_BOUND,
            activity_kind=RuntimeActivityKind.EVENT,
            visibility=RuntimeEventVisibility.INTERNAL,
            redaction_state=RuntimeEventRedactionState.REDACTED,
            payload=self._snapshot_payload(snapshot),
        )
        try:
            envelope = await self._events.append_event(draft)
        except RuntimeEventIdempotencyConflict:
            # Another writer won the stable event id. Compare the semantic
            # digest rather than record id/time so equivalent builders converge.
            durable = await self.get(
                org_id=write.org_id,
                run_id=snapshot.run_id,
                subject_fingerprint=snapshot.subject_fingerprint,
            )
            if durable is None:
                raise RunControlJournalCorruption(
                    run_id=snapshot.run_id,
                    reason="stable snapshot event conflicted but was not readable",
                ) from None
            return self._same_snapshot_or_conflict(durable, snapshot)

        durable = self._snapshot_from_event(envelope)
        return self._same_snapshot_or_conflict(durable, snapshot)

    async def append(
        self,
        write: RunControlDecisionWrite,
    ) -> SequencedRunControlDecision:
        decision = write.decision
        snapshot = await self.get(
            org_id=write.org_id,
            run_id=decision.run_id,
            subject_fingerprint=write.subject_fingerprint,
        )
        if snapshot is None:
            raise RunControlJournalCorruption(
                run_id=decision.run_id,
                reason="a decision cannot precede the control snapshot",
            )
        if decision.snapshot_id != snapshot.snapshot_id:
            raise RunControlSnapshotConflict(run_id=decision.run_id)

        draft = RuntimeEventDraft(
            org_id=write.org_id,
            event_id=self._stable_event_id(
                "decision",
                f"{decision.run_id}:{decision.decision_id}",
            ),
            created_at=decision.created_at,
            run_id=decision.run_id,
            conversation_id=snapshot.conversation_id,
            trace_id=write.trace_id,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.QUALITY_DECISION,
            activity_kind=RuntimeActivityKind.EVENT,
            visibility=RuntimeEventVisibility.INTERNAL,
            redaction_state=RuntimeEventRedactionState.REDACTED,
            payload=self._decision_payload(decision),
        )
        try:
            envelope = await self._events.append_event(draft)
        except RuntimeEventIdempotencyConflict:
            existing = await self._decision_by_id(
                org_id=write.org_id,
                run_id=decision.run_id,
                decision_id=decision.decision_id,
            )
            if existing is None:
                raise RunControlJournalCorruption(
                    run_id=decision.run_id,
                    reason="stable decision event conflicted but was not readable",
                ) from None
            if existing.decision.decision_digest != decision.decision_digest:
                raise RunControlDecisionConflict(
                    run_id=decision.run_id,
                    decision_id=decision.decision_id,
                ) from None
            return existing

        durable = self._decision_from_event(envelope)
        if durable.decision_digest != decision.decision_digest:
            raise RunControlJournalCorruption(
                run_id=decision.run_id,
                reason="appended decision does not match its canonical digest",
            )
        return SequencedRunControlDecision(
            sequence_no=envelope.sequence_no,
            decision=durable,
        )

    async def list_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        after_sequence: int = 0,
    ) -> tuple[SequencedRunControlDecision, ...]:
        snapshot = await self.get(
            org_id=org_id,
            run_id=run_id,
            subject_fingerprint=subject_fingerprint,
        )
        if snapshot is None:
            return ()
        events = await self._events.list_events_after(
            org_id=org_id,
            run_id=run_id,
            after_sequence=after_sequence,
        )
        decisions = tuple(
            SequencedRunControlDecision(
                sequence_no=event.sequence_no,
                decision=self._decision_from_event(event),
            )
            for event in events
            if event.event_type is RuntimeApiEventType.QUALITY_DECISION
        )
        if any(item.decision.snapshot_id != snapshot.snapshot_id for item in decisions):
            raise RunControlJournalCorruption(
                run_id=run_id,
                reason="decision refers to a different run snapshot",
            )
        return decisions

    async def _decision_by_id(
        self,
        *,
        org_id: str,
        run_id: str,
        decision_id: str,
    ) -> SequencedRunControlDecision | None:
        events = await self._events.list_events_after(
            org_id=org_id,
            run_id=run_id,
            after_sequence=0,
        )
        matches = tuple(
            SequencedRunControlDecision(
                sequence_no=event.sequence_no,
                decision=self._decision_from_event(event),
            )
            for event in events
            if event.event_type is RuntimeApiEventType.QUALITY_DECISION
            and event.event_id
            == self._stable_event_id("decision", f"{run_id}:{decision_id}")
        )
        if len(matches) > 1:
            raise RunControlJournalCorruption(
                run_id=run_id,
                reason="duplicate stable decision event identity",
            )
        return matches[0] if matches else None

    @classmethod
    def _snapshot_from_event(
        cls,
        event: RuntimeEventEnvelope,
    ) -> RunControlSnapshot:
        try:
            payload = QualityControlBoundPayload.model_validate(event.payload)
            snapshot = RunControlSnapshot.model_validate(
                {
                    "schema_version": payload.schema_version,
                    "snapshot_id": payload.snapshot_id,
                    "snapshot_digest": payload.snapshot_digest,
                    "run_id": event.run_id,
                    "conversation_id": event.conversation_id,
                    "subject_fingerprint": payload.subject_fingerprint,
                    "deployment_profile": payload.deployment_profile,
                    "harness_variant_ref": payload.harness_variant_ref,
                    "task_policy_selection_ref": (payload.task_policy_selection_ref),
                    "policy_revisions": {
                        "prompt": payload.prompt_policy_revision,
                        "capability": payload.capability_policy_revision,
                        "context": payload.context_policy_revision,
                        "tool_controller": (payload.tool_controller_policy_revision),
                        "concurrency": payload.concurrency_policy_revision,
                        "dataflow": payload.dataflow_policy_revision,
                        "mcp_freshness": (payload.mcp_freshness_policy_revision),
                        "delegation": payload.delegation_policy_revision,
                        "model_route": payload.model_route_policy_revision,
                        "workspace_edit": (payload.workspace_edit_policy_revision),
                        "answer_verification": (
                            payload.answer_verification_policy_revision
                        ),
                    },
                    "feature_modes": {
                        "f1": payload.feature_mode_f1,
                        "f2": payload.feature_mode_f2,
                        "f3": payload.feature_mode_f3,
                        "f4": payload.feature_mode_f4,
                        "f5": payload.feature_mode_f5,
                        "f6": payload.feature_mode_f6,
                        "f7": payload.feature_mode_f7,
                        "f8": payload.feature_mode_f8,
                        "f9": payload.feature_mode_f9,
                        "f10": payload.feature_mode_f10,
                        "f11": payload.feature_mode_f11,
                        "f12": payload.feature_mode_f12,
                    },
                    "model_reliability_controls": {
                        "same_deployment_retry": (
                            payload.model_same_deployment_retry_mode
                        ),
                        "alternate_route": payload.model_alternate_route_mode,
                        "equivalent_route": payload.model_equivalent_route_mode,
                        "circuit_influence": (payload.model_circuit_influence_mode),
                        "qualification_authority_ref": (
                            payload.model_qualification_authority_ref
                        ),
                        "qualification_authority_revision": (
                            payload.model_qualification_authority_revision
                        ),
                    },
                    "budget_envelope_ref": payload.budget_envelope_ref,
                    "assignment_revision": payload.assignment_revision,
                    "created_at": payload.created_at,
                }
            )
        except ValidationError as exc:
            raise RunControlJournalCorruption(
                run_id=event.run_id,
                reason="malformed quality.control_bound.v1 payload",
            ) from exc
        expected = cls._snapshot_payload(snapshot)
        if event.payload != expected:
            raise RunControlJournalCorruption(
                run_id=event.run_id,
                reason="snapshot event mirrors do not match the canonical record",
            )
        if event.event_id != cls._stable_event_id("control", event.run_id):
            raise RunControlJournalCorruption(
                run_id=event.run_id,
                reason="snapshot event does not use its stable identity",
            )
        return snapshot

    @classmethod
    def _decision_from_event(
        cls,
        event: RuntimeEventEnvelope,
    ) -> RunControlDecision:
        try:
            payload = QualityDecisionPayload.model_validate(event.payload)
            decision = RunControlDecision.model_validate(
                {
                    "schema_version": payload.schema_version,
                    "decision_id": payload.decision_id,
                    "decision_digest": payload.decision_digest,
                    "run_id": event.run_id,
                    "snapshot_id": payload.snapshot_id,
                    "phase": payload.phase,
                    "feature": payload.feature,
                    "policy_revision": payload.policy_revision,
                    "input_digest": payload.input_digest,
                    "outcome_code": payload.outcome_code,
                    "record_ref": payload.record_ref,
                    "parent_decision_refs": payload.parent_decision_refs,
                    "candidate_count": payload.candidate_count,
                    "selection_rank": payload.selection_rank,
                    "result_tokens": payload.result_tokens,
                    "model_turns": payload.model_turns,
                    "created_at": payload.created_at,
                }
            )
        except ValidationError as exc:
            raise RunControlJournalCorruption(
                run_id=event.run_id,
                reason="malformed quality.decision.v1 payload",
            ) from exc
        if cls._observed_counts_spelled_out(event.payload) != cls._decision_payload(
            decision
        ):
            raise RunControlJournalCorruption(
                run_id=event.run_id,
                reason="decision event mirrors do not match the canonical record",
            )
        if event.event_id != cls._stable_event_id(
            "decision",
            f"{decision.run_id}:{decision.decision_id}",
        ):
            raise RunControlJournalCorruption(
                run_id=event.run_id,
                reason="decision event does not use its stable identity",
            )
        return decision

    @staticmethod
    def _snapshot_payload(snapshot: RunControlSnapshot) -> dict[str, object]:
        revisions = snapshot.policy_revisions
        modes = snapshot.feature_modes
        reliability = snapshot.model_reliability_controls
        payload = QualityControlBoundPayload(
            schema_version=snapshot.schema_version,
            snapshot_id=snapshot.snapshot_id,
            snapshot_digest=snapshot.snapshot_digest,
            subject_fingerprint=snapshot.subject_fingerprint,
            deployment_profile=snapshot.deployment_profile,
            harness_variant_ref=snapshot.harness_variant_ref,
            task_policy_selection_ref=snapshot.task_policy_selection_ref,
            prompt_policy_revision=revisions.prompt,
            capability_policy_revision=revisions.capability,
            context_policy_revision=revisions.context,
            tool_controller_policy_revision=revisions.tool_controller,
            concurrency_policy_revision=revisions.concurrency,
            dataflow_policy_revision=revisions.dataflow,
            mcp_freshness_policy_revision=revisions.mcp_freshness,
            delegation_policy_revision=revisions.delegation,
            model_route_policy_revision=revisions.model_route,
            workspace_edit_policy_revision=revisions.workspace_edit,
            answer_verification_policy_revision=revisions.answer_verification,
            feature_mode_f1=modes.f1.value,
            feature_mode_f2=modes.f2.value,
            feature_mode_f3=modes.f3.value,
            feature_mode_f4=modes.f4.value,
            feature_mode_f5=modes.f5.value,
            feature_mode_f6=modes.f6.value,
            feature_mode_f7=modes.f7.value,
            feature_mode_f8=modes.f8.value,
            feature_mode_f9=modes.f9.value,
            feature_mode_f10=modes.f10.value,
            feature_mode_f11=modes.f11.value,
            feature_mode_f12=modes.f12.value,
            model_same_deployment_retry_mode=(reliability.same_deployment_retry.value),
            model_alternate_route_mode=reliability.alternate_route.value,
            model_equivalent_route_mode=reliability.equivalent_route.value,
            model_circuit_influence_mode=reliability.circuit_influence.value,
            model_qualification_authority_ref=(reliability.qualification_authority_ref),
            model_qualification_authority_revision=(
                reliability.qualification_authority_revision
            ),
            budget_envelope_ref=snapshot.budget_envelope_ref,
            assignment_revision=snapshot.assignment_revision,
            created_at=snapshot.created_at,
        )
        excluded = (
            {
                "model_same_deployment_retry_mode",
                "model_alternate_route_mode",
                "model_equivalent_route_mode",
                "model_circuit_influence_mode",
                "model_qualification_authority_ref",
                "model_qualification_authority_revision",
            }
            if snapshot.schema_version == 1
            else set()
        )
        return payload.model_dump(mode="json", exclude=excluded)

    @staticmethod
    def _observed_counts_spelled_out(payload: dict[str, object]) -> dict[str, object]:
        """Say a stored row's *unobserved* counts the way a fresh dump says them.

        A ``quality.decision.v1`` row written before the numeric extension
        existed carries none of the four keys; one written now by a producer
        that measured nothing carries them as ``null``. Both make the identical
        statement — not observed — so the mirror check must not read the
        difference as corruption and refuse to replay every older row in the
        journal.

        This normalizes only *absence*. A key that is present keeps its value
        exactly, so a stored count that disagrees with the canonical record
        still fails the check it exists to fail.
        """

        missing = {name: None for name in DECISION_COUNT_FIELDS if name not in payload}
        return {**payload, **missing} if missing else payload

    @staticmethod
    def _decision_payload(decision: RunControlDecision) -> dict[str, object]:
        """Mirror one decision into the closed event payload it is carried by."""

        return QualityDecisionPayload(
            schema_version=decision.schema_version,
            decision_id=decision.decision_id,
            decision_digest=decision.decision_digest,
            snapshot_id=decision.snapshot_id,
            phase=decision.phase,
            feature=decision.feature.value,
            policy_revision=decision.policy_revision,
            input_digest=decision.input_digest,
            outcome_code=decision.outcome_code,
            record_ref=decision.record_ref,
            parent_decision_refs=decision.parent_decision_refs,
            candidate_count=decision.candidate_count,
            selection_rank=decision.selection_rank,
            result_tokens=decision.result_tokens,
            model_turns=decision.model_turns,
            created_at=decision.created_at,
        ).model_dump(mode="json")

    @staticmethod
    def _same_snapshot_or_conflict(
        existing: RunControlSnapshot,
        candidate: RunControlSnapshot,
    ) -> RunControlSnapshot:
        if existing.snapshot_digest != candidate.snapshot_digest:
            raise RunControlSnapshotConflict(run_id=candidate.run_id)
        return existing

    @staticmethod
    def _stable_event_id(kind: str, value: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return f"quality_{kind}:{digest}"


__all__ = ["EventJournalRunControlStore"]
