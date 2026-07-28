"""Graph-wide F10 model invocation binding and LangChain middleware."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from threading import Lock
from time import monotonic
from typing import Any, Protocol, cast

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

from agent_runtime.control_plane.context import RunControlBinding, RunControlContext
from agent_runtime.execution.call_identity import RuntimeModelCallIdentity
from agent_runtime.execution.model_invocation.contracts import (
    ModelAttemptAdmissionRequest,
    ModelAttemptDecision,
    ModelAttemptDecisionKind,
    ModelAttemptDecisionReason,
    ModelAttemptOutcome,
    ModelDispatchState,
    ModelFailureClass,
    ModelFailureSignal,
    ModelInvocationAuthority,
    ModelInvocationRequirementsSnapshot,
    ModelRouteEntry,
    ModelRoutePlan,
    ModelStreamState,
)
from agent_runtime.execution.model_invocation.journal import (
    ModelAttemptAdmissionRecord,
    ModelAttemptFailedRecord,
    ModelAttemptLifecycleState,
    ModelAttemptStateRecord,
    ModelAttemptUsageRecord,
    ModelInvocationCompletedRecord,
    ModelInvocationFailedRecord,
    ModelInvocationFailureReason,
    ModelInvocationPlannedRecord,
    ModelInvocationRecord,
    ModelInvocationRecoveryRecord,
    ModelInvocationStorePort,
    ModelInvocationWrite,
    ModelRecoveryKind,
    ModelRecoveryOutcome,
    route_records,
)
from agent_runtime.execution.model_invocation.lifecycle import (
    ProviderAttemptLifecycle,
    ProviderLifecycleEvent,
    ProviderLifecycleReducer,
    ProviderTerminalState,
)
from agent_runtime.execution.model_invocation.policy import (
    ModelAttemptAdmissionPolicy,
    ProviderFailureClassifier,
)
from agent_runtime.execution.model_invocation.release_controls import (
    ModelReliabilityReleaseDecision,
)
from agent_runtime.execution.providers.model_failure_adapters import (
    ProviderFailureAdapterRegistry,
)
from agent_runtime.observability.attribution import Purpose
from agent_runtime.observability.token_usage import (
    NormalizedTokenUsage,
    TokenUsageExtractorRegistry,
)
from agent_runtime.prompts.cache_fallback import (
    PromptCacheFallbackContext,
    PromptCacheFallbackHandoff,
)
from agent_runtime.prompts.provider_cache import ProviderCacheFallbackSignal
from agent_runtime.prompts import tool_schema_revision
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


class AtomicModelInvocationAuthorityAdapterPort(Protocol):
    def prepare(
        self,
        *,
        authority_input: object,
        call_identity: RuntimeModelCallIdentity,
        control: RunControlBinding,
    ) -> object: ...


class EphemeralRouteModelResolverPort(Protocol):
    def resolve(
        self,
        route: ModelRouteEntry,
    ) -> BaseChatModel | Awaitable[BaseChatModel]: ...


class ModelInvocationReplayConflict(RuntimeError):
    """Replay cannot safely repeat or synthesize a provider response."""


class ModelInvocationPostResponsePersistenceError(RuntimeError):
    """Internal diagnostic delivered to the optional non-raising observer."""


class ModelCacheFallbackPosture(StrEnum):
    """F2 cache-rejection fallback is unavailable without an exact F2 handoff."""

    NOT_CONFIGURED = "not_configured"
    DENY = "deny"
    ENABLED = "enabled"


@dataclass(frozen=True, slots=True)
class _PreparedAuthority:
    authority: ModelInvocationAuthority
    requirements: ModelInvocationRequirementsSnapshot
    route_plan: ModelRoutePlan
    catalog_revision: str


@dataclass(frozen=True, slots=True)
class _PendingCacheRetry:
    decision: ModelAttemptDecision
    request: ModelRequest[Any]
    signal: ProviderCacheFallbackSignal


@dataclass(frozen=True, slots=True)
class ModelInvocationRuntimeBinding:
    """Run-scoped F10 inputs inherited by supervisor and local child graphs."""

    authority_adapter: AtomicModelInvocationAuthorityAdapterPort
    authority_input_factory: Callable[[str], object]
    journal: ModelInvocationStorePort
    route_model_resolver: EphemeralRouteModelResolverPort | None
    release: ModelReliabilityReleaseDecision
    org_id: str
    subject_fingerprint: str
    trace_id: str
    failure_adapters: ProviderFailureAdapterRegistry
    cache_fallback_posture: ModelCacheFallbackPosture = (
        ModelCacheFallbackPosture.NOT_CONFIGURED
    )
    projected_cost_microusd: int | None = None
    projected_input_tokens: int | None = None
    projected_output_tokens: int | None = None
    external_effect_observed: Callable[[], bool] = lambda: False
    post_response_error_observer: Callable[[Exception], None] | None = None
    circuit_success_observer: Callable[[ModelRouteEntry], None] | None = None
    circuit_failure_observer: (
        Callable[[ModelRouteEntry, ModelFailureClass], None] | None
    ) = None
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def __post_init__(self) -> None:
        if not self.org_id.strip() or not self.trace_id.strip():
            raise ValueError("model invocation journal scope is incomplete")
        if len(self.subject_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in self.subject_fingerprint
        ):
            raise ValueError("subject_fingerprint must be lowercase SHA-256")


class _ProviderLifecycleCallback(BaseCallbackHandler):
    """Per-attempt callback; retains only monotonic, body-free facts and usage."""

    def __init__(
        self,
        *,
        provider: str,
        adapters: ProviderFailureAdapterRegistry,
    ) -> None:
        self._provider = provider
        self._adapters = adapters
        self._reducer = ProviderLifecycleReducer()
        self._state = ProviderAttemptLifecycle()
        self._usage = NormalizedTokenUsage()
        self._usage_reported = False
        self._usage_record_id: str | None = None
        self._lock = Lock()

    @property
    def state(self) -> ProviderAttemptLifecycle:
        with self._lock:
            return self._state

    @property
    def usage(self) -> tuple[NormalizedTokenUsage, bool]:
        with self._lock:
            return (self._usage, self._usage_reported)

    @property
    def usage_record_id(self) -> str | None:
        """The stream accumulator's stable LangChain message id, when known."""

        with self._lock:
            return self._usage_record_id

    def dispatch_started(self) -> None:
        self._event(ProviderLifecycleEvent.DISPATCH_STARTED)

    def observe_response(self, response: ModelResponse[Any]) -> None:
        with self._lock:
            if self._state.terminal_state is not None:
                return
            self._acknowledge_locked()
            for message in response.result:
                self._observe_message_locked(message, streamed=False)
            self._state = self._reducer.reduce(
                self._state, ProviderLifecycleEvent.COMPLETED
            )

    def observe_error(self, error: BaseException) -> None:
        with self._lock:
            if self._state.terminal_state is not None:
                return
            observation = self._adapters.observe(self._provider, error, self._state)
            if (
                observation.signal is ModelFailureSignal.CONNECTIVITY
                and not self._state.stream_started
                and self._state.dispatch_started
            ):
                self._state = self._reducer.reduce(
                    self._state, ProviderLifecycleEvent.DISPATCH_NOT_ACCEPTED
                )
            self._state = self._reducer.reduce(
                self._state,
                ProviderLifecycleEvent.FAILED,
                failure_signal=observation.signal,
            )

    def observe_cache_rejection(self) -> None:
        """Apply the F2 adapter result without generic exception classification."""

        with self._lock:
            if not self._state.dispatch_started:
                self._state = self._reducer.reduce(
                    self._state, ProviderLifecycleEvent.DISPATCH_STARTED
                )
            self._state = self._reducer.refine_cache_rejection(self._state)

    def on_chat_model_start(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.dispatch_started()

    def on_llm_start(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.dispatch_started()

    def on_llm_new_token(
        self, token: str, *, chunk: object | None = None, **kwargs: object
    ) -> None:
        del kwargs
        with self._lock:
            if self._state.terminal_state is not None:
                return
            self._acknowledge_locked()
            if not self._state.stream_started:
                self._state = self._reducer.reduce(
                    self._state, ProviderLifecycleEvent.STREAM_STARTED
                )
            if token:
                self._state = self._reducer.reduce(
                    self._state, ProviderLifecycleEvent.VISIBLE_TEXT
                )
            if chunk is not None:
                self._observe_message_locked(chunk, streamed=True)

    def on_llm_end(self, response: object, **kwargs: object) -> None:
        del kwargs
        generations = getattr(response, "generations", ())
        messages: list[object] = []
        for generation_group in generations or ():
            for generation in generation_group or ():
                messages.append(getattr(generation, "message", generation))
        with self._lock:
            if self._state.terminal_state is not None:
                return
            self._acknowledge_locked()
            for message in messages:
                self._observe_message_locked(message, streamed=False)
            self._state = self._reducer.reduce(
                self._state, ProviderLifecycleEvent.COMPLETED
            )

    def on_llm_error(self, error: BaseException, **kwargs: object) -> None:
        del kwargs
        self.observe_error(error)

    def _event(self, event: ProviderLifecycleEvent) -> None:
        with self._lock:
            self._state = self._reducer.reduce(self._state, event)

    def _acknowledge_locked(self) -> None:
        if not self._state.dispatch_started:
            self._state = self._reducer.reduce(
                self._state, ProviderLifecycleEvent.DISPATCH_STARTED
            )
        if self._state.dispatch_state is not ModelDispatchState.ACCEPTED:
            self._state = self._reducer.reduce(
                self._state, ProviderLifecycleEvent.DISPATCH_ACKNOWLEDGED
            )

    def _observe_message_locked(self, message: object, *, streamed: bool) -> None:
        if streamed and not self._state.stream_started:
            self._state = self._reducer.reduce(
                self._state, ProviderLifecycleEvent.STREAM_STARTED
            )
        content = getattr(message, "content", None)
        if isinstance(content, str) and content:
            self._state = self._reducer.reduce(
                self._state, ProviderLifecycleEvent.VISIBLE_TEXT
            )
        elif isinstance(content, list) and content:
            self._state = self._reducer.reduce(
                self._state, ProviderLifecycleEvent.VISIBLE_TEXT
            )
        tool_calls = getattr(message, "tool_calls", None) or getattr(
            message, "tool_call_chunks", None
        )
        if tool_calls:
            self._state = self._reducer.reduce(
                self._state, ProviderLifecycleEvent.TOOL_CALL_CONTENT
            )
        observed = TokenUsageExtractorRegistry.for_provider(self._provider).extract(
            message
        )
        if observed is not None:
            message_id = getattr(message, "id", None)
            if isinstance(message_id, str) and message_id:
                self._usage_record_id = message_id
            self._usage = self._usage.merge(observed)
            self._usage_reported = True
            self._state = self._reducer.reduce(
                self._state, ProviderLifecycleEvent.USAGE_OBSERVED
            )


class ModelInvocationMiddleware(AgentMiddleware):
    """Inner F10 provider-call seam; RuntimeControl/F2 must run outside it."""

    name = "0xCopilotModelInvocationMiddleware"

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        # Current production F10 persistence is async. Legacy/sync graphs remain
        # byte-for-byte compatible when no binding is installed.
        if RunControlContext.model_invocation_runtime() is None:
            return handler(request)
        raise RuntimeError("enforced model invocation journaling requires async graph")

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        binding = RunControlContext.model_invocation_runtime()
        if binding is None:
            return await handler(request)
        control = RunControlContext.require_current()
        identity = RuntimeModelCallIdentity.from_current(
            execution_scope=self._execution_scope(request.runtime),
            model_turn=max(self._model_turn(request.state), 1),
        )
        if identity is None:  # pragma: no cover - require_current proves this
            raise RuntimeError("model invocation identity is unavailable")
        cache_handoff = (
            PromptCacheFallbackContext.current()
            if binding.cache_fallback_posture is ModelCacheFallbackPosture.ENABLED
            else None
        )
        semantic_request = request
        if cache_handoff is not None:
            cache_handoff.validate_provider_request(
                system_message=request.system_message,
                tools=request.tools or (),
            )
            semantic_request = request.override(
                system_message=cache_handoff.semantic_system_message()
            )
        request_digest = canonical_model_request_digest(semantic_request)
        prepared = self._prepare_authority(
            binding=binding,
            control=control,
            identity=identity,
            request_digest=request_digest,
        )
        invocation, records = await self._load_or_plan(
            binding=binding,
            identity=identity,
            prepared=prepared,
            request_digest=request_digest,
        )
        prior = self._prior_outcomes(records)
        await self._reconcile_replay(binding, invocation, records)
        last_error: BaseException | None = None
        pending_cache_retry: _PendingCacheRetry | None = None

        while True:
            cache_retry = pending_cache_retry
            pending_cache_retry = None
            if cache_retry is not None:
                decision = cache_retry.decision
                provider_request = cache_retry.request
            else:
                decision = ModelAttemptAdmissionPolicy().decide(
                    self._admission_request(
                        binding=binding,
                        route_plan=prepared.route_plan,
                        prior=prior,
                    )
                )
                decision = self._apply_release(
                    decision=decision,
                    prior=prior,
                    route_plan=prepared.route_plan,
                    release=binding.release,
                )
                provider_request = request
            if decision.kind is ModelAttemptDecisionKind.DENY:
                admission = self._admission_record(
                    binding=binding,
                    invocation=invocation,
                    decision=decision,
                    prior=prior,
                )
                await self._append(binding, admission)
                terminal = ModelInvocationFailedRecord.create(
                    invocation=invocation,
                    attempt_count=len(prior),
                    terminal_attempt_id=(prior[-1].attempt_id if prior else None),
                    reason=(
                        ModelInvocationFailureReason.NO_ELIGIBLE_ROUTE
                        if not prepared.route_plan.routes
                        else ModelInvocationFailureReason.ADMISSION_DENIED
                    ),
                    failure_class=prior[-1].failure_class if prior else None,
                    total_input_tokens=sum(item.input_tokens for item in prior),
                    total_output_tokens=sum(item.output_tokens for item in prior),
                    total_cost_microusd=sum(item.cost_microusd for item in prior),
                )
                await self._append(binding, terminal)
                if last_error is not None:
                    raise last_error
                raise RuntimeError(f"model invocation denied: {decision.reason.value}")

            # Resolve and decorate the ephemeral SDK model before recording a
            # provider attempt. A local resolver/configuration failure has not
            # crossed the provider boundary and must not leave an admitted,
            # replay-ambiguous attempt behind.
            route = next(
                route
                for route in prepared.route_plan.routes
                if route.deployment_id == decision.deployment_id
            )
            attempt_request = await self._route_request(
                request=provider_request,
                route=route,
                primary=prepared.route_plan.routes[0],
                binding=binding,
            )
            observer = _ProviderLifecycleCallback(
                provider=route.provider,
                adapters=binding.failure_adapters,
            )
            attempt_request = attempt_request.override(
                model=self._attach_callback(attempt_request.model, observer)
            )

            if prior and decision.kind is ModelAttemptDecisionKind.ADMIT:
                latest_recovery = next(
                    (
                        item
                        for item in reversed(records)
                        if isinstance(item, ModelInvocationRecoveryRecord)
                    ),
                    None,
                )
                if (
                    latest_recovery is None
                    or latest_recovery.source_attempt_id != prior[-1].attempt_id
                    or latest_recovery.outcome is not ModelRecoveryOutcome.ADMITTED
                ):
                    kind = (
                        ModelRecoveryKind.CACHE_UNDECORATED_RETRY
                        if decision.reason
                        is ModelAttemptDecisionReason.SAFE_CACHE_UNDECORATED_RETRY
                        else (
                            ModelRecoveryKind.SAME_DEPLOYMENT_RETRY
                            if decision.deployment_id == prior[-1].deployment_id
                            else ModelRecoveryKind.ALTERNATE_ROUTE
                        )
                    )
                    recovery = ModelInvocationRecoveryRecord.create(
                        invocation=invocation,
                        source_attempt_id=prior[-1].attempt_id,
                        recovery_ordinal=1
                        + sum(
                            isinstance(item, ModelInvocationRecoveryRecord)
                            for item in records
                        ),
                        kind=kind,
                        outcome=ModelRecoveryOutcome.ADMITTED,
                        decision_reason=decision.reason,
                        target_attempt_ordinal=len(prior) + 1,
                    )
                    await self._append(binding, recovery)
                    records = (*records, recovery)
            admission = self._admission_record(
                binding=binding,
                invocation=invocation,
                decision=decision,
                prior=prior,
            )
            await self._append(binding, admission)
            observer.dispatch_started()
            await self._append(
                binding,
                ModelAttemptStateRecord.create(
                    invocation=invocation,
                    admission=admission,
                    state=ModelAttemptLifecycleState.DISPATCHING,
                    dispatch_state=observer.state.dispatch_state,
                    stream_state=observer.state.stream_state,
                ),
            )
            started = monotonic()
            try:
                response = await handler(attempt_request)
            except BaseException as error:
                cache_signal = self._consume_cache_rejection(
                    handoff=cache_handoff,
                    error=error,
                    observer=observer,
                    binding=binding,
                    route=route,
                )
                if cache_signal is None:
                    observer.observe_error(error)
                else:
                    observer.observe_cache_rejection()
                duration_ms = max(0, int((monotonic() - started) * 1000))
                try:
                    prior, records = await self._record_failure(
                        binding=binding,
                        invocation=invocation,
                        admission=admission,
                        observer=observer,
                        prior=prior,
                        records=records,
                        duration_ms=duration_ms,
                    )
                    failure = prior[-1].failure_class
                    assert failure is not None
                    if binding.circuit_failure_observer is not None:
                        binding.circuit_failure_observer(route, failure)
                    if cache_signal is not None:
                        next_decision = (
                            ModelAttemptAdmissionPolicy().decide_cache_fallback(
                                self._admission_request(
                                    binding=binding,
                                    route_plan=prepared.route_plan,
                                    prior=prior,
                                )
                            )
                        )
                        if next_decision.kind is ModelAttemptDecisionKind.DENY:
                            await self._append_terminal_failure(
                                binding,
                                invocation,
                                prior,
                                admission,
                                failure,
                                duration_ms,
                            )
                            raise error
                        assert cache_handoff is not None
                        pending_cache_retry = _PendingCacheRetry(
                            decision=next_decision,
                            request=request.override(
                                system_message=(
                                    cache_handoff.undecorated_system_message()
                                )
                            ),
                            signal=cache_signal,
                        )
                        last_error = error
                        continue
                    if not self._can_retry(
                        failure, observer.state, binding, route, prepared.route_plan
                    ):
                        await self._append_terminal_failure(
                            binding, invocation, prior, admission, failure, duration_ms
                        )
                        raise error
                    next_decision = ModelAttemptAdmissionPolicy().decide(
                        self._admission_request(
                            binding=binding,
                            route_plan=prepared.route_plan,
                            prior=prior,
                        )
                    )
                    next_decision = self._apply_release(
                        decision=next_decision,
                        prior=prior,
                        route_plan=prepared.route_plan,
                        release=binding.release,
                    )
                    if next_decision.kind is ModelAttemptDecisionKind.DENY:
                        await self._append_terminal_failure(
                            binding, invocation, prior, admission, failure, duration_ms
                        )
                        raise error
                    last_error = error
                    continue
                except BaseException as persistence_error:
                    if persistence_error is error:
                        raise
                    raise error from persistence_error
            observer.observe_response(response)
            duration_ms = max(0, int((monotonic() - started) * 1000))
            try:
                await self._record_success(
                    binding=binding,
                    invocation=invocation,
                    admission=admission,
                    observer=observer,
                    prior=prior,
                    attempt_count=len(prior) + 1,
                    duration_ms=duration_ms,
                )
                if binding.circuit_success_observer is not None:
                    binding.circuit_success_observer(route)
            except Exception:  # post-response telemetry never discards output
                diagnostic = ModelInvocationPostResponsePersistenceError(
                    "provider response succeeded but F10 telemetry persistence failed"
                )
                if binding.post_response_error_observer is not None:
                    binding.post_response_error_observer(diagnostic)
            return response

    @staticmethod
    def _admission_record(
        *,
        binding: ModelInvocationRuntimeBinding,
        invocation: ModelInvocationPlannedRecord,
        decision: ModelAttemptDecision,
        prior: tuple[ModelAttemptOutcome, ...],
    ) -> ModelAttemptAdmissionRecord:
        return ModelAttemptAdmissionRecord.create(
            invocation=invocation,
            decision=decision,
            admission_ordinal=len(prior) + 1,
            prior_attempt_count=len(prior),
            external_effect_observed=binding.external_effect_observed(),
            projected_cost_microusd=binding.projected_cost_microusd,
            projected_input_tokens=binding.projected_input_tokens,
            projected_output_tokens=binding.projected_output_tokens,
        )

    @staticmethod
    def _admission_request(
        *,
        binding: ModelInvocationRuntimeBinding,
        route_plan: ModelRoutePlan,
        prior: tuple[ModelAttemptOutcome, ...],
    ) -> ModelAttemptAdmissionRequest:
        return ModelAttemptAdmissionRequest(
            route_plan=route_plan,
            now=binding.now(),
            prior_attempts=prior,
            external_effect_observed=binding.external_effect_observed(),
            projected_cost_microusd=binding.projected_cost_microusd,
            projected_input_tokens=binding.projected_input_tokens,
            projected_output_tokens=binding.projected_output_tokens,
        )

    @staticmethod
    def _consume_cache_rejection(
        *,
        handoff: PromptCacheFallbackHandoff | None,
        error: BaseException,
        observer: _ProviderLifecycleCallback,
        binding: ModelInvocationRuntimeBinding,
        route: ModelRouteEntry,
    ) -> ProviderCacheFallbackSignal | None:
        if handoff is None:
            return None
        state = observer.state
        return handoff.consume_rejection(
            error,
            provider=route.provider,
            model_family=route.model_name,
            provider_acknowledged=(state.dispatch_state is ModelDispatchState.ACCEPTED),
            content_observed=state.visible_text_observed,
            tool_call_observed=state.tool_call_content_observed,
            usage_observed=state.usage_observed,
            external_effect_observed=binding.external_effect_observed(),
        )

    @staticmethod
    def _prepare_authority(
        *,
        binding: ModelInvocationRuntimeBinding,
        control: RunControlBinding,
        identity: RuntimeModelCallIdentity,
        request_digest: str,
    ) -> _PreparedAuthority:
        raw = binding.authority_adapter.prepare(
            authority_input=binding.authority_input_factory(request_digest),
            call_identity=identity,
            control=control,
        )
        authority = ModelInvocationAuthority.model_validate(
            getattr(raw, "authority", None)
        )
        requirements = ModelInvocationRequirementsSnapshot.model_validate(
            getattr(raw, "requirements", None)
        )
        route_plan = ModelRoutePlan.model_validate(getattr(raw, "route_plan", None))
        catalog = getattr(raw, "catalog", None)
        revision = str(getattr(catalog, "catalog_revision", "")).strip()
        if not revision:
            raise RuntimeError("atomic authority result lacks catalog revision")
        if authority.request_digest != request_digest:
            raise RuntimeError("atomic authority changed the canonical request digest")
        return _PreparedAuthority(authority, requirements, route_plan, revision)

    async def _load_or_plan(
        self,
        *,
        binding: ModelInvocationRuntimeBinding,
        identity: RuntimeModelCallIdentity,
        prepared: _PreparedAuthority,
        request_digest: str,
    ) -> tuple[ModelInvocationPlannedRecord, tuple[ModelInvocationRecord, ...]]:
        durable = await binding.journal.list_for_run(
            org_id=binding.org_id,
            run_id=identity.run_id,
            subject_fingerprint=binding.subject_fingerprint,
        )
        records = tuple(
            item.record
            for item in durable
            if item.record.model_call_id == identity.model_call_id
        )
        planned = next(
            (
                item
                for item in records
                if isinstance(item, ModelInvocationPlannedRecord)
            ),
            None,
        )
        purpose = Purpose(prepared.authority.purpose)
        bare_request_digest = _bare_digest(request_digest)
        bare_requirements_digest = _bare_digest(
            prepared.requirements.requirements_digest
        )
        if planned is not None:
            expected = (
                planned.request_digest == bare_request_digest
                and planned.requirements_digest == bare_requirements_digest
                and planned.requirements_revision
                == prepared.requirements.requirements_revision
                and planned.descriptor_set_revision == prepared.catalog_revision
                and planned.route_digest == prepared.route_plan.route_digest
                and planned.route_policy_revision == prepared.route_plan.policy_revision
            )
            if not expected:
                raise ModelInvocationReplayConflict(
                    "model invocation replay conflicts with current authority"
                )
            if any(
                isinstance(
                    item,
                    (ModelInvocationCompletedRecord, ModelInvocationFailedRecord),
                )
                for item in records
            ):
                raise ModelInvocationReplayConflict(
                    "terminal model invocation cannot be blindly re-dispatched"
                )
            return (planned, records)
        planned = ModelInvocationPlannedRecord.create(
            binding=RunControlContext.require_current(),
            identity=identity,
            purpose=purpose,
            request_digest=bare_request_digest,
            requirements_digest=bare_requirements_digest,
            requirements_revision=prepared.requirements.requirements_revision,
            descriptor_set_revision=prepared.catalog_revision,
            route_plan=prepared.route_plan,
        )
        await self._append(binding, planned)
        projected = route_records(planned, prepared.route_plan)
        for record in projected:
            await self._append(binding, record)
        return (planned, (planned, *projected))

    @staticmethod
    def _prior_outcomes(
        records: tuple[ModelInvocationRecord, ...],
    ) -> tuple[ModelAttemptOutcome, ...]:
        admissions = tuple(
            item
            for item in records
            if isinstance(item, ModelAttemptAdmissionRecord)
            and item.decision is ModelAttemptDecisionKind.ADMIT
        )
        failures = {
            item.attempt_id: item
            for item in records
            if isinstance(item, ModelAttemptFailedRecord)
        }
        usages = {
            item.attempt_id: item
            for item in records
            if isinstance(item, ModelAttemptUsageRecord)
        }
        outcomes: list[ModelAttemptOutcome] = []
        for admission in admissions:
            if admission.attempt_id not in failures:
                continue
            failure = failures[cast(str, admission.attempt_id)]
            usage = usages.get(cast(str, admission.attempt_id))
            outcomes.append(
                ModelAttemptOutcome(
                    attempt_id=cast(str, admission.attempt_id),
                    ordinal=cast(int, admission.attempt_ordinal),
                    deployment_id=cast(str, admission.deployment_id),
                    failure_class=failure.failure_class,
                    stream_state=failure.stream_state,
                    cost_microusd=usage.cost_microusd if usage else 0,
                    input_tokens=usage.input_tokens if usage else 0,
                    output_tokens=usage.output_tokens if usage else 0,
                )
            )
        return tuple(outcomes)

    async def _reconcile_replay(
        self,
        binding: ModelInvocationRuntimeBinding,
        invocation: ModelInvocationPlannedRecord,
        records: tuple[ModelInvocationRecord, ...],
    ) -> None:
        admissions = tuple(
            item
            for item in records
            if isinstance(item, ModelAttemptAdmissionRecord)
            and item.decision is ModelAttemptDecisionKind.ADMIT
        )
        failures = {
            item.attempt_id
            for item in records
            if isinstance(item, ModelAttemptFailedRecord)
        }
        states = tuple(
            item for item in records if isinstance(item, ModelAttemptStateRecord)
        )
        usages = {
            item.attempt_id: item
            for item in records
            if isinstance(item, ModelAttemptUsageRecord)
        }
        for admission in admissions:
            attempt_id = cast(str, admission.attempt_id)
            attempt_states = tuple(
                item for item in states if item.attempt_id == attempt_id
            )
            completed = any(
                item.state is ModelAttemptLifecycleState.COMPLETED
                for item in attempt_states
            )
            if completed:
                if attempt_id not in usages:
                    usage = ModelAttemptUsageRecord.create(
                        invocation=invocation,
                        admission=admission,
                        usage=NormalizedTokenUsage(),
                        provider_reported=False,
                    )
                    await self._append(binding, usage)
                    usages[attempt_id] = usage
                await self._append(
                    binding,
                    ModelInvocationCompletedRecord.create(
                        invocation=invocation,
                        terminal_attempt_id=attempt_id,
                        attempt_count=len(admissions),
                        total_input_tokens=sum(
                            item.input_tokens for item in usages.values()
                        ),
                        total_output_tokens=sum(
                            item.output_tokens for item in usages.values()
                        ),
                        total_cost_microusd=sum(
                            item.cost_microusd for item in usages.values()
                        ),
                    ),
                )
                raise ModelInvocationReplayConflict(
                    "completed provider output cannot be synthesized during replay"
                )
            if attempt_id in failures:
                continue

            last = attempt_states[-1] if attempt_states else None
            dispatch_state = (
                last.dispatch_state if last is not None else ModelDispatchState.UNKNOWN
            )
            stream_state = (
                last.stream_state if last is not None else ModelStreamState.NOT_STARTED
            )
            visible = last.visible_text_emitted if last is not None else False
            tool_content = last.tool_call_content_emitted if last is not None else False
            if not any(
                item.state is ModelAttemptLifecycleState.AMBIGUOUS
                for item in attempt_states
            ):
                await self._append(
                    binding,
                    ModelAttemptStateRecord.create(
                        invocation=invocation,
                        admission=admission,
                        state=ModelAttemptLifecycleState.AMBIGUOUS,
                        dispatch_state=dispatch_state,
                        stream_state=stream_state,
                        visible_text_emitted=visible,
                        tool_call_content_emitted=tool_content,
                        external_effect_observed=binding.external_effect_observed(),
                    ),
                )
            failed = ModelAttemptFailedRecord.create(
                invocation=invocation,
                admission=admission,
                failure_class=ModelFailureClass.AMBIGUOUS_PROVIDER_STATE,
                dispatch_state=dispatch_state,
                stream_state=stream_state,
                provider_failure_observed=False,
                visible_text_emitted=visible,
                tool_call_content_emitted=tool_content,
                external_effect_observed=binding.external_effect_observed(),
                usage_may_be_incomplete=True,
            )
            await self._append(binding, failed)
            if attempt_id not in usages:
                usage = ModelAttemptUsageRecord.create(
                    invocation=invocation,
                    admission=admission,
                    usage=NormalizedTokenUsage(),
                    provider_reported=False,
                )
                await self._append(binding, usage)
                usages[attempt_id] = usage
            await self._append(
                binding,
                ModelInvocationFailedRecord.create(
                    invocation=invocation,
                    terminal_attempt_id=attempt_id,
                    attempt_count=len(admissions),
                    reason=ModelInvocationFailureReason.AMBIGUOUS_RECOVERY,
                    failure_class=ModelFailureClass.AMBIGUOUS_PROVIDER_STATE,
                    total_input_tokens=sum(
                        item.input_tokens for item in usages.values()
                    ),
                    total_output_tokens=sum(
                        item.output_tokens for item in usages.values()
                    ),
                    total_cost_microusd=sum(
                        item.cost_microusd for item in usages.values()
                    ),
                ),
            )
            raise ModelInvocationReplayConflict(
                f"open provider attempt is ambiguous for {invocation.invocation_id}"
            )

    async def _record_success(
        self,
        *,
        binding: ModelInvocationRuntimeBinding,
        invocation: ModelInvocationPlannedRecord,
        admission: ModelAttemptAdmissionRecord,
        observer: _ProviderLifecycleCallback,
        prior: tuple[ModelAttemptOutcome, ...],
        attempt_count: int,
        duration_ms: int,
    ) -> None:
        await self._append_states(binding, invocation, admission, observer.state)
        usage, reported = observer.usage
        await self._append(
            binding,
            ModelAttemptUsageRecord.create(
                invocation=invocation,
                admission=admission,
                usage=usage,
                provider_reported=reported,
                usage_record_id=observer.usage_record_id,
                duration_ms=duration_ms,
            ),
        )
        await self._append(
            binding,
            ModelInvocationCompletedRecord.create(
                invocation=invocation,
                terminal_attempt_id=cast(str, admission.attempt_id),
                attempt_count=attempt_count,
                total_input_tokens=sum(item.input_tokens for item in prior)
                + usage.input_tokens,
                total_output_tokens=sum(item.output_tokens for item in prior)
                + usage.output_tokens,
                total_cost_microusd=sum(item.cost_microusd for item in prior),
                total_duration_ms=duration_ms,
            ),
        )

    async def _record_failure(
        self,
        *,
        binding: ModelInvocationRuntimeBinding,
        invocation: ModelInvocationPlannedRecord,
        admission: ModelAttemptAdmissionRecord,
        observer: _ProviderLifecycleCallback,
        prior: tuple[ModelAttemptOutcome, ...],
        records: tuple[ModelInvocationRecord, ...],
        duration_ms: int,
    ) -> tuple[tuple[ModelAttemptOutcome, ...], tuple[ModelInvocationRecord, ...]]:
        state = observer.state
        await self._append_states(binding, invocation, admission, state)
        observation = state.failure_observation()
        failure_class = ProviderFailureClassifier().classify(observation)
        failed = ModelAttemptFailedRecord.create(
            invocation=invocation,
            admission=admission,
            failure_class=failure_class,
            dispatch_state=observation.dispatch_state,
            stream_state=observation.stream_state,
            provider_failure_observed=observation.signal
            is not ModelFailureSignal.UNKNOWN,
            visible_text_emitted=state.visible_text_observed,
            tool_call_content_emitted=state.tool_call_content_observed,
            external_effect_observed=binding.external_effect_observed(),
            usage_may_be_incomplete=state.usage_observed,
        )
        await self._append(binding, failed)
        usage, reported = observer.usage
        usage_record = ModelAttemptUsageRecord.create(
            invocation=invocation,
            admission=admission,
            usage=usage if reported else NormalizedTokenUsage(),
            provider_reported=reported,
            usage_record_id=observer.usage_record_id,
            duration_ms=duration_ms,
        )
        await self._append(binding, usage_record)
        outcome = ModelAttemptOutcome(
            attempt_id=cast(str, admission.attempt_id),
            ordinal=cast(int, admission.attempt_ordinal),
            deployment_id=cast(str, admission.deployment_id),
            failure_class=failure_class,
            stream_state=observation.stream_state,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        return ((*prior, outcome), (*records, admission, failed, usage_record))

    async def _append_states(
        self,
        binding: ModelInvocationRuntimeBinding,
        invocation: ModelInvocationPlannedRecord,
        admission: ModelAttemptAdmissionRecord,
        state: ProviderAttemptLifecycle,
    ) -> None:
        facts: list[ModelAttemptLifecycleState] = []
        if state.dispatch_state is ModelDispatchState.ACCEPTED:
            facts.append(ModelAttemptLifecycleState.ACCEPTED)
        if state.stream_started:
            facts.append(ModelAttemptLifecycleState.STREAM_STARTED)
        if state.visible_text_observed:
            facts.append(ModelAttemptLifecycleState.VISIBLE_OUTPUT)
        if state.tool_call_content_observed:
            facts.append(ModelAttemptLifecycleState.TOOL_CALL_CONTENT)
        if state.terminal_state is ProviderTerminalState.COMPLETED:
            facts.append(ModelAttemptLifecycleState.COMPLETED)
        elif (
            state.failure_signal is ModelFailureSignal.CANCELLED
            or state.terminal_state is ProviderTerminalState.CANCELLED
        ):
            facts.append(ModelAttemptLifecycleState.CANCELLED)
        elif (
            state.terminal_state is ProviderTerminalState.FAILED
            and ProviderFailureClassifier().classify(state.failure_observation())
            is ModelFailureClass.AMBIGUOUS_PROVIDER_STATE
        ):
            facts.append(ModelAttemptLifecycleState.AMBIGUOUS)
        for fact in facts:
            await self._append(
                binding,
                ModelAttemptStateRecord.create(
                    invocation=invocation,
                    admission=admission,
                    state=fact,
                    dispatch_state=state.dispatch_state,
                    stream_state=state.stream_state,
                    visible_text_emitted=state.visible_text_observed,
                    tool_call_content_emitted=state.tool_call_content_observed,
                    external_effect_observed=binding.external_effect_observed(),
                ),
            )

    async def _append_terminal_failure(
        self,
        binding: ModelInvocationRuntimeBinding,
        invocation: ModelInvocationPlannedRecord,
        prior: tuple[ModelAttemptOutcome, ...],
        admission: ModelAttemptAdmissionRecord,
        failure: ModelFailureClass,
        duration_ms: int,
    ) -> None:
        reason = {
            ModelFailureClass.CANCELLED: ModelInvocationFailureReason.CANCELLED,
            ModelFailureClass.DEADLINE_EXCEEDED: (
                ModelInvocationFailureReason.DEADLINE_EXCEEDED
            ),
            ModelFailureClass.AMBIGUOUS_PROVIDER_STATE: (
                ModelInvocationFailureReason.AMBIGUOUS_RECOVERY
            ),
        }.get(failure, ModelInvocationFailureReason.ATTEMPT_FAILED)
        await self._append(
            binding,
            ModelInvocationFailedRecord.create(
                invocation=invocation,
                terminal_attempt_id=cast(str, admission.attempt_id),
                attempt_count=len(prior),
                reason=reason,
                failure_class=failure,
                total_input_tokens=sum(item.input_tokens for item in prior),
                total_output_tokens=sum(item.output_tokens for item in prior),
                total_cost_microusd=sum(item.cost_microusd for item in prior),
                total_duration_ms=duration_ms,
            ),
        )

    @staticmethod
    def _can_retry(
        failure: ModelFailureClass,
        state: ProviderAttemptLifecycle,
        binding: ModelInvocationRuntimeBinding,
        route: ModelRouteEntry,
        route_plan: ModelRoutePlan,
    ) -> bool:
        del route, route_plan
        return (
            (
                binding.release.same_deployment_retry_enabled
                or binding.release.alternate_route_enabled
                or binding.release.equivalent_route_enabled
            )
            and failure
            in {
                ModelFailureClass.PRE_DISPATCH_TRANSIENT,
                ModelFailureClass.PROVIDER_OVERLOADED,
                ModelFailureClass.STREAM_INTERRUPTED_BEFORE_CONTENT,
            }
            and not (
                state.visible_output_observed
                or binding.external_effect_observed()
                or failure is ModelFailureClass.AMBIGUOUS_PROVIDER_STATE
            )
        )

    @staticmethod
    def _apply_release(
        *,
        decision: ModelAttemptDecision,
        prior: tuple[ModelAttemptOutcome, ...],
        route_plan: ModelRoutePlan,
        release: ModelReliabilityReleaseDecision,
    ) -> ModelAttemptDecision:
        if decision.kind is ModelAttemptDecisionKind.DENY or not prior:
            return decision
        source = prior[-1].deployment_id
        target = cast(str, decision.deployment_id)
        routes = {route.deployment_id: route for route in route_plan.routes}
        if ModelInvocationMiddleware._route_release_enabled(
            source=source,
            target=target,
            routes=routes,
            release=release,
        ):
            return decision

        # A disabled same-deployment retry must not mask an independently
        # released alternate/equivalent route. Skip directly to the first
        # deterministic, unattempted route whose own release gate is open.
        attempted = {item.deployment_id for item in prior}
        for candidate in route_plan.routes:
            if candidate.deployment_id in attempted:
                continue
            if ModelInvocationMiddleware._route_release_enabled(
                source=source,
                target=candidate.deployment_id,
                routes=routes,
                release=release,
            ):
                return ModelAttemptDecision(
                    kind=ModelAttemptDecisionKind.ADMIT,
                    reason=ModelAttemptDecisionReason.SAFE_ALTERNATE_ROUTE,
                    deployment_id=candidate.deployment_id,
                    ordinal=decision.ordinal,
                )
        return ModelAttemptDecision(
            kind=ModelAttemptDecisionKind.DENY,
            reason=ModelAttemptDecisionReason.FAILURE_NOT_RETRYABLE,
        )

    @staticmethod
    def _route_release_enabled(
        *,
        source: str,
        target: str,
        routes: Mapping[str, ModelRouteEntry],
        release: ModelReliabilityReleaseDecision,
    ) -> bool:
        if target == source:
            return release.same_deployment_retry_enabled
        source_route = routes[source]
        target_route = routes[target]
        equivalent_model = (
            target_route.provider != source_route.provider
            or target_route.model_name != source_route.model_name
        )
        return (
            release.equivalent_route_enabled
            if equivalent_model
            else release.alternate_route_enabled
        )

    @staticmethod
    async def _route_request(
        *,
        request: ModelRequest[Any],
        route: ModelRouteEntry,
        primary: ModelRouteEntry,
        binding: ModelInvocationRuntimeBinding,
    ) -> ModelRequest[Any]:
        if route.deployment_id == primary.deployment_id:
            return request
        if binding.route_model_resolver is None:
            raise RuntimeError("alternate route has no ephemeral model resolver")
        model = binding.route_model_resolver.resolve(route)
        if asyncio.iscoroutine(model):
            model = await model
        if not isinstance(model, BaseChatModel):
            raise RuntimeError("route model resolver returned a non-chat model")
        return request.override(model=model)

    @staticmethod
    def _attach_callback(
        model: BaseChatModel, observer: BaseCallbackHandler
    ) -> BaseChatModel:
        callbacks = list(getattr(model, "callbacks", None) or ())
        callbacks.append(observer)
        copied = model.model_copy(update={"callbacks": callbacks})
        if not isinstance(copied, BaseChatModel):
            raise RuntimeError("provider callback attachment changed model type")
        return copied

    @staticmethod
    async def _append(
        binding: ModelInvocationRuntimeBinding, record: ModelInvocationRecord
    ) -> None:
        await binding.journal.append(
            ModelInvocationWrite(
                org_id=binding.org_id,
                subject_fingerprint=binding.subject_fingerprint,
                trace_id=binding.trace_id,
                record=record,
            )
        )

    @staticmethod
    def _execution_scope(runtime: object) -> str:
        config = getattr(runtime, "config", None)
        if not isinstance(config, Mapping):
            return "supervisor"
        for container_name in ("metadata", "configurable"):
            container = config.get(container_name)
            if not isinstance(container, Mapping):
                continue
            task_id = container.get("supervisor_task_call_id")
            if isinstance(task_id, str) and task_id.strip():
                return f"subagent:{task_id.strip()}"
        return "supervisor"

    @staticmethod
    def _model_turn(state: object) -> int:
        if isinstance(state, Mapping):
            value = state.get("runtime_control_model_turn")
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return 0


def canonical_model_request_digest(request: ModelRequest[Any]) -> str:
    """Hash final F2/model-call semantics without retaining any request body."""

    def message(value: BaseMessage | None) -> object:
        return None if value is None else value.model_dump(mode="json")

    model = request.model
    provider = str(getattr(model, "_llm_type", type(model).__name__)).strip().lower()
    model_name = ""
    for attribute in ("model_name", "model"):
        raw = getattr(model, attribute, None)
        if isinstance(raw, str) and raw.strip():
            model_name = raw.strip()
            break
    payload = {
        "messages": [message(item) for item in request.messages],
        "system_message": message(request.system_message),
        "tool_schema_revision": tool_schema_revision(request.tools or ()),
        "tool_choice": _json_value(request.tool_choice),
        "response_format": _response_format_identity(request.response_format),
        "model_settings": _json_value(request.model_settings),
        "provider": provider,
        "model_name": model_name,
    }
    return f"sha256:{canonical_json_sha256(payload)}"


def _response_format_identity(value: object) -> object:
    if value is None:
        return None
    schema = getattr(value, "schema", None)
    if isinstance(schema, type) and callable(
        getattr(schema, "model_json_schema", None)
    ):
        return {
            "kind": f"{type(value).__module__}.{type(value).__qualname__}",
            "schema": schema.model_json_schema(),
        }
    return {"kind": f"{type(value).__module__}.{type(value).__qualname__}"}


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise ValueError(
        f"model request digest contains unsupported {type(value).__name__}"
    )


def _bare_digest(value: str) -> str:
    prefix = "sha256:"
    if not value.startswith(prefix) or len(value) != len(prefix) + 64:
        raise ValueError("authority digest must use sha256:<64-hex>")
    return value.removeprefix(prefix)


__all__ = (
    "AtomicModelInvocationAuthorityAdapterPort",
    "EphemeralRouteModelResolverPort",
    "ModelInvocationMiddleware",
    "ModelCacheFallbackPosture",
    "ModelInvocationPostResponsePersistenceError",
    "ModelInvocationReplayConflict",
    "ModelInvocationRuntimeBinding",
    "canonical_model_request_digest",
)
