"""Graph-wide F10 model invocation binding and LangChain middleware."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import logging
import random
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, Any, ClassVar, Final, Protocol, cast

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
from agent_runtime.execution.model_invocation.retry_schedule import (
    ModelCallRetryPolicy,
    ModelRetryDecision,
    provider_retry_hint,
)
from agent_runtime.execution.providers.model_failure_adapters import (
    ProviderFailureAdapterRegistry,
)
from agent_runtime.observability.attribution import Purpose
from agent_runtime.observability.context_occupancy import (
    ContextOccupancySnapshot,
    GraphScope,
)
from agent_runtime.observability.token_usage import (
    NormalizedTokenUsage,
    TokenUsageExtractorRegistry,
)
from agent_runtime.prompts.assembly import PromptAssemblyPlan
from agent_runtime.prompts.cache_fallback import (
    PromptCacheFallbackContext,
    PromptCacheFallbackHandoff,
)
from agent_runtime.prompts.provider_cache import ProviderCacheFallbackSignal
from agent_runtime.prompts import tool_schema_revision
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


if TYPE_CHECKING:  # pragma: no cover - typing only
    # Deferred at runtime, not merely for tidiness. The occupancy recorder pulls
    # the message classifier, which reads the citation and tool-budget note
    # constants from ``agent_runtime.capabilities`` — a package that is itself
    # mid-import when ``runtime_api.schemas`` reaches this module. Importing it
    # eagerly here closes that loop and breaks service start-up. The one runtime
    # import lives in ``_shared_occupancy_recorder``, by which time every module
    # in the cycle is fully initialized.
    from agent_runtime.observability.context_occupancy_recorder import (
        ContextOccupancyRecorder,
        ContextOccupancySink,
    )


_OCCUPANCY_LOGGER = logging.getLogger(__name__)
# Wall-clock budget for appending one occupancy row, enforced in
# ``_append_occupancy``. The append sits between the provider's answer and the
# response handed back to the graph, so an unbounded await would let a slow or
# contended store add latency to every model call. Two seconds is generous for a
# single-row insert or a JSONL append+fsync and still bounds a stalled disk to a
# blip rather than a systemic slowdown. Occupancy is observability: a breach
# drops the measurement, never the run.
_OCCUPANCY_PERSIST_TIMEOUT_SECONDS: Final[float] = 2.0


@dataclass(frozen=True, slots=True)
class _PendingOccupancyAppend:
    """One measured snapshot plus every fact needed to append it.

    Exists so the append call site reads only plain locals. Resolving a tenant or
    a conversation id in the argument list would put an unguarded attribute read
    on the model-call path, where an ``AttributeError`` fails the run instead of
    dropping a measurement — the §6.4 hole that shipped and was caught by driving
    a real run rather than by any test.
    """

    sink: "ContextOccupancySink"
    snapshot: ContextOccupancySnapshot
    org_id: str
    run_id: str
    conversation_id: str
    model_call_id: str


"""Logger for the Context Occupancy Ledger's guards, and only for them.

