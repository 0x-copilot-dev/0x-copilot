"""Production F4 runtime over the canonical run event journal.

The adapter is intentionally the only mutable bridge between the pure task
policy reducer and runtime persistence.  Its lock covers decision, append, and
local fold so concurrent graph calls observe the same order as the canonical
per-run event stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import hmac
import os
from typing import Final

from agent_runtime.capabilities.task_policy import (
    ErrorFingerprint,
    EvidenceFingerprint,
    ModelTurnRecord,
    RequestFingerprint,
    ResultFingerprint,
    RunToolPlan,
    RunToolPlanFactory,
    TaskFamily,
    TaskPolicyBudgetRecord,
    TaskPolicyBundle,
    TaskPolicyProfile,
    TaskPolicyRequest,
    TaskPolicyResolver,
    TaskPolicySelection,
    ToolControllerState,
    ToolOperationOutcome,
    ToolPlanCreator,
    ToolPlanProgressRecord,
    ToolUseController,
    ToolUseDisposition,
    ToolUseFeedback,
    ToolUseIntent,
)
from agent_runtime.capabilities.task_policy_journal import (
    SequencedTaskPolicyJournalRecord,
    TaskPolicyAdmissionDisposition,
    TaskPolicyAdmissionRecordedRecord,
    TaskPolicyBudgetRecordedRecord,
    TaskPolicyFeedbackDisposition,
    TaskPolicyFeedbackRecordedRecord,
    TaskPolicyIntentRecordedRecord,
    TaskPolicyJournalRecord,
    TaskPolicyModelTurnRecordedRecord,
    TaskPolicyOutcomeRecordedRecord,
    TaskPolicyOutcomeStatus,
    TaskPolicyPlanBoundRecord,
    TaskPolicyProfileSelectedRecord,
    TaskPolicyProgressRecordedRecord,
    TaskPolicyReasonCode,
    validate_task_policy_journal_record,
)
from agent_runtime.control_plane.context import (
    TaskPolicyCapabilityProgress,
    TaskPolicyProgressProjection,
    TaskPolicyRuntimeBinding,
)
from agent_runtime.control_plane.contracts import BudgetEnvelope
from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    canonical_json_sha256,
)
from runtime_worker.run_control import (
    TaskPolicyRuntimeFactoryPort,
    VerifiedTaskPolicySignals,
)

_SECRET_ENV: Final = "ENTERPRISE_AUTH_SECRET"
_DEVELOPMENT_SECRET: Final = b"dev-task-policy-fingerprint-root-v1"
_KEY_PURPOSE: Final = b"0x-copilot/task-policy/fingerprints/v1"


class TaskPolicyRuntimeError(RuntimeError):
    """An enabled task-policy runtime cannot be prepared or persisted safely."""


class TaskPolicyFingerprinter:
    """Domain-separated request/result/evidence/error HMACs for one deployment."""

    def __init__(self, *, root_key: bytes) -> None:
        if len(root_key) < 16:
            raise ValueError("task-policy root key is too short")
        key = hmac.new(root_key, _KEY_PURPOSE, hashlib.sha256).digest()
        self._scope_key = hmac.new(key, b"scope", hashlib.sha256).digest()
        self._requests = RequestFingerprint(key=key)
        self._results = ResultFingerprint(
            key=hmac.new(key, b"result", hashlib.sha256).digest()
        )
        self._evidence = EvidenceFingerprint(
            key=hmac.new(key, b"evidence", hashlib.sha256).digest()
        )
        self._errors = ErrorFingerprint(
            key=hmac.new(key, b"error", hashlib.sha256).digest()
        )

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "TaskPolicyFingerprinter":
        env = os.environ if environment is None else environment
        raw = (env.get(_SECRET_ENV) or "").strip()
        runtime_environment = (env.get("RUNTIME_ENVIRONMENT") or "development").strip()
        if not raw:
            if runtime_environment.lower() == "production":
                raise TaskPolicyRuntimeError(
                    "task-policy fingerprint root is unavailable in production"
                )
            return cls(root_key=_DEVELOPMENT_SECRET)
        try:
            root = bytes.fromhex(raw)
        except ValueError:
            root = raw.encode("utf-8")
        return cls(root_key=root)

    def for_request(
        self,
        *,
        capability_id: str,
        arguments: Mapping[str, object],
    ) -> str:
        return self._requests.for_request(
            capability_id=capability_id,
            arguments=arguments,
        )

    def for_result(
        self,
        *,
        capability_id: str,
        result_metadata: Mapping[str, object],
    ) -> str:
        return self._results.for_result(
            capability_id=capability_id,
            result_metadata=result_metadata,
        )

    def for_evidence(
        self,
        *,
        source_kind: str,
        source_identity: Mapping[str, object],
    ) -> str:
        return self._evidence.for_evidence(
            source_kind=source_kind,
            source_identity=source_identity,
        )

    def for_error(
        self,
        *,
        capability_id: str,
        request_fingerprint: str,
        error_class: str,
        retryable: bool,
        retry_hint: str | None = None,
    ) -> str:
        return self._errors.for_error(
            capability_id=capability_id,
            request_fingerprint=request_fingerprint,
            error_class=error_class,
            retryable=retryable,
            retry_hint=retry_hint,
        )

    def protected_scope(self, value: str) -> str:
        return hmac.new(
            self._scope_key,
            canonical_json_bytes({"kind": "execution_scope", "value": value}),
            hashlib.sha256,
        ).hexdigest()


class DeploymentTaskPolicyBundles:
    """Code-reviewed immutable policy bundle template keyed by release revision."""

    @staticmethod
    def for_revision(revision: str) -> TaskPolicyBundle:
        profiles = (
            TaskPolicyProfile(
                profile_id="connected.lookup",
                revision=revision,
                task_family=TaskFamily.CONNECTED_RECORD_LOOKUP,
                model_turn_limit=6,
                total_tool_call_limit=4,
                tool_call_limits={"*": 3},
                wall_time_limit_seconds=300,
                checkpoint_interval=1,
                enforce_exact_duplicates=True,
                enforce_unchanged_errors=True,
                objective_evidence_threshold=1,
            ),
            TaskPolicyProfile(
                profile_id="public.research",
                revision=revision,
                task_family=TaskFamily.PUBLIC_RESEARCH,
                model_turn_limit=12,
                total_tool_call_limit=12,
                tool_call_limits={"*": 8},
                wall_time_limit_seconds=900,
                checkpoint_interval=2,
                enforce_exact_duplicates=True,
                enforce_unchanged_errors=True,
                objective_evidence_threshold=2,
            ),
            TaskPolicyProfile(
                profile_id="effect.proposal",
                revision=revision,
                task_family=TaskFamily.EFFECT_PROPOSAL,
                model_turn_limit=8,
                total_tool_call_limit=6,
                tool_call_limits={"*": 3},
                wall_time_limit_seconds=600,
                checkpoint_interval=1,
                enforce_exact_duplicates=True,
                enforce_unchanged_errors=True,
            ),
            TaskPolicyProfile(
                profile_id="delegated.analysis",
                revision=revision,
                task_family=TaskFamily.DELEGATED_ANALYSIS,
                model_turn_limit=12,
                total_tool_call_limit=10,
                tool_call_limits={"*": 6},
                wall_time_limit_seconds=900,
                checkpoint_interval=2,
                enforce_exact_duplicates=True,
                enforce_unchanged_errors=True,
            ),
        )
        return TaskPolicyBundle.with_conservative_unknown(
            bundle_id="desktop-and-consumer-default",
            revision=revision,
            profiles=profiles,
        )


def _stable_id(prefix: str, payload: object) -> str:
    return f"{prefix}-{canonical_json_sha256(payload)[:32]}"


def _plan_digest(plan: RunToolPlan) -> str:
    return canonical_json_sha256(plan.model_dump(mode="json"))


def _canonical_datetime(value: datetime | None) -> str | None:
    """Return the stable JSON representation used by content-derived IDs."""

    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise TaskPolicyRuntimeError("task-policy deadline must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _reason(value: str) -> TaskPolicyReasonCode:
    direct = {item.value: item for item in TaskPolicyReasonCode}
    aliases = {
        "profile_deadline_exhausted": TaskPolicyReasonCode.DEADLINE_EXHAUSTED,
        "profile_model_turn_limit": TaskPolicyReasonCode.BUDGET_EXHAUSTED,
        "profile_total_tool_call_limit": TaskPolicyReasonCode.BUDGET_EXHAUSTED,
        "profile_cost_limit": TaskPolicyReasonCode.BUDGET_EXHAUSTED,
        "model_turn_recorded": TaskPolicyReasonCode.WITHIN_BUDGET,
        "model_turn_replayed": TaskPolicyReasonCode.OPERATION_REPLAYED,
        "operation_replayed": TaskPolicyReasonCode.OPERATION_REPLAYED,
    }
    return direct.get(value, aliases.get(value, TaskPolicyReasonCode.UNKNOWN))


def _feedback_disposition(
    value: ToolUseDisposition,
) -> TaskPolicyFeedbackDisposition:
    return TaskPolicyFeedbackDisposition(value.value)


class DurableTaskPolicyController:
    """Serialize domain decisions with their canonical durable facts."""

    def __init__(
        self,
        *,
        signals: VerifiedTaskPolicySignals,
        mode: FeatureMode,
        selection_ref: str,
        selection: TaskPolicySelection,
        plan: RunToolPlan | None,
        controller: ToolUseController,
        fingerprinter: TaskPolicyFingerprinter,
        append_record: Callable[[object], Awaitable[object]],
        journal_records: Sequence[TaskPolicyJournalRecord],
    ) -> None:
        self._signals = signals
        self._mode = mode
        self._selection_ref = selection_ref
        self._selection = selection
        self._plan = plan
        self._controller = controller
        self._fingerprinter = fingerprinter
        self._append = append_record
        self._lock = asyncio.Lock()
        self._records = list(journal_records)
        self._intent_by_operation = {
            record.operation_id: record
            for record in self._records
            if isinstance(record, TaskPolicyIntentRecordedRecord)
        }
        self._admission_by_operation = {
            record.operation_id: record
            for record in self._records
            if isinstance(record, TaskPolicyAdmissionRecordedRecord)
        }

    @property
    def state(self) -> ToolControllerState:
        return self._controller.state

    async def before_operation(self, intent: ToolUseIntent) -> ToolUseFeedback:
        async with self._lock:
            existing = self._admission_by_operation.get(intent.operation_id)
            if existing is not None:
                return self._feedback_from_admission(existing)
            intent_record = self._intent_record(intent)
            await self._persist(intent_record)
            feedback = self._controller.before_operation(intent)
            shadow_admitted = (
                self._mode is FeatureMode.SHADOW
                and feedback.disposition is not ToolUseDisposition.CONTINUE
            )
            if shadow_admitted:
                self._controller.observe_dispatched(intent)
            disposition = (
                TaskPolicyAdmissionDisposition.SHADOW_ADMITTED
                if shadow_admitted
                else (
                    TaskPolicyAdmissionDisposition.ADMITTED
                    if feedback.disposition is ToolUseDisposition.CONTINUE
                    else TaskPolicyAdmissionDisposition.BLOCKED
                )
            )
            admission = TaskPolicyAdmissionRecordedRecord.create(
                record_id=_stable_id(
                    "f4-admission",
                    {
                        "intent": intent_record.record_digest,
                        "disposition": disposition.value,
                        "reason": feedback.reason_code,
                    },
                ),
                run_id=self._signals.run_id,
                snapshot_id=self._signals.snapshot_id,
                tool_call_id=intent.operation_id,
                operation_id=intent.operation_id,
                intent_record_id=intent_record.record_id,
                intent_digest=intent_record.record_digest,
                disposition=disposition,
                reason_codes=(_reason(feedback.reason_code),),
                duplicate_of_operation_id=feedback.duplicate_of_operation_id,
                model_turn_ordinal=max(self._controller.state.model_turns, 1),
                tool_call_ordinal=self._controller.state.tool_calls,
            )
            await self._persist(admission)
            await self._persist_feedback(
                intent=intent,
                admission=admission,
                feedback=feedback,
            )
            self._intent_by_operation[intent.operation_id] = intent_record
            self._admission_by_operation[intent.operation_id] = admission
            return feedback

    async def after_operation(self, outcome: ToolOperationOutcome) -> ToolUseFeedback:
        async with self._lock:
            intent_record = self._intent_by_operation.get(outcome.operation_id)
            admission = self._admission_by_operation.get(outcome.operation_id)
            if intent_record is None or admission is None:
                raise TaskPolicyRuntimeError(
                    "task-policy outcome has no durable admission"
                )
            feedback = self._controller.after_operation(outcome)
            status = (
                TaskPolicyOutcomeStatus.SUCCEEDED
                if outcome.succeeded
                else TaskPolicyOutcomeStatus.FAILED
            )
            result_fingerprint = outcome.result_fingerprint
            if outcome.succeeded and result_fingerprint is None:
                raise TaskPolicyRuntimeError(
                    "successful task-policy outcome requires a keyed result fingerprint"
                )
            error_fingerprint = outcome.error_fingerprint
            if not outcome.succeeded and error_fingerprint is None:
                error_fingerprint = self._fingerprinter.for_error(
                    capability_id=outcome.capability_id,
                    request_fingerprint=intent_record.request_fingerprint,
                    error_class=outcome.error_class or "unknown",
                    retryable=outcome.retryable,
                )
            outcome_record = TaskPolicyOutcomeRecordedRecord.create(
                record_id=_stable_id("f4-outcome", outcome.outcome_digest),
                run_id=self._signals.run_id,
                snapshot_id=self._signals.snapshot_id,
                tool_call_id=outcome.operation_id,
                operation_id=outcome.operation_id,
                intent_record_id=intent_record.record_id,
                intent_digest=intent_record.record_digest,
                request_fingerprint=intent_record.request_fingerprint,
                status=status,
                result_fingerprint=result_fingerprint,
                error_fingerprint=error_fingerprint,
                failure_class=(
                    None
                    if outcome.succeeded
                    else _normalize_code(outcome.error_class or "unknown")
                ),
                retryable=outcome.retryable,
                new_evidence_count=feedback.new_evidence_count,
                observed_source_count=len(outcome.source_fingerprints),
                source_fingerprints=outcome.source_fingerprints,
                evidence_fingerprint=outcome.evidence_fingerprint,
                cost_microusd=outcome.cost_microusd,
                latency_ms=0,
            )
            await self._persist(outcome_record)
            await self._persist_feedback(
                intent=ToolUseIntent(
                    operation_id=outcome.operation_id,
                    capability_id=outcome.capability_id,
                    canonical_request_fingerprint=intent_record.request_fingerprint,
                ),
                admission=admission,
                feedback=feedback,
                outcome_record=outcome_record,
            )
            await self._persist_progress()
            return feedback

    async def before_model_turn(
        self,
        *,
        model_turn: int,
        execution_scope: str,
    ) -> ToolUseFeedback:
        if model_turn < 1:
            raise ValueError("model_turn must be positive")
        turn_id = _stable_id(
            "f4-turn",
            {
                "run_id": self._signals.run_id,
                "model_turn": model_turn,
                "scope": self._fingerprinter.protected_scope(execution_scope),
            },
        )
        async with self._lock:
            existing = next(
                (
                    record
                    for record in self._records
                    if isinstance(record, TaskPolicyModelTurnRecordedRecord)
                    and record.turn_id == turn_id
                ),
                None,
            )
            if existing is not None:
                return ToolUseFeedback(
                    disposition=(
                        ToolUseDisposition.CONTINUE
                        if existing.disposition
                        is not TaskPolicyAdmissionDisposition.BLOCKED
                        else ToolUseDisposition.STOP
                    ),
                    reason_code=existing.reason_codes[0].value,
                )
            domain = ModelTurnRecord(turn_id=turn_id)
            feedback = self._controller.record_model_turn(domain)
            shadow_admitted = (
                self._mode is FeatureMode.SHADOW
                and feedback.disposition is not ToolUseDisposition.CONTINUE
            )
            if shadow_admitted:
                self._controller.observe_model_turn(domain)
            disposition = (
                TaskPolicyAdmissionDisposition.SHADOW_ADMITTED
                if shadow_admitted
                else (
                    TaskPolicyAdmissionDisposition.ADMITTED
                    if feedback.disposition is ToolUseDisposition.CONTINUE
                    else TaskPolicyAdmissionDisposition.BLOCKED
                )
            )
            record = TaskPolicyModelTurnRecordedRecord.create(
                record_id=_stable_id(
                    "f4-model-turn",
                    {"turn_id": turn_id, "disposition": disposition.value},
                ),
                run_id=self._signals.run_id,
                snapshot_id=self._signals.snapshot_id,
                turn_id=turn_id,
                model_turn_ordinal=model_turn,
                execution_scope_fingerprint=self._fingerprinter.protected_scope(
                    execution_scope
                ),
                disposition=disposition,
                reason_codes=(_reason(feedback.reason_code),),
                cost_microusd=0,
            )
            await self._persist(record)
            return feedback

    async def observe_upstream_policy_block(
        self,
        intent: ToolUseIntent,
    ) -> ToolUseFeedback:
        async with self._lock:
            existing = self._admission_by_operation.get(intent.operation_id)
            if existing is not None:
                return self._feedback_from_admission(existing)
            intent_record = self._intent_record(intent)
            await self._persist(intent_record)
            feedback = ToolUseFeedback(
                disposition=ToolUseDisposition.BLOCKED,
                reason_code="authorization_blocked",
            )
            admission = TaskPolicyAdmissionRecordedRecord.create(
                record_id=_stable_id(
                    "f4-admission",
                    {"intent": intent_record.record_digest, "upstream": "blocked"},
                ),
                run_id=self._signals.run_id,
                snapshot_id=self._signals.snapshot_id,
                tool_call_id=intent.operation_id,
                operation_id=intent.operation_id,
                intent_record_id=intent_record.record_id,
                intent_digest=intent_record.record_digest,
                disposition=TaskPolicyAdmissionDisposition.BLOCKED,
                reason_codes=(TaskPolicyReasonCode.AUTHORIZATION_BLOCKED,),
                model_turn_ordinal=max(self._controller.state.model_turns, 1),
                tool_call_ordinal=max(self._controller.state.tool_calls, 1),
            )
            await self._persist(admission)
            await self._persist_feedback(
                intent=intent,
                admission=admission,
                feedback=feedback,
            )
            self._intent_by_operation[intent.operation_id] = intent_record
            self._admission_by_operation[intent.operation_id] = admission
            return feedback

    def _intent_record(self, intent: ToolUseIntent) -> TaskPolicyIntentRecordedRecord:
        plan_digest = _plan_digest(self._plan) if self._plan is not None else None
        return TaskPolicyIntentRecordedRecord.create(
            record_id=_stable_id("f4-intent", intent.intent_digest),
            run_id=self._signals.run_id,
            snapshot_id=self._signals.snapshot_id,
            selection_ref=self._selection_ref,
            selection_digest=self._selection.selection_digest,
            tool_call_id=intent.operation_id,
            operation_id=intent.operation_id,
            capability_id=intent.capability_id,
            request_fingerprint=intent.canonical_request_fingerprint,
            plan_id=self._plan.plan_id if self._plan is not None else None,
            plan_digest=plan_digest,
            plan_step_id=intent.plan_step_id,
            expected_evidence_kind=(
                _normalize_code(intent.expected_evidence_kind)
                if intent.expected_evidence_kind
                else None
            ),
            semantic_fingerprint=intent.semantic_fingerprint,
            objective_fingerprint=intent.objective_fingerprint,
        )

    async def _persist_feedback(
        self,
        *,
        intent: ToolUseIntent,
        admission: TaskPolicyAdmissionRecordedRecord,
        feedback: ToolUseFeedback,
        outcome_record: TaskPolicyOutcomeRecordedRecord | None = None,
    ) -> None:
        record = TaskPolicyFeedbackRecordedRecord.create(
            record_id=_stable_id(
                "f4-feedback",
                {
                    "admission": admission.record_digest,
                    "outcome": (
                        outcome_record.record_digest if outcome_record else None
                    ),
                    "feedback": feedback.feedback_digest,
                },
            ),
            run_id=self._signals.run_id,
            snapshot_id=self._signals.snapshot_id,
            tool_call_id=intent.operation_id,
            operation_id=intent.operation_id,
            admission_record_id=admission.record_id,
            outcome_record_id=(
                outcome_record.record_id if outcome_record is not None else None
            ),
            disposition=_feedback_disposition(feedback.disposition),
            reason_codes=(_reason(feedback.reason_code),),
            duplicate_of_operation_id=feedback.duplicate_of_operation_id,
            new_evidence_count=feedback.new_evidence_count,
            total_evidence_count=self._controller.state.evidence_count,
        )
        await self._persist(record)

    async def _persist_progress(self) -> None:
        if self._plan is None:
            return
        state = self._controller.state
        completed = min(state.evidence_count, len(self._plan.steps))
        active_step = (
            self._plan.steps[completed].step_id
            if completed < len(self._plan.steps)
            else None
        )
        record = TaskPolicyProgressRecordedRecord.create(
            record_id=_stable_id(
                "f4-progress",
                {
                    "plan": _plan_digest(self._plan),
                    "calls": state.tool_calls,
                    "evidence": state.evidence_count,
                },
            ),
            run_id=self._signals.run_id,
            snapshot_id=self._signals.snapshot_id,
            plan_id=self._plan.plan_id,
            plan_digest=_plan_digest(self._plan),
            plan_status=(
                "completed" if completed == len(self._plan.steps) else "active"
            ),
            step_count=len(self._plan.steps),
            completed_step_count=completed,
            blocked_step_count=0,
            active_step_id=active_step,
            evidence_count=state.evidence_count,
            checkpoint_ordinal=state.tool_calls,
            waiting_for_approval=False,
        )
        await self._persist(record)

    async def _persist(self, record: TaskPolicyJournalRecord) -> None:
        durable = await self._append(record)
        parsed = _unwrap_record(durable)
        if parsed.record_digest != record.record_digest:
            raise TaskPolicyRuntimeError(
                "canonical F4 append returned a different record"
            )
        if not any(item.record_id == parsed.record_id for item in self._records):
            self._records.append(parsed)

    @staticmethod
    def _feedback_from_admission(
        record: TaskPolicyAdmissionRecordedRecord,
    ) -> ToolUseFeedback:
        disposition = (
            ToolUseDisposition.CONTINUE
            if record.disposition is not TaskPolicyAdmissionDisposition.BLOCKED
            else ToolUseDisposition.STOP
        )
        return ToolUseFeedback(
            disposition=disposition,
            reason_code=record.reason_codes[0].value,
            duplicate_of_operation_id=record.duplicate_of_operation_id,
        )


class DefaultTaskPolicyRuntimeFactory(TaskPolicyRuntimeFactoryPort):
    """Select, initialize, replay, and bind one enabled F4 runtime."""

    def __init__(
        self,
        *,
        fingerprinter: TaskPolicyFingerprinter,
        bundle_provider: Callable[[str], TaskPolicyBundle] = (
            DeploymentTaskPolicyBundles.for_revision
        ),
    ) -> None:
        self._fingerprinter = fingerprinter
        self._bundle_provider = bundle_provider

    async def prepare(
        self,
        *,
        signals: VerifiedTaskPolicySignals,
        mode: FeatureMode,
        budget_envelope: BudgetEnvelope | None,
        load_records: Callable[[], Awaitable[Sequence[object]]],
        append_record: Callable[[object], Awaitable[object]],
    ) -> TaskPolicyRuntimeBinding:
        if mode is FeatureMode.OFF:
            raise TaskPolicyRuntimeError("off F4 mode must not prepare a runtime")
        bundle = self._bundle_provider(signals.task_policy_revision)
        bundle.verify()
        resolver = TaskPolicyResolver(bundle=bundle)
        # Persisted RunRecord currently exposes no trusted task-family fact.
        # Deliberately resolve the conservative family here: request options,
        # trace metadata, and model text are not authority. Future specialized
        # selection must arrive as a separately verified server-owned signal.
        policy_request = TaskPolicyRequest(
            run_id=signals.run_id,
            policy_revision=signals.task_policy_revision,
        )
        selection = resolver.resolve_selection(policy_request)
        profile = resolver.resolve(policy_request)
        plan = RunToolPlanFactory.create_for_selection(
            selection,
            created_by=ToolPlanCreator.DETERMINISTIC,
        )
        # Validate the immutable envelope binding before writing any journal
        # prefix, so a mismatched deployment catalog cannot leave partial F4
        # initialization facts behind.
        budget_record = _budget_record(
            signals=signals,
            profile=profile,
            budget_envelope=budget_envelope,
        )
        raw_records = await load_records()
        try:
            records = tuple(_unwrap_record(item) for item in raw_records)
        except (TypeError, ValueError) as exc:
            raise TaskPolicyRuntimeError(
                "F4 replay contains a corrupt durable record"
            ) from exc
        if records:
            self._validate_existing(
                signals=signals,
                selection=selection,
                plan=plan,
                records=records,
                expected_budget=budget_record,
                require_complete=False,
            )
        records = await self._initialize(
            signals=signals,
            selection=selection,
            profile=profile,
            plan=plan,
            budget_record=budget_record,
            append_record=append_record,
            existing_records=records,
        )
        self._validate_existing(
            signals=signals,
            selection=selection,
            plan=plan,
            records=records,
            expected_budget=budget_record,
            require_complete=True,
        )
        domain_records = _replay_domain_records(records)
        controller = ToolUseController.rebuild(
            profile=profile,
            records=domain_records,
        )
        durable = DurableTaskPolicyController(
            signals=signals,
            mode=mode,
            selection_ref=signals.task_policy_selection_ref,
            selection=selection,
            plan=plan,
            controller=controller,
            fingerprinter=self._fingerprinter,
            append_record=append_record,
            journal_records=records,
        )
        return TaskPolicyRuntimeBinding(
            selection=selection,
            profile=profile,
            controller=durable,
            fingerprinter=self._fingerprinter,
            mode=mode,
            progress_projector=lambda: _project_progress(
                profile=profile,
                plan=plan,
                state=durable.state,
            ),
        )

    async def _initialize(
        self,
        *,
        signals: VerifiedTaskPolicySignals,
        selection: TaskPolicySelection,
        profile: TaskPolicyProfile,
        plan: RunToolPlan | None,
        budget_record: TaskPolicyBudgetRecordedRecord,
        append_record: Callable[[object], Awaitable[object]],
        existing_records: Sequence[TaskPolicyJournalRecord],
    ) -> tuple[TaskPolicyJournalRecord, ...]:
        created = list(existing_records)

        async def persist(record: TaskPolicyJournalRecord) -> None:
            existing = next(
                (item for item in created if item.record_id == record.record_id),
                None,
            )
            if existing is not None:
                if existing.record_digest != record.record_digest:
                    raise TaskPolicyRuntimeError(
                        "canonical F4 initialization record conflicts"
                    )
                return
            durable = _unwrap_record(await append_record(record))
            if durable.record_digest != record.record_digest:
                raise TaskPolicyRuntimeError(
                    "canonical F4 initialization append changed the record"
                )
            created.append(durable)

        profile_record = TaskPolicyProfileSelectedRecord.create(
            record_id=_stable_id("f4-profile", selection.selection_digest),
            run_id=signals.run_id,
            snapshot_id=signals.snapshot_id,
            selection_ref=signals.task_policy_selection_ref,
            selection_digest=selection.selection_digest,
            profile_id=profile.profile_id,
            profile_revision=profile.revision,
            task_family=profile.task_family,
            planning_requirement=profile.planning_requirement,
            selection_reason=selection.selection_reason,
        )
        await persist(profile_record)
        if plan is not None:
            digest = _plan_digest(plan)
            await persist(
                TaskPolicyPlanBoundRecord.create(
                    record_id=_stable_id("f4-plan", digest),
                    run_id=signals.run_id,
                    snapshot_id=signals.snapshot_id,
                    selection_ref=signals.task_policy_selection_ref,
                    selection_digest=selection.selection_digest,
                    plan_id=plan.plan_id,
                    plan_ref=f"task-plan://{plan.plan_id}/sha256/{digest}",
                    plan_digest=digest,
                    created_by=plan.created_by.value,
                    status=plan.status.value,
                    step_count=len(plan.steps),
                    success_evidence_requirement_count=len(plan.success_evidence),
                )
            )
        await persist(budget_record)
        return tuple(created)

    @staticmethod
    def _validate_existing(
        *,
        signals: VerifiedTaskPolicySignals,
        selection: TaskPolicySelection,
        plan: RunToolPlan | None,
        records: Sequence[TaskPolicyJournalRecord],
        expected_budget: TaskPolicyBudgetRecordedRecord,
        require_complete: bool,
    ) -> None:
        profiles = tuple(
            item
            for item in records
            if isinstance(item, TaskPolicyProfileSelectedRecord)
        )
        if len(profiles) != 1:
            raise TaskPolicyRuntimeError(
                "F4 replay requires exactly one profile selection"
            )
        bound = profiles[0]
        if (
            bound.run_id != signals.run_id
            or bound.snapshot_id != signals.snapshot_id
            or bound.selection_ref != signals.task_policy_selection_ref
            or bound.selection_digest != selection.selection_digest
            or bound.profile_id != selection.profile_id
            or bound.profile_revision != selection.profile_revision
        ):
            raise TaskPolicyRuntimeError("F4 replay profile/scope binding mismatch")
        plans = tuple(
            item for item in records if isinstance(item, TaskPolicyPlanBoundRecord)
        )
        if plan is None:
            if plans:
                raise TaskPolicyRuntimeError("F4 replay has an unexpected plan")
        else:
            if len(plans) > 1 or (require_complete and len(plans) != 1):
                raise TaskPolicyRuntimeError("F4 replay plan binding mismatch")
            if plans and (
                plans[0].plan_id != plan.plan_id
                or plans[0].plan_digest != _plan_digest(plan)
            ):
                raise TaskPolicyRuntimeError("F4 replay plan binding mismatch")
        budgets = tuple(
            item for item in records if isinstance(item, TaskPolicyBudgetRecordedRecord)
        )
        if len(budgets) > 1 or (require_complete and len(budgets) != 1):
            raise TaskPolicyRuntimeError("F4 replay budget binding mismatch")
        if budgets and budgets[0].record_digest != expected_budget.record_digest:
            raise TaskPolicyRuntimeError("F4 replay budget binding mismatch")


def _unwrap_record(value: object) -> TaskPolicyJournalRecord:
    if isinstance(value, SequencedTaskPolicyJournalRecord):
        return value.record
    return validate_task_policy_journal_record(value)


def _replay_domain_records(
    records: Sequence[TaskPolicyJournalRecord],
) -> tuple[
    TaskPolicyBudgetRecord
    | ToolUseIntent
    | ToolOperationOutcome
    | ModelTurnRecord
    | ToolPlanProgressRecord,
    ...,
]:
    intent_records = {
        item.record_id: item
        for item in records
        if isinstance(item, TaskPolicyIntentRecordedRecord)
    }
    admitted_intents = {
        item.intent_record_id
        for item in records
        if isinstance(item, TaskPolicyAdmissionRecordedRecord)
        and item.disposition is not TaskPolicyAdmissionDisposition.BLOCKED
    }
    replay: list[
        TaskPolicyBudgetRecord
        | ToolUseIntent
        | ToolOperationOutcome
        | ModelTurnRecord
        | ToolPlanProgressRecord
    ] = []
    for record in records:
        if isinstance(record, TaskPolicyBudgetRecordedRecord):
            replay.append(
                TaskPolicyBudgetRecord(
                    budget_id=record.record_id,
                    model_turn_limit=record.model_turn_limit,
                    total_tool_call_limit=record.tool_call_limit,
                    cost_limit_microusd=record.cost_microusd_limit,
                    deadline_at=record.deadline_at,
                )
            )
        elif (
            isinstance(record, TaskPolicyIntentRecordedRecord)
            and record.record_id in admitted_intents
        ):
            replay.append(
                ToolUseIntent(
                    operation_id=record.operation_id,
                    capability_id=record.capability_id,
                    canonical_request_fingerprint=record.request_fingerprint,
                    plan_step_id=record.plan_step_id,
                    expected_evidence_kind=record.expected_evidence_kind,
                    semantic_fingerprint=record.semantic_fingerprint,
                    objective_fingerprint=record.objective_fingerprint,
                )
            )
        elif isinstance(record, TaskPolicyOutcomeRecordedRecord):
            intent = intent_records.get(record.intent_record_id)
            if intent is None:
                raise TaskPolicyRuntimeError("F4 outcome references missing intent")
            replay.append(
                ToolOperationOutcome(
                    operation_id=record.operation_id,
                    capability_id=intent.capability_id,
                    succeeded=record.status is TaskPolicyOutcomeStatus.SUCCEEDED,
                    error_class=record.failure_class,
                    retryable=record.retryable,
                    source_fingerprints=record.source_fingerprints,
                    result_fingerprint=record.result_fingerprint,
                    evidence_fingerprint=record.evidence_fingerprint,
                    error_fingerprint=record.error_fingerprint,
                    cost_microusd=record.cost_microusd,
                )
            )
        elif (
            isinstance(record, TaskPolicyModelTurnRecordedRecord)
            and record.disposition is not TaskPolicyAdmissionDisposition.BLOCKED
        ):
            replay.append(
                ModelTurnRecord(
                    turn_id=record.turn_id,
                    cost_microusd=record.cost_microusd,
                )
            )
        elif isinstance(record, TaskPolicyProgressRecordedRecord):
            replay.append(
                ToolPlanProgressRecord(
                    progress_id=record.record_id,
                    plan_id=record.plan_id,
                    active_step_id=record.active_step_id,
                    completed_step_ids=tuple(
                        f"completed-{index}"
                        for index in range(record.completed_step_count)
                    ),
                    evidence_count=record.evidence_count,
                    objective_satisfied=record.plan_status == "completed",
                )
            )
    return tuple(replay)


def _project_progress(
    *,
    profile: TaskPolicyProfile,
    plan: RunToolPlan | None,
    state: ToolControllerState,
) -> TaskPolicyProgressProjection:
    capabilities = tuple(
        TaskPolicyCapabilityProgress(
            capability_id=capability_id,
            tool_calls_used=used,
            tool_call_limit=profile.call_limit_for(capability_id),
        )
        for capability_id, used in state.calls_by_capability[:256]
    )
    completed_steps = (
        min(state.evidence_count, len(plan.steps)) if plan is not None else 0
    )
    return TaskPolicyProgressProjection(
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        task_family=profile.task_family.value,
        model_turns_used=state.model_turns,
        model_turn_limit=profile.model_turn_limit,
        tool_calls_used=state.tool_calls,
        tool_call_limit=profile.total_tool_call_limit,
        cost_microusd_used=state.cost_microusd,
        cost_microusd_limit=profile.cost_limit_microusd,
        deadline_epoch_ms=(
            int(state.deadline_at.timestamp() * 1000)
            if state.deadline_at is not None
            else None
        ),
        completed_steps=completed_steps,
        total_steps=len(plan.steps) if plan is not None else 0,
        capabilities=capabilities,
    )


def _budget_record(
    *,
    signals: VerifiedTaskPolicySignals,
    profile: TaskPolicyProfile,
    budget_envelope: BudgetEnvelope | None,
) -> TaskPolicyBudgetRecordedRecord:
    if (
        budget_envelope is not None
        and budget_envelope.revision_ref != signals.budget_envelope_ref
    ):
        raise TaskPolicyRuntimeError(
            "loaded budget envelope does not match the run-control snapshot"
        )
    effective_tool_limit = _minimum(
        profile.total_tool_call_limit,
        signals.model_declared_tool_call_limit,
        budget_envelope.max_tool_calls if budget_envelope else None,
    )
    effective_turn_limit = _minimum(
        profile.model_turn_limit,
        budget_envelope.max_model_turns if budget_envelope else None,
    )
    effective_cost_limit = _minimum(
        profile.cost_limit_microusd,
        budget_envelope.max_cost_microusd if budget_envelope else None,
    )
    deadline = budget_envelope.deadline_at if budget_envelope else None
    digest_payload = {
        "model_turn_limit": effective_turn_limit,
        "tool_call_limit": effective_tool_limit,
        "cost_microusd_limit": effective_cost_limit,
        "deadline_at": _canonical_datetime(deadline),
    }
    return TaskPolicyBudgetRecordedRecord.create(
        record_id=_stable_id("f4-budget", digest_payload),
        run_id=signals.run_id,
        snapshot_id=signals.snapshot_id,
        budget_envelope_ref=signals.budget_envelope_ref,
        effective_budget_digest=canonical_json_sha256(digest_payload),
        model_turn_limit=effective_turn_limit,
        tool_call_limit=effective_tool_limit,
        cost_microusd_limit=effective_cost_limit,
        deadline_at=deadline,
        exhausted_dimensions=(),
        hard_stop=False,
    )


def _minimum(*values: int | None) -> int | None:
    present = tuple(value for value in values if value is not None)
    return min(present) if present else None


def _normalize_code(value: str) -> str:
    normalized = "".join(
        character.lower() if character.isalnum() else "_" for character in value.strip()
    ).strip("_")
    return (normalized or "unknown")[:120]


__all__ = (
    "DefaultTaskPolicyRuntimeFactory",
    "DeploymentTaskPolicyBundles",
    "DurableTaskPolicyController",
    "TaskPolicyFingerprinter",
    "TaskPolicyRuntimeError",
)