The F10 seam itself is deliberately silent: every fact it has is journaled as a
typed record, so a log line would be a second, weaker copy of durable truth.
Occupancy is the exception because its failures are *not* journaled — a dropped
snapshot leaves no record by construction (§6.4) — so a log line is the only
evidence that measurement degraded.
"""


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
    # Context Occupancy Ledger sink (design §5). Optional and defaulted because
    # occupancy is an *observation* lane, not a precondition for dispatching a
    # model call: a deployment that has not wired a store still measures, and
    # simply drops the snapshot. It is deliberately kept off the journal port —
    # occupancy rows have a different lifecycle, a different read pattern, and
    # must never be able to fail an F10 append.
    context_occupancy_store: ContextOccupancySink | None = None
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
        provider: str | None,
        adapters: ProviderFailureAdapterRegistry,
    ) -> None:
        #: ``None`` means "no verified route named the provider" — the default
        #: deployment path, where the only provider hint available is the
        #: LangChain model's ``_llm_type`` and that is the library's name, not
        #: ours. Classification then goes by SDK exception identity instead.
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
            observation = (
                self._adapters.observe(self._provider, error, self._state)
                if self._provider is not None
                else self._adapters.observe_unattributed(error, self._state)
            )
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
    """Inner F10 provider-call seam; RuntimeControl/F2 must run outside it.

    This is also the Context Occupancy Ledger's measurement boundary (design
    §3.1): the one place a fully materialized ``ModelRequest`` exists, after
    every library and middleware contribution has landed. Occupancy is measured
    here rather than at assembly because it is the difference between reporting
    the prompt we intended to send and the one that was sent — and because the
    AST topology gate proves this middleware is installed on the root graph
    *and* on every child, so subagent windows are covered with no new plumbing.

    Occupancy is strictly additive to this seam. It reads the materialized
    request and the observed usage; it never touches ``request_digest``, the
    semantic request, attempt admission, or the records appended, and every
    entry point into it is wrapped in a guard that logs and continues (§6.4).
    """

    name = "0xCopilotModelInvocationMiddleware"

    class _Scopes:
        """The two execution-scope spellings this seam mints and reads."""

        SUPERVISOR: Final[str] = "supervisor"
        SUBAGENT_PREFIX: Final[str] = "subagent:"
        SUPERVISOR_TASK_CALL_ID: Final[str] = "supervisor_task_call_id"

    _SHARED_OCCUPANCY_RECORDER: ClassVar["ContextOccupancyRecorder | None"] = None
    _OCCUPANCY_RECORDER_GUARD: ClassVar[Lock] = Lock()

    def __init__(
        self,
        *,
        occupancy_recorder: "ContextOccupancyRecorder | None" = None,
        retry_policy: ModelCallRetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        random_source: Callable[[], float] | None = None,
    ) -> None:
        """Bind the occupancy recorder and retry policy; all arguments optional.

        The graph funnel constructs this middleware as ``ModelInvocationMiddleware()``
        for the root and passes the *class itself* as a universal child-graph
        factory, and the AST topology gate pins both spellings. A required
        constructor argument would therefore break child-graph construction, not
        merely this call site.

        ``sleep`` and ``random_source`` exist so a test can drive the retry
        schedule on a fake clock. Production never passes them: a unit test that
        actually waited out a 2-second backoff would be a unit test nobody runs.
        """

        super().__init__()
        self._occupancy = occupancy_recorder or self._shared_occupancy_recorder()
        self._retry_policy = retry_policy or ModelCallRetryPolicy()
        self._sleep = sleep or asyncio.sleep
        self._random = random_source or random.random

    @classmethod
    def _shared_occupancy_recorder(cls) -> "ContextOccupancyRecorder | None":
        """Return the process-wide recorder, constructing it on first use.

        Shared rather than per-instance because a fresh middleware is built for
        the root graph and for every child graph, and the recorder holds the two
        caches that make §3.4's cost model work: the digest-keyed token cache and
        the one-time ``deepagents`` constant sweep. Rebuilding them per graph
        would pay the memoization cost without ever collecting the benefit.

        ``None`` when the recorder cannot be built, and the guard is the whole
        point. This runs from ``__init__``, and ``__init__`` runs at harness
        construction — ``factory._build_harness`` both instantiates this class
        and hands it to ``build_deep_agent`` as a universal child-graph factory,
        inside a ``try`` that converts *any* exception into
        ``AgentRuntimeError(RUNTIME_FACTORY_ERROR)``. So an unguarded failure
        here does not degrade measurement, it fails the run with "The agent
        runtime could not be constructed", which is precisely what §6.4 forbids.
        The failure is not hypothetical: the import below is deferred *because*
        there is a live import cycle through ``agent_runtime.capabilities``
        (see the module's ``TYPE_CHECKING`` block), and a cycle re-entered from a
        new call order raises ``ImportError`` rather than returning a module.

        A ``None`` recorder is the correct degradation and not a special case
        downstream: capture and persist both check for it, and the seam then
        behaves exactly as a deployment with no occupancy store wired.
        """

        try:
            from agent_runtime.observability.context_occupancy_recorder import (  # noqa: PLC0415 — break import cycle
                ContextOccupancyRecorder,
            )

            with cls._OCCUPANCY_RECORDER_GUARD:
                if cls._SHARED_OCCUPANCY_RECORDER is None:
                    cls._SHARED_OCCUPANCY_RECORDER = ContextOccupancyRecorder()
                return cls._SHARED_OCCUPANCY_RECORDER
        except Exception:  # noqa: BLE001 — measurement never fails a run (§6.4)
            _OCCUPANCY_LOGGER.warning(
                "Could not construct the context occupancy recorder; this "
                "process will dispatch model calls without measuring occupancy.",
                exc_info=True,
            )
            return None

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
            return await self._awrap_occupancy_only(request, handler)
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
        # Read once per model call rather than per attempt: the F2 plan is a
        # property of the assembled prompt, and every attempt of one call is
        # dispatched against the same assembly. Guarded because occupancy may
        # never influence dispatch.
        occupancy_plan = self._occupancy_assembly_plan()

        # Backoff owed to the *next* attempt, spent at the top of the loop
        # rather than at the failure site. The failure site sits inside a
        # ``except BaseException as persistence_error`` whose handler rewrites
        # anything raised under it into ``raise error from persistence_error``,
        # so awaiting there would report a run cancelled during a backoff as the
        # provider's rate-limit error instead of as a cancellation.
        pending_retry_backoff = 0.0

        while True:
            if pending_retry_backoff > 0:
                await self._sleep(pending_retry_backoff)
                pending_retry_backoff = 0.0
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
            # Measured per attempt, never per call: a retry re-materializes the
            # request against a different window state, so it earns its own
            # snapshot under its own ordinal rather than overwriting the first
            # (design §6.3). Captured from ``attempt_request`` — the exact
            # payload this attempt dispatches, after routing.
            occupancy = self._capture_occupancy(
                request=attempt_request,
                identity=identity,
                attempt_ordinal=len(prior) + 1,
                route=route,
                plan=occupancy_plan,
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
                    # A failed attempt still occupied a window, and its snapshot
                    # is what makes "two attempts, two snapshots" true (§6.3).
                    # Strictly *after* the failure record, never before it: the
                    # occupancy sink is an external store with no timeout, and
                    # sequencing it ahead of the journal append would let a
                    # degraded observability store delay the record that drives
                    # retry admission and terminal failure. Occupancy is
                    # subordinate to the journal on this path exactly as it is on
                    # the success path below. Inside this ``try`` so a
                    # ``BaseException`` escaping a persistence call is handled
                    # exactly as one escaping the journal append is — the
                    # occupancy write must not introduce a second,
                    # differently-shaped failure mode on the seam's most delicate
                    # path.
                    await self._persist_occupancy(
                        binding=binding,
                        control=control,
                        identity=identity,
                        snapshot=occupancy,
                        observer=observer,
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
                    # Admission decided *whether*; the policy decides *how long*.
                    # Without this wait the loop re-dispatched instantly, so a
                    # 429 was answered by an identical request microseconds
                    # later — the retry that is guaranteed to be rate-limited
                    # again. Computed after the DENY branch so a call that is
                    # not going to be retried never accrues a backoff, and
                    # *spent* at the top of the loop (see above) so the wait is
                    # not inside this handler's exception rewrite.
                    # ``_can_retry`` above already proved the class is one this
                    # policy admits, so only the pacing is taken from it.
                    pending_retry_backoff = self._retry_pacing_seconds(
                        attempt=len(prior),
                        error=error,
                        now=binding.now(),
                    )
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
            # After the usage-bearing records, so the occupancy row reconciles
            # against exactly the ``NormalizedTokenUsage`` the usage lane just
            # recorded (§6.1) — read-side denormalization, never a second meter.
            await self._persist_occupancy(
                binding=binding,
                control=control,
                identity=identity,
                snapshot=occupancy,
                observer=observer,
            )
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

    @classmethod
    def _execution_scope(cls, runtime: object) -> str:
        config = getattr(runtime, "config", None)
        if not isinstance(config, Mapping):
            return cls._Scopes.SUPERVISOR
        for container_name in ("metadata", "configurable"):
            container = config.get(container_name)
            if not isinstance(container, Mapping):
                continue
            task_id = container.get(cls._Scopes.SUPERVISOR_TASK_CALL_ID)
            if isinstance(task_id, str) and task_id.strip():
                return f"{cls._Scopes.SUBAGENT_PREFIX}{task_id.strip()}"
        return cls._Scopes.SUPERVISOR

    # --- context occupancy (design §3.1, §6.2-§6.5) --------------------------

    @classmethod
    def _graph_scope(cls, execution_scope: str) -> GraphScope:
        """Project the F10 execution scope onto the occupancy graph scope (§6.2).

        A subagent runs against its **own** context window, so a snapshot has to
        say which window it describes; summing a child's occupancy into its
        parent is not a rounding error but a category error that reports >100%
        utilization on any run that delegates. The projection is deliberately
        lossy in one direction only — occupancy needs "root or child", not
        *which* child, and the child's identity is already carried by the
        attribution context's ``task_id`` / ``subagent_slug``.
        """

        return (
            GraphScope.SUBAGENT
            if execution_scope.startswith(cls._Scopes.SUBAGENT_PREFIX)
            else GraphScope.ROOT
        )

    @classmethod
    def _occupancy_assembly_plan(cls) -> PromptAssemblyPlan | None:
        """Return the plan that decomposes this call's system block (§3.2).

        Two sources, in strict preference order, because the system prompt is
        assembled **twice** on two different rollout postures and only the first
        of them used to be readable here:

        1. The **per-call** F2 plan, published on the handoff
           ``RuntimeToolControlMiddleware`` binds around the handler this
           middleware runs inside. It describes the exact request being sent,
           including the per-turn fragments a re-assembly adds, so it wins
           whenever it exists. The read is strictly non-mutating: it touches
           ``result.plan`` and nothing else, and in particular never calls
           ``consume_rejection``, so the handoff's one-shot cache-fallback
           permit is untouched and F2's retry semantics are exactly what they
           were.
        2. The **build-time** plan the factory assembled to produce the graph's
           ``system_prompt``. ``PromptRuntimeBinding.prepare`` returns
           ``plan=None`` whenever F2's mode is ``OFF``, which is the shipped
           default, so on an ordinary deployment source 1 is always empty. The
           bytes it would have described are still there — they are the prompt
           the graph was built with — and the typed decomposition of them is
           still there too, held by the binding's fragment provider. This is the
           whole reason the ledger reported one anonymous 4,853-token
           ``UNDECLARED`` span covering 58% of a real run's measured input: not
           a contributor that failed to declare itself, but a declaration that
           had landed and was never wired to the seam that reads declarations.

        Preferring the per-call plan matters on the paths where the two differ.
        Under ``ENFORCE`` the request carries the re-assembled prompt, and the
        build-time plan would describe a prefix of it at best; taking source 1
        first keeps the measurement matched to the request rather than to the
        graph.

        Falling back cannot misattribute. ``SystemBlockAttributor`` verifies
        every located fragment against its ``content_digest`` before labelling a
        byte range, so a plan that no longer describes the system message —
        a subagent with its own prompt, a decorated or re-rendered block —
        attributes nothing and the bytes stay exactly as unexplained as they
        were. The failure mode of a stale fallback is "no better than before",
        never "confidently wrong".

        ``None`` when neither source can answer, which means to the measurement
        what it always meant: the system block is attributed by the third-party
        adapter and otherwise recorded as unexplained.
        """

        try:
            handoff = PromptCacheFallbackContext.current()
            plan = None if handoff is None else handoff.result.plan
            return plan if plan is not None else cls._build_time_assembly_plan()
        except Exception:  # noqa: BLE001 — measurement never fails a run (§6.4)
            _OCCUPANCY_LOGGER.debug(
                "Could not read the F2 assembly plan for occupancy measurement; "
                "the system block will measure as unattributed.",
                exc_info=True,
            )
            return None

    @staticmethod
    def _build_time_assembly_plan() -> PromptAssemblyPlan | None:
        """Return the run-scoped plan the graph's system prompt was rendered from.

        Read off the prompt runtime binding rather than off a slot of its own.
        The binding is installed once per harness build, is inherited by every
        local child task through the same ContextVar, and already owns the
        fragment provider that holds the plan — so there is exactly one
        run-lifetime object to bind, unbind and reason about instead of two that
        could disagree about which build a measurement belongs to.

        Note what is *not* gated on here: F2's mode. The binding exists whenever
        run control is bound, and its ``mode`` decides whether the prompt is
        re-assembled per call, not whether one was assembled at build time. A
        mode check here would re-introduce the exact coupling that made the
        ledger inert — an observability read inheriting a feature's rollout
        posture for no reason of its own.
        """

        binding = RunControlContext.prompt_runtime()
        return None if binding is None else binding.build_time_plan()

    def _capture_occupancy(
        self,
        *,
        request: ModelRequest[Any],
        identity: RuntimeModelCallIdentity,
        attempt_ordinal: int,
        route: ModelRouteEntry,
        plan: PromptAssemblyPlan | None,
    ) -> ContextOccupancySnapshot | None:
        """Measure this attempt's materialized request, or return ``None``.

        The recorder is already total, so this second guard exists for the one
        thing the recorder cannot promise: that *it* is the object it claims to
        be. A caller may inject a recorder, and a middleware seam that trusted
        an injected collaborator not to raise would have moved the fail-open
        contract outside the code that owns it.

        ``context_window_tokens`` comes from the resolved route's
        ``max_input_tokens`` — the deployment descriptor's own declared input
        window, from the same catalog lane the pricing source reads. Taking it
        here rather than re-looking-up a pricing row keeps the denominator
        consistent with the route the attempt actually used, including on an
        alternate-route retry to a differently-sized deployment.

        Capture runs wherever the F10 binding is installed and a recorder was
        built (see :meth:`_shared_occupancy_recorder`), while *persistence* is
        additionally gated on a wired store. That asymmetry is deliberate: the
        snapshot is the input to both the durable row (§5) and the streamed
        ``context_occupancy`` event (§7), so gating the measurement on a store
        would make a streaming surface depend on persistence being configured.
        The cost of measuring is bounded by §3.4's digest memoization — a
        resident prompt and tool surface is tokenized once per process — and by
        the fact that nothing here can fail a run.
        """

        if self._occupancy is None:
            return None
        try:
            return self._occupancy.capture(
                request,
                identity=identity,
                attempt_ordinal=attempt_ordinal,
                graph_scope=self._graph_scope(identity.execution_scope),
                provider=route.provider,
                model_family=route.model_name,
                context_window_tokens=route.max_input_tokens,
                plan=plan,
            )
        except Exception:  # noqa: BLE001 — measurement never fails a run (§6.4)
            _OCCUPANCY_LOGGER.warning(
                "Context occupancy capture failed for model call %s; "
                "continuing without a snapshot.",
                identity.model_call_id,
                exc_info=True,
            )
            return None

    async def _awrap_occupancy_only(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Measure occupancy on the path where F10 is not installed.

        This is the default deployment. ``FeatureModeSet.f10`` ships ``OFF``, so
        ``ModelInvocationComposer.compose`` returns ``None`` and no F10 binding
        exists — and the ledger has no business depending on that. What occupancy
        actually needs is the materialized request, which is present on every
        call, and a sink, which the run handler installs unconditionally.

        Deliberately **not** a partial F10 binding. Everything else the F10 path
        does — the journal, the authority checks, admission, alternate-route
        retry — reads a binding it is entitled to assume is complete, so handing
        those code paths a half-populated one to get an observability side effect
        would trade a measurement gap for a correctness hazard. This is a
        separate, much smaller path: measure, call the handler, append.

        Two honest limits. There is no ``_ProviderLifecycleCallback`` here, so
        the snapshot carries no ``provider_input_tokens`` and no cache figures —
        it is estimate-only, and ``unattributed_delta`` stays 0 rather than
        pretending to reconcile against a total nobody reported. And the route
        facts come from the request's own model object rather than a resolved
        deployment descriptor, so ``context_window_tokens`` is ``None`` and
        ``free_tokens`` with it. Segment attribution — the reason the ledger
        exists — is unaffected.

        The handler is called at least once whatever happens above it, and more
        than once only when :class:`ModelCallRetryPolicy` admits a re-dispatch —
        never because measurement failed. Capture sits in its own guard so a
        measurement failure cannot become a failed model call (§6.4).

        This is also where the runtime's per-model-call retry policy applies,
        and the placement is the whole point. Retrying *here* costs one provider
        round trip; the alternative that exists today is the run-claim retry at
        ``runtime_worker/loop.py``, which restarts the turn and re-pays for every
        tool call that already succeeded. The provider hiccup that motivates a
        retry — a 429, a 503, a socket closed before the first token — is exactly
        the failure that does not need the turn thrown away.
        """

        pending = self._plan_occupancy_only(request)

        response = await self._dispatch_with_retry(request, handler)

        # Every argument below is a plain local resolved inside the guarded
        # planner above. That is deliberate rather than stylistic: computing an
        # argument at the call site would put an unguarded attribute read on the
        # model-call path, and an AttributeError there fails the run — which is
        # exactly how the first draft of this method broke a real run before it
        # ever reached a test.
        if pending is not None:
            await self._append_occupancy(
                sink=pending.sink,
                snapshot=pending.snapshot,
                usage=None,
                org_id=pending.org_id,
                run_id=pending.run_id,
                conversation_id=pending.conversation_id,
                model_call_id=pending.model_call_id,
            )
        return response

    async def _dispatch_with_retry(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Dispatch one model call under the runtime-owned retry policy.

        Classification is not re-implemented here. A ``_ProviderLifecycleCallback``
        is attached to the request's model exactly as the F10 path attaches one,
        so the failure reaches :class:`ProviderFailureClassifier` as
        adapter-attested facts — the SDK exception class and its numeric status —
        rather than as a string this method pattern-matched.

        Each attempt is dispatched through ``handler`` unchanged, so the model
        client's own ``timeout`` still bounds **one attempt**. The backoff is
        spent between attempts and is deliberately outside that bound: a policy
        where the timeout covered the whole sequence would shrink the last
        attempt's budget to whatever the earlier waits left over.

        The last provider exception is re-raised untouched when retries are
        exhausted, so the runtime's existing typed-error taxonomy decides what
        the user sees. This method never invents an error of its own.
        """

        attempt = 0
        while True:
            attempt += 1
            observer = _ProviderLifecycleCallback(
                # No verified route here, so the provider is deliberately not
                # named: ``_request_provider`` yields LangChain's ``_llm_type``
                # ("openai-chat"), which matches no adapter key and would
                # classify every provider failure as UNKNOWN — i.e. never
                # retry. The exception's own SDK identity is authoritative.
                provider=None,
                adapters=ProviderFailureAdapterRegistry.defaults(),
            )
            observer.dispatch_started()
            attempt_request = request.override(
                model=self._attach_callback(request.model, observer)
            )
            try:
                return await handler(attempt_request)
            except BaseException as error:
                observer.observe_error(error)
                state = observer.state
                failure = ProviderFailureClassifier().classify(
                    state.failure_observation()
                )
                decision = self._retry_decision(
                    failure=failure,
                    lifecycle=state,
                    attempt=attempt,
                    error=error,
                )
                self._log_retry(
                    request=request,
                    failure=failure,
                    attempt=attempt,
                    decision=decision,
                )
                if not decision.should_retry:
                    raise
                await self._sleep(decision.delay_seconds)

    def _retry_pacing_seconds(
        self,
        *,
        attempt: int,
        error: BaseException,
        now: datetime,
    ) -> float:
        """Backoff for an F10 retry that admission has already authorized.

        Only the *pacing* half of the policy is used on this path.
        ``ModelInvocationBudget`` remains the attempt authority when the journal
        is installed, so consulting ``max_attempts`` here would put a second
        ceiling on a decision that already has one — and the two would drift.
        """

        try:
            return self._retry_policy.delay_seconds(
                attempt=max(attempt, 1),
                hint=provider_retry_hint(error, now=now),
                random_value=self._random(),
            )
        except Exception:  # noqa: BLE001 — pacing never fails a model call
            _OCCUPANCY_LOGGER.warning(
                "Model-call retry pacing failed; retrying without backoff.",
                exc_info=True,
            )
            return 0.0

    def _retry_decision(
        self,
        *,
        failure: ModelFailureClass,
        lifecycle: ProviderAttemptLifecycle,
        attempt: int,
        error: BaseException,
    ) -> ModelRetryDecision:
        """Ask the policy, converting any policy failure into "do not retry".

        Guarded for the same reason occupancy is: this runs on the model-call
        path, and a malformed provider header must not be able to turn a
        recoverable 429 into an ``AttributeError`` that fails the run outright.
        """

        try:
            return self._retry_policy.decide(
                failure=failure,
                lifecycle=lifecycle,
                attempt=attempt,
                error=error,
                now=datetime.now(timezone.utc),
                random_value=self._random(),
            )
        except Exception:  # noqa: BLE001 — pacing never fails a model call
            _OCCUPANCY_LOGGER.warning(
                "Model-call retry policy failed to decide; not retrying.",
                exc_info=True,
            )
            return ModelRetryDecision(should_retry=False)

    def _log_retry(
        self,
        *,
        request: ModelRequest[Any],
        failure: ModelFailureClass,
        attempt: int,
        decision: ModelRetryDecision,
    ) -> None:
        """Emit the ``model_call_retry`` structured event.

        Same shape and channel as ``RetryingTool``'s ``tool_retry`` — a logger
        name plus a ``metadata`` mapping of low-cardinality, body-free facts. No
        provider message, no prompt, no response: the failure *class* is the
        thing worth alerting on, and it is already provider-neutral.

        Emitted on the give-up path too, not only before a wait. A retry budget
        that is being exhausted every turn is the signal that matters most, and
        it is invisible if only successful retries are logged.
        """

        _OCCUPANCY_LOGGER.info(
            "model_call_retry",
            extra={
                "metadata": {
                    "provider": self._request_provider(request),
                    "model_family": self._request_model_family(request),
                    "attempt": attempt,
                    "max_attempts": self._retry_policy.max_attempts,
                    "failure_class": failure.value,
                    "will_retry": decision.should_retry,
                    "delay_seconds": round(decision.delay_seconds, 3),
                    "provider_directed": decision.provider_directed,
                }
            },
        )

    def _plan_occupancy_only(
        self, request: ModelRequest[Any]
    ) -> "_PendingOccupancyAppend | None":
        """Resolve everything the non-F10 append needs, under one guard.

        Returns ``None`` when occupancy cannot or should not be recorded — no
        recorder, no installed sink, no bound run control, or any failure while
        measuring. The caller then simply skips the append.
        """

        if self._occupancy is None:
            return None
        installed = RunControlContext.context_occupancy_store()
        if installed is None:
            return None
        sink, org_id = installed
        try:
            control = RunControlContext.current()
            identity = RuntimeModelCallIdentity.from_current(
                execution_scope=self._execution_scope(request.runtime),
                model_turn=max(self._model_turn(request.state), 1),
            )
            if control is None or identity is None:
                return None
            snapshot = self._occupancy.capture(
                request,
                identity=identity,
                attempt_ordinal=1,
                graph_scope=self._graph_scope(identity.execution_scope),
                provider=self._request_provider(request),
                model_family=self._request_model_family(request),
                context_window_tokens=None,
                plan=self._occupancy_assembly_plan(),
            )
            return _PendingOccupancyAppend(
                sink=sink,
                snapshot=snapshot,
                org_id=org_id,
                run_id=identity.run_id,
                conversation_id=control.snapshot.conversation_id,
                model_call_id=identity.model_call_id,
            )
        except Exception:  # noqa: BLE001 — measurement never fails a run (§6.4)
            _OCCUPANCY_LOGGER.warning(
                "Context occupancy capture failed outside F10; "
                "continuing without a snapshot.",
                exc_info=True,
            )
            return None

    @staticmethod
    def _request_provider(request: ModelRequest[Any]) -> str:
        """Provider slug from the request's own model, for the non-F10 path.

        Mirrors ``canonical_model_request_digest``'s derivation so the two agree
        about what provider a call belongs to.
        """

        model = request.model
        return str(getattr(model, "_llm_type", type(model).__name__)).strip().lower()

    @staticmethod
    def _request_model_family(request: ModelRequest[Any]) -> str:
        """Model name from the request's own model, for the non-F10 path."""

        model = request.model
        for attribute in ("model_name", "model", "deployment_name"):
            value = getattr(model, attribute, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return type(model).__name__

    async def _append_occupancy(
        self,
        *,
        sink: "ContextOccupancySink | None",
        snapshot: ContextOccupancySnapshot,
        usage: NormalizedTokenUsage | None,
        org_id: str,
        run_id: str,
        conversation_id: str,
        model_call_id: str,
    ) -> None:
        """Reconcile and append one snapshot under a bounded timeout.

        The timeout is the point. ``§6.4``'s fail-open guard absorbs a store that
        *raises*; it does nothing about a store that is merely **slow**, and this
        await sits between the provider's answer and the response returned to the
        graph. On the file-native store — the desktop default — an append is a
        write plus ``flush`` plus ``fsync`` under the global store lock, so a
        contended or stalled disk would add that latency to every model call in
        the process. An observability ledger is never worth a slow run, so a
        breach of the budget is logged and the measurement dropped.

        Not fire-and-forget: a detached task would routinely lose the last
        snapshot of a run to worker teardown, and would need its own strong
        reference set to avoid being garbage-collected mid-write. A short bound
        keeps the row in the common case and caps the pathological one.
        """

        if sink is None or self._occupancy is None:
            return
        try:
            await asyncio.wait_for(
                self._occupancy.persist(
                    self._occupancy.finalize(snapshot, usage),
                    sink=sink,
                    org_id=org_id,
                    run_id=run_id,
                    conversation_id=conversation_id,
                ),
                timeout=_OCCUPANCY_PERSIST_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            _OCCUPANCY_LOGGER.warning(
                "Context occupancy persistence exceeded %.1fs for model call %s; "
                "dropping the measurement rather than delaying the run.",
                _OCCUPANCY_PERSIST_TIMEOUT_SECONDS,
                model_call_id,
            )
        except Exception:  # noqa: BLE001 — a dropped snapshot is the failure mode
            _OCCUPANCY_LOGGER.warning(
                "Context occupancy persistence failed for model call %s; "
                "dropping the measurement.",
                model_call_id,
                exc_info=True,
            )

    async def _persist_occupancy(
        self,
        *,
        binding: ModelInvocationRuntimeBinding,
        control: RunControlBinding,
        identity: RuntimeModelCallIdentity,
        snapshot: ContextOccupancySnapshot | None,
        observer: _ProviderLifecycleCallback,
    ) -> None:
        """Reconcile and append one attempt's snapshot; never raises an ``Exception``.

        The precision matters, because "never raises" is the claim that lets
        callers stop checking. A ``BaseException`` — a task cancellation, an
        interpreter shutdown — deliberately still propagates: swallowing a
        cancellation to finish an observability write would keep a torn-down run
        alive, which is worse than losing the snapshot. Every failure mode this
        seam is actually meant to absorb (a store that is down, an unprojectable
        snapshot, a raising adapter) is an ``Exception`` and is absorbed here.

        ``usage`` is passed as ``None`` when the provider reported nothing,
        which is *not* the same as a zero-token usage object: the former leaves
        ``provider_input_tokens`` unset and ``unattributed_delta`` at zero,
        while the latter would claim the provider billed nothing and turn the
        whole estimate into a large negative residual on every unreported call.
        """

        if (
            snapshot is None
            or self._occupancy is None
            or binding.context_occupancy_store is None
        ):
            return
        usage, reported = observer.usage
        await self._append_occupancy(
            sink=binding.context_occupancy_store,
            snapshot=snapshot,
            usage=usage if reported else None,
            org_id=binding.org_id,
            run_id=identity.run_id,
            conversation_id=control.snapshot.conversation_id,
            model_call_id=identity.model_call_id,
        )

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
