"""Queued run command handling."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
import asyncio
import inspect
import logging
import time
from datetime import datetime, timezone

from agent_runtime.api.presentation_templates import _ErrorMessage
from agent_runtime.budgets import (
    BudgetCharger,
    BudgetEnforcer,
    BudgetEstimate,
    BudgetEstimator,
    BudgetPreflightAllow,
    BudgetPreflightDeny,
    BudgetPreflightWarn,
    CharHeuristicTokenCounter,
    LitellmTokenCounter,
    TokenCounterPort,
)
from agent_runtime.api.mcp_discovery_service import McpDiscoveryService
from agent_runtime.capabilities.citation_resolver import CitationResolver
from agent_runtime.capabilities.citations import CitationLedger
from agent_runtime.capabilities.conversation_ordinals import (
    ConversationOrdinalAllocator,
)
from agent_runtime.capabilities.surfaces import (
    ShapingCredentials,
    SurfaceGenerationScheduler,
    SurfaceSpecStorePort,
    build_surface_generation_scheduler,
    build_surface_spec_store,
)
from agent_runtime.capabilities.surfaces.shape_request import (
    ReadPathShaper,
    build_read_path_shaper,
)
from agent_runtime.capabilities.tool_budget_guard import ToolBudgetGuard
from agent_runtime.capabilities.tool_budget_middleware import (
    ToolBudgetMiddleware,
    WorkspaceToolBudgetOverride,
)
from agent_runtime.control_plane.context import TaskPolicyRuntimeBinding
from agent_runtime.surfaces_v2.emitter import WorkLedgerEmitter
from runtime_worker.handlers.receipt_hook import emit_receipt_if_enabled
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
    RuntimeErrorEnvelope,
    StreamEventSource,
)
from agent_runtime.execution.tool_outcomes import (
    ToolErrorCode,
    ToolInvocationOutcome,
    ToolOutcome,
)
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_termination import (
    RunTerminationCoordinator,
    TerminalRunObserverPort,
    TerminationReason,
)
from agent_runtime.prompts.observation import (
    PromptAssemblyObserver,
    PromptObservationStorePort,
)
from agent_runtime.api.presentation import (
    ToolDisplayLookup,
    ToolDisplayLookupContext,
)
from agent_runtime.api.user_policies_resolver import (
    ProviderKeysHydrator,
    UserPoliciesResolver,
)
from agent_runtime.execution.model_invocation.journal import ModelInvocationStorePort
from agent_runtime.capabilities.mcp.annotations import (
    McpToolAnnotations,
    McpToolAnnotationsRegistry,
)
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationEventEmitterAdapter,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.mcp.gateway_context import (
    McpOperationGatewayContext,
    McpOperationGatewayServices,
)
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.operations.probes import OperationShadowProbe
from agent_runtime.capabilities.operations.catalog import DEFAULT_OPERATION_DESCRIPTORS
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.operations.presentation import (
    SurfaceLedgerOperationOutcomePresenter,
)
from agent_runtime.rollout import RolloutCapability, RolloutMode
from agent_runtime.rollout_admission import (
    E2RolloutAdmission,
    PersistedRunCohortFactsProvider,
)
from agent_runtime.rollout_shadow import (
    ShadowComparisonContext,
    ShadowComparisonService,
)
from agent_runtime.rollout_shadow_adapters import ShadowRunProjectionObserver
from agent_runtime.capabilities.tools.builtin.publish_artifact import (
    ArtifactContentPartPublisher,
    PublishArtifactTool,
)
from agent_runtime.capabilities.tools.builtin.revise_artifact import (
    ReviseArtifactTool,
)
from agent_runtime.capabilities.mcp.descriptor_registry import (
    McpDisplayRegistryContext,
)
from agent_runtime.capabilities.tools.tool_use_enforcement import (
    ToolUsePolicyResolver,
)
from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate
from agent_runtime.persistence.ports import (
    CitationStorePort,
    ConversationToolOrdinalStorePort,
    DraftStorePort,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.tool_errors import (
    AuthDenied,
    BudgetExceeded,
    RunFatalToolError,
    TenantIsolationViolation,
)
from agent_runtime.execution.factory import (
    RuntimeHarness,
    acreate_agent_runtime,
)
from agent_runtime.execution.providers.citation_pipeline import CitationStreamPipeline
from agent_runtime.execution.runtime import ainvoke_runtime, astream_runtime
from agent_runtime.persistence import with_optimistic_retry
from agent_runtime.persistence.records import BudgetReservationRecord, ToolBudgetRecord
from agent_runtime.observability.attribution import Purpose
from agent_runtime.observability.usage_meter import (
    MeteredModelInvocation,
    UsageMeter,
)
from agent_runtime.observability.usage_recorder import (
    PostgresUsageRecorder,
    UsageRecorder,
)
from agent_runtime.pricing import ModelPricingCatalog
from agent_runtime.settings import RuntimeSettings
from runtime_api.schemas import (
    AgentRunStatus,
    MessageRecord,
    MessageRole,
    RunRecord,
    RuntimeApiEventType,
    RuntimeRunCommand,
)
from runtime_worker.audit import WorkerAuditEmitter
from runtime_worker.turn_content import AssistantTurnContent
from runtime_worker.dependencies import (
    DefaultRuntimeDependenciesFactory,
)
from runtime_worker.agent_scratch_wiring import AgentScratchWorkerWiring
from runtime_worker.file_store_wiring import FileStoreWorkerWiring
from runtime_worker.workspace_backend_wiring import WorkspaceBackendWorkerWiring
from runtime_worker.run_metrics import AssistantRunMetrics
from runtime_worker.run_control import (
    RunControlContext,
    RunControlPlaneBuilder,
)
from runtime_worker.model_invocation_composition import (
    ModelInvocationEffectTracker,
    ModelInvocationWorkerComposer,
)
from runtime_worker.model_invocation_terminal import ModelInvocationTerminalIntegration
from runtime_worker.stream_events import StreamOrchestrator
from runtime_worker.stream_messages import StreamTextHelper
from runtime_worker.streaming_executor import StreamingExecutor
from runtime_worker.mcp_operation_storage import (
    McpOperationGatewayComposer,
)
from agent_runtime.context.memory.subagent_trace import SubagentArtifactsBackend
from runtime_worker.tool_observations import (
    PriorToolResultLoader,
    ToolObservationIndex,
    ToolObservationIndexBuilder,
)

RuntimeDependenciesFactory = Callable[[AgentRuntimeContext], RuntimeDependencies]
# Async by default (``acreate_agent_runtime``) so registry-listing HTTP calls
# don't block the event loop; sync fakes still work via ``inspect.isawaitable``.
AgentFactory = Callable[..., RuntimeHarness | Awaitable[RuntimeHarness]]
RuntimeInvoker = Callable[[RuntimeHarness, Sequence[object]], object]
RuntimeStreamer = Callable[[RuntimeHarness, Sequence[object]], AsyncIterator[object]]
MAX_STRUCTURED_CONTEXT_CHARS = 4_000


class RuntimeRunHandler:
    """Execute a queued runtime run command asynchronously."""

    action_interrupt_events = frozenset(
        {
            RuntimeApiEventType.APPROVAL_REQUESTED,
            RuntimeApiEventType.MCP_AUTH_REQUIRED,
        }
    )

    class _Fields:
        ROLE = "role"
        CONTENT = "content"
        FINAL_RESPONSE = "final_response"
        RESPONSE = "response"
        OUTPUT = "output"
        MESSAGES = "messages"
        TEXT = "text"
        FILENAME = "filename"
        NAME = "name"
        ID = "id"
        CONTENT_TYPE = "content_type"
        MIME_TYPE = "mime_type"
        SIZE = "size"
        FILE_ID = "file_id"
        URL = "url"
        TYPE = "type"
        ACTION_REQUIRED = "action_required"
        APPROVAL_REQUESTED = "approval_requested"
        INTERRUPTS = "interrupts"
        STATUS = "status"
        DELTA = "delta"
        MESSAGE = "message"
        BRANCH = "branch"
        REGENERATE_FROM_MESSAGE_ID = "regenerate_from_message_id"
        REPLACE_FROM_MESSAGE_ID = "replace_from_message_id"
        BRANCH_ID = "branch_id"
        SOURCE_MESSAGE_ID = "source_message_id"
        PARENT_MESSAGE_ID = "parent_message_id"
        SEQUENCE_NO = "sequence_no"
        EVENT_TYPE = "event_type"
        PAYLOAD = "payload"

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        dependencies_factory: RuntimeDependenciesFactory | None = None,
        settings: RuntimeSettings | None = None,
        agent_factory: AgentFactory = acreate_agent_runtime,
        runtime_invoker: RuntimeInvoker = ainvoke_runtime,
        runtime_streamer: RuntimeStreamer = astream_runtime,
        on_event_appended: Callable[[str], None] | None = None,
        citation_store: CitationStorePort | None = None,
        draft_store: DraftStorePort | None = None,
        conversation_tool_ordinal_store: (
            ConversationToolOrdinalStorePort | None
        ) = None,
        usage_recorder: UsageRecorder | None = None,
        mcp_discovery_cache: object | None = None,
        user_policies_resolver: UserPoliciesResolver | None = None,
        token_counter: TokenCounterPort | None = None,
        queue: object | None = None,
        artifact_service: object | None = None,
        artifact_blob_store: object | None = None,
        artifact_reference_store: object | None = None,
        workspace_host_sessions: object | None = None,
        workspace_overlay_store: object | None = None,
        sandbox_patch_collector: object | None = None,
        sandbox_provider_overrides: Mapping[object, object] | None = None,
        capability_env: Mapping[str, str] | None = None,
        workspace_broker_http_client: object | None = None,
        run_control_builder: RunControlPlaneBuilder | None = None,
        prompt_observation_store: PromptObservationStorePort | None = None,
        run_control_decision_store: object | None = None,
        model_invocation_store: ModelInvocationStorePort | None = None,
        model_invocation_composer: ModelInvocationWorkerComposer | None = None,
        model_invocation_terminal: ModelInvocationTerminalIntegration | None = None,
        terminal_run_observer: TerminalRunObserverPort | None = None,
    ) -> None:
        self.persistence: PersistencePort = persistence
        self.event_store: EventStorePort = event_store
        # The turn's ordered parts, folded from this run's own ledger at seal
        # time. Shared with the approval handler's terminal path so there is one
        # projection rule, not one per completion route.
        self._turn_content = AssistantTurnContent(self.event_store)
        # One explicit desktop writer/admission composition per handler. The
        # pre-model wrapper and durable event projector must share this object;
        # constructing the gate ad hoc would create two unrelated decisions.
        self._file_store_worker_wiring = FileStoreWorkerWiring(self.event_store)
        # PRD-D3 — the durable command queue, threaded from the worker loop so the
        # per-run ``stage_rowset_write`` tool can enqueue an allow-always auto-apply
        # (FR-C8). ``None`` (unwired / minimal test handler) ⇒ the stager's
        # ``commit_queue`` is ``None`` and nothing auto-applies.
        self._queue = queue
        # B1 publication uses the A2-composed service directly through the
        # OperationContext. ``None`` on the dark path keeps the tool absent.
        self.artifact_service = artifact_service
        self._artifact_blob_store = artifact_blob_store
        self._artifact_reference_store = artifact_reference_store
        self._workspace_host_sessions = workspace_host_sessions
        self._workspace_overlay_store = workspace_overlay_store
        # An injected collector is a test/extension seam. The file-native D3
        # composition otherwise constructs its own A2-backed collector; it
        # still accepts no C1 importer and never applies a patch at completion.
        self._sandbox_patch_collector = sandbox_patch_collector
        self._sandbox_provider_overrides = sandbox_provider_overrides
        self._capability_env = capability_env
        # Test/extension seam for the desktop capability broker. ``None`` — the
        # only value production ever supplies — leaves the wiring on the
        # process-shared loopback pool exactly as before. It exists because the
        # broker lanes (the ``/workspace/`` route and the attached-folder roots)
        # were otherwise unreachable from a handler-level test, which is how the
        # ENFORCE lane shipped with grants that did nothing.
        self._workspace_broker_http_client = workspace_broker_http_client
        self._run_control_builder = run_control_builder
        self._prompt_observation_store = prompt_observation_store
        self.settings = settings or RuntimeSettings.load()
        self._model_invocation_composer = (
            model_invocation_composer
            or ModelInvocationWorkerComposer(
                settings=self.settings,
                persistence=self.persistence,
                event_store=self.event_store,
                journal=model_invocation_store,
            )
        )
        # The sole runtime gate for explicitly enabled E2 lanes. It is
        # deliberately run-scoped: every capability receives persisted
        # server-owned identity facts before it can appear in dependencies.
        self._e2_rollout_admission = E2RolloutAdmission(
            resolution=self.settings.execution.rollout,
            cohorts=self.settings.execution.rollout_cohorts,
            kill_switches=self.settings.execution.rollout_kill_switches,
        )
        # BYOK re-hydration: queue commands round-trip through JSON, which
        # drops the serialization-excluded ``AgentRuntimeContext.provider_keys``
        # field. When a resolver is wired, the handler re-fetches the policy
        # snapshot at claim time and re-attaches the keys in memory only.
        self._provider_keys_hydrator = (
            ProviderKeysHydrator(resolver=user_policies_resolver)
            if user_policies_resolver is not None
            else None
        )
        # When the caller supplies a ``dependencies_factory`` we trust it
        # entirely (tests). Otherwise the default factory threads the cache
        # through ``RuntimeDependencies.mcp_discovery_cache`` so the runtime
        # factory wires it into ``McpLoader`` and ``AuthMcpTool``.
        self.dependencies_factory = dependencies_factory or (
            DefaultRuntimeDependenciesFactory(
                self.settings,
                mcp_discovery_cache=mcp_discovery_cache,  # type: ignore[arg-type]
            )
        )
        self.agent_factory = agent_factory
        self.runtime_invoker = runtime_invoker
        self.runtime_streamer = runtime_streamer
        # When None, the citation ledger never binds and citations degrade to absent.
        self.citation_store = citation_store
        # When None, the agent's /drafts/ writes fall through to the in-state StateBackend
        # for that run only (non-persistent legacy fallback).
        self.draft_store = draft_store
        # Persistent (conversation_ordinal ↔ tool_call_id) binding store. When None,
        # ordinals are memory-only and citations degrade to absent across resumes.
        self.conversation_tool_ordinal_store = conversation_tool_ordinal_store
        self.event_producer = RuntimeEventProducer(
            persistence=self.persistence,
            event_store=self.event_store,
            on_event_appended=on_event_appended,
        )
        self.run_termination = RunTerminationCoordinator(
            event_producer=self.event_producer,
            terminal_observer=terminal_run_observer,
        )
        self.stream_event_mapper = StreamOrchestrator(
            self.event_producer,
            tool_result_offloader=self._build_tool_result_offloader(),
        )
        self._runtime_streamer_explicit = runtime_streamer is not astream_runtime
        self.audit_emitter = WorkerAuditEmitter(persistence=self.persistence)
        # Rates come from the LiteLLM library (`litellm.model_cost`) with the
        # reviewed override backstop — not the DB catalog. `CostCalculator`
        # remains the integer micro-USD rounding boundary downstream.
        self.pricing_catalog = ModelPricingCatalog.from_litellm()
        self.budget_enforcer = BudgetEnforcer(self.persistence)
        self.budget_charger = BudgetCharger(self.persistence)
        # Pre-run input-token counter for the budget preflight. Defaults to the
        # litellm counter (offline-configured); tests inject a deterministic fake.
        # ``CharHeuristicTokenCounter`` is the second tier of the fallback chain
        # (litellm miss → char/4 → context-window proxy → fail-open Allow).
        self.token_counter: TokenCounterPort = token_counter or LitellmTokenCounter()
        self._char_token_counter: TokenCounterPort = CharHeuristicTokenCounter()
        # Default-built from collaborators so production gets the live impl;
        # tests inject ``InMemoryUsageRecorder`` to assert records directly.
        self.usage_recorder: UsageRecorder = usage_recorder or PostgresUsageRecorder(
            persistence=self.persistence,
            pricing_catalog=self.pricing_catalog,
        )
        self._model_invocation_terminal = (
            model_invocation_terminal
            or ModelInvocationTerminalIntegration(
                journal=model_invocation_store,
                usage_recorder=self.usage_recorder,
                persistence=self.persistence,
            )
        )

    async def handle(self, command: RuntimeRunCommand) -> None:
        """Run the agent and persist lifecycle events."""

        run = await self.persistence.get_run(
            org_id=command.org_id, run_id=command.run_id
        )
        if run is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Run command references an unknown run.",
                retryable=False,
                correlation_id=command.trace_id,
            )
        if run.conversation_id != command.conversation_id:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Run command conversation_id does not match persisted run.",
                retryable=False,
                correlation_id=command.trace_id,
            )
        if run.user_id != command.user_id:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Run command user_id does not match persisted run.",
                retryable=False,
                correlation_id=command.trace_id,
            )
        if not _runtime_context_matches_persisted_run(command.runtime_context, run):
            # Queue payloads are transport, not authority. Refuse a stale or
            # forged context before budgets, snapshot reads, provider attestation,
            # or any external sandbox work can begin.
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Run command runtime context does not match persisted run.",
                retryable=False,
                correlation_id=command.trace_id,
            )

        # Pre-run budget preflight. Done BEFORE flipping status to RUNNING so a Deny
        # leaves the run in QUEUED→FAILED with a distinct safe_error_code.
        budget_decision = await self._preflight_budgets(run, command)
        if isinstance(budget_decision, BudgetPreflightDeny):
            await self._reject_run_for_budget(run, budget_decision)
            return

        run_control_snapshot = (
            await self._run_control_builder.ensure_snapshot(
                run=run,
                trace_id=command.trace_id,
            )
            if self._run_control_builder is not None
            else None
        )
        prepared_run_control = (
            await self._run_control_builder.prepare_binding(
                run=run,
                snapshot=run_control_snapshot,
            )
            if self._run_control_builder is not None
            and run_control_snapshot is not None
            else None
        )
        # Queue payload serialization excludes BYOK keys.  Hydrate exactly once
        # before F10 authority composition and before graph construction; the
        # same in-memory copy is then used by both seams.
        hydrated_context = await self._hydrated_runtime_context(command.runtime_context)
        run = await with_optimistic_retry(
            lambda: self.persistence.update_run_status(
                run_id=command.run_id, status=AgentRunStatus.RUNNING
            )
        )
        await self._append_lifecycle(
            run, RuntimeApiEventType.RUN_STARTED, "Run started"
        )
        if isinstance(budget_decision, BudgetPreflightWarn):
            await self._emit_budget_warning(run, budget_decision)
        await self.audit_emitter.emit_run_started(run)
        run_start_perf = time.perf_counter()
        metrics = AssistantRunMetrics.from_run(run)
        self.stream_event_mapper.update_processor.bind_metrics(run.run_id, metrics)
        budget_reservations: tuple[BudgetReservationRecord, ...] = (
            budget_decision.reservations
            if isinstance(budget_decision, (BudgetPreflightAllow, BudgetPreflightWarn))
            else ()
        )

        ledger = self._bind_citation_ledger(run)
        ledger_token = (
            CitationLedger.bind_for_run(ledger) if ledger is not None else None
        )
        # The ordinal allocator assigns a per-conversation monotonic counter to each
        # tool call; tool wrappers embed that counter in result headers so the model
        # can cite specific sources via ``[[N]]`` markers in its prose.
        # The resolver watches streamed text for those markers and emits ``citation_made``
        # events over the same SSE wire.
        allocator = await self._bind_conversation_ordinal_allocator(command, run)
        allocator_token = (
            ConversationOrdinalAllocator.bind_for_run(allocator)
            if allocator is not None
            else None
        )
        citation_resolver = self._bind_citation_resolver(run, allocator)
        resolver_token = (
            CitationResolver.bind_for_run(citation_resolver)
            if citation_resolver is not None
            else None
        )
        # The shaping subsystem's credential, read ONCE off the same hydrated
        # context the run's own chat model is built from (``factory.py`` composes
        # workspace + user-policy kwargs from these very fields). Both shaping
        # builders below take it: the shaping model is a second outbound call on
        # the run's provider, and on a packaged BYOK install the process env
        # holds no key at all — a builder called without this constructs a model
        # with no credential, fails, and turns its rung off for every run.
        # One producer, two consumers: a second lookup is how one of them goes
        # stale without anyone noticing.
        shaping_credentials = ShapingCredentials.from_runtime_context(hydrated_context)
        # Generative-UI (PRD-07): a run-scoped spec-generation scheduler, bound
        # only when ``SURFACE_SPEC_MODEL`` is set. On a projector ladder miss the
        # tool layer reaches it via the ContextVar and fires a fire-and-forget
        # generation; ``surface_spec_generated`` then upgrades the surface.
        surface_scheduler = self._build_surface_generation_scheduler(
            run, credentials=shaping_credentials
        )
        surface_scheduler_token = (
            SurfaceGenerationScheduler.bind_for_run(surface_scheduler)
            if surface_scheduler is not None
            else None
        )
        # Rung 5 of the same ladder: the SHAPING question, for a payload the
        # deterministic rungs could not bind at all (an array at the root, prose,
        # a CSV block). Bound beside the refinement scheduler and gated by the
        # same resolver, so a run with no shaping model gets neither.
        surface_shaper = self._build_read_path_shaper(
            run, credentials=shaping_credentials
        )
        surface_shaper_token = (
            ReadPathShaper.bind_for_run(surface_shaper)
            if surface_shaper is not None
            else None
        )
        # Generative Surfaces v2 (PRD-A3 D4): a run-scoped Work Ledger emitter,
        # bound only when ``SURFACES_V2`` is on. The tool middleware reaches it
        # via the ContextVar to record ``action.classified`` / ``read.executed``
        # / ``surface.created`` / ``view.derived`` for each executed read.
        work_ledger_emitter = self._build_work_ledger_emitter(run)
        work_ledger_emitter_token = (
            WorkLedgerEmitter.bind_for_run(work_ledger_emitter)
            if work_ledger_emitter is not None
            else None
        )
        operation_context_token: object | None = None
        shadow_comparison_token: object | None = None
        mcp_operation_gateway_token: object | None = None
        model_invocation_effect_tracker: ModelInvocationEffectTracker | None = None
        logging.getLogger(__name__).info(
            "[citations] run.bind run=%s conv=%s allocator_seed=%d "
            "ledger=%s allocator=%s resolver=%s",
            run.run_id,
            command.conversation_id,
            allocator.last_allocated if allocator is not None else -1,
            "bound" if ledger_token is not None else "unbound",
            "bound" if allocator_token is not None else "unbound",
            "bound" if resolver_token is not None else "unbound",
        )
        # Per-tool budget guard. Loaded per-run; ``None`` when the org has no budgets,
        # in which case the guard is unbound and tool calls are a passthrough.
        budget_guard = await self._build_tool_budget_guard(
            run,
            task_policy_binding=(
                prepared_run_control.task_policy
                if prepared_run_control is not None
                else None
            ),
        )
        budget_token = (
            ToolBudgetGuard.bind_for_run(budget_guard)
            if budget_guard is not None
            else None
        )
        # MCP discovery service — built per-run so audit and event emission share
        # the same RunRecord used by the citation ledger.
        discovery_service: McpDiscoveryService | None = None
        discovery_token: object | None = None
        # Bind the MCP descriptor registry before the tool-display lookup so lazily
        # registered MCP descriptors are visible to the composite lookup.
        display_token: object | None = None
        mcp_display_token: object | None = None
        mcp_display_registry: dict[str, ToolDisplayTemplate] = {}
        # PRD-C1 — per-run MCP tool annotations registry (untrusted classifier
        # hints). Bound alongside the display registry so ``_tool_descriptor``
        # (which runs inside this bound context) can capture annotations for the
        # classifier the ledger emitter consults.
        mcp_annotations_token: object | None = None
        mcp_annotations_registry: dict[tuple[str, str], McpToolAnnotations] = {}
        # Per-run ``/workspace/`` backend. Held across the try so the finally can
        # release its pinned broker grant snapshot (``/v1/runs/end``) on every
        # exit path — completion, failure, timeout, or cancel.
        workspace_backend: object | None = None
        run_control_token: object | None = None
        try:
            if prepared_run_control is not None:
                run_control_token = RunControlContext.bind_for_run(
                    prepared_run_control.control,
                    task_policy=prepared_run_control.task_policy,
                )
                # Context Occupancy Ledger sink (design §3.1/§5), installed
                # BEFORE the F10 composition below and unconditionally. The
                # ledger measures at the model-call seam but is not an F10
                # feature, and hanging its sink off the F10 binding is what made
                # it inert everywhere: ``compose`` returns ``None`` whenever
                # ``effective_f10_mode`` is ``OFF`` — the shipped default — so
                # the seam took its no-binding early return and recorded nothing
                # on every model call of every run. Installing here ties the sink
                # to the run's control lifetime and to the same unbind token,
                # without giving an observability concern a say in whether a
                # reliability feature is on.
                RunControlContext.install_context_occupancy_store(
                    self.persistence, org_id=run.org_id
                )
                composed_model_invocation = (
                    await self._model_invocation_composer.compose(
                        run=run,
                        context=hydrated_context,
                        control=prepared_run_control.control,
                    )
                )
                if composed_model_invocation is not None:
                    RunControlContext.install_model_invocation_runtime(
                        composed_model_invocation.binding
                    )
                    model_invocation_effect_tracker = (
                        composed_model_invocation.effect_tracker
                    )
            if self._shadow_comparison_enabled():
                shadow_comparison_token = ShadowComparisonContext.bind_for_run(
                    resolution=self.settings.execution.rollout
                )
            mcp_gateway_services = self._build_mcp_operation_gateway_services(run)
            if self._operation_context_required():
                operation_context_token = OperationContext.bind_for_run(
                    identity=VerifiedOperationIdentity(
                        org_id=run.org_id,
                        user_id=run.user_id,
                        conversation_id=run.conversation_id,
                        run_id=run.run_id,
                    ),
                    policy_snapshot=ToolUsePolicyResolver.resolve(
                        command.runtime_context
                    ),
                    ledger_emitter=self._build_operation_ledger_emitter(
                        run,
                        external_effect_tracker=model_invocation_effect_tracker,
                    ),
                    artifact_service=(
                        self.artifact_service
                        if self._artifact_publication_enabled(run)
                        else None
                    ),
                    outcome_presenter=SurfaceLedgerOperationOutcomePresenter(),
                    mode=self._effective_operation_gateway_mode(mcp_gateway_services),
                    canonical_arguments_durable=mcp_gateway_services is not None,
                )
            if mcp_gateway_services is not None:
                mcp_operation_gateway_token = McpOperationGatewayContext.bind_for_run(
                    mcp_gateway_services
                )
            tool_observation_index = await self._tool_observation_index(command, run)
            workspace_backend = await self._workspace_backend_for_run(
                command,
                run=run,
                mcp_gateway_services=mcp_gateway_services,
            )
            granted_host_roots = await self._granted_host_roots_for_run(
                workspace_backend
            )
            await self._provision_agent_scratch(
                command, workspace_backend=workspace_backend
            )
            dependencies = self._dependencies_for_run(
                command,
                tool_observation_index,
                workspace_backend=workspace_backend,
                granted_host_roots=granted_host_roots,
                run=run,
                mcp_gateway_services=mcp_gateway_services,
            )
            mcp_display_token = McpDisplayRegistryContext.bind_for_run(
                mcp_display_registry
            )
            mcp_annotations_token = McpToolAnnotationsRegistry.bind_for_run(
                mcp_annotations_registry
            )
            display_token = ToolDisplayLookupContext.bind_for_run(
                self._build_tool_display_lookup(dependencies.tool_registry)
            )
            discovery_service = self._bind_mcp_discovery_service(
                run=run,
                runtime_context=command.runtime_context,
                dependencies=dependencies,
            )
            discovery_token = McpDiscoveryService.bind_for_run(discovery_service)
            harness_or_coro = self.agent_factory(
                context=hydrated_context,
                dependencies=dependencies,
            )
            harness = (
                await harness_or_coro
                if inspect.isawaitable(harness_or_coro)
                else harness_or_coro
            )
            messages = await self._messages_for_run(
                command,
                run,
                tool_observation_index=tool_observation_index,
            )
            await self._append_model_call_started(run, metrics, messages)
            if command.runtime_context.model_profile.supports_streaming and (
                self._runtime_streamer_explicit
                or callable(getattr(harness.agent, "astream", None))
            ):
                result = await self._stream_runtime(
                    command,
                    run,
                    harness,
                    messages,
                    metrics,
                )
            else:
                result = await asyncio.wait_for(
                    self.runtime_invoker(
                        harness,
                        messages,
                    ),
                    timeout=command.runtime_context.model_profile.timeout_seconds,
                )
                metrics.record_usage_from(result)
                if await self.stream_event_mapper.append_native_interrupt_events(
                    run=run,
                    value=result,
                ):
                    result = {self._Fields.ACTION_REQUIRED: True}
            await self._process_model_artifact_content(result, run=run)
            if self._is_action_interrupt(result):
                await with_optimistic_retry(
                    lambda: self.persistence.update_run_status(
                        run_id=command.run_id,
                        status=AgentRunStatus.WAITING_FOR_APPROVAL,
                    )
                )
                return
            final_text = self._extract_final_text(result)
            if final_text is not None:
                metrics_payload = metrics.to_payload(
                    completed_at=datetime.now(timezone.utc)
                )
                usage = metrics_payload.get("usage")
                output_tokens = usage.get("output") if isinstance(usage, dict) else None
                await self.persistence.append_message(
                    MessageRecord(
                        conversation_id=command.conversation_id,
                        org_id=command.org_id,
                        run_id=command.run_id,
                        role=MessageRole.ASSISTANT,
                        content_text=final_text,
                        # The turn's ORDERED parts, folded from this run's own
                        # sealed ledger. `content_text` stays what it honestly
                        # is — the final assistant text, used for previews and
                        # the next turn's model context — while `content` is
                        # what the turn actually looked like: text, then tools,
                        # then more text. Without this the terminal re-seed
                        # replaces a correctly interleaved live transcript with
                        # a single blob, which is why the mid-turn prose
                        # appeared to vanish the moment a run completed.
                        content=await self._turn_content.blocks(
                            org_id=command.org_id,
                            run_id=command.run_id,
                            final_text=final_text,
                        ),
                        parent_message_id=run.user_message_id,
                        branch_id=self._trace_text(
                            command.runtime_context, self._Fields.BRANCH_ID
                        ),
                        metadata=AssistantRunMetrics.metadata(metrics_payload),
                        token_count=output_tokens
                        if isinstance(output_tokens, int)
                        else None,
                        trace_id=command.trace_id,
                    )
                )
                final_payload: dict[str, object] = AssistantRunMetrics.with_payload(
                    {self._Fields.MESSAGE: final_text},
                    metrics_payload,
                )
                if ledger is not None:
                    sealed = ledger.sealed_payloads()
                    if sealed:
                        final_payload["citations"] = sealed
                # Sealed list of ordinals cited in this turn, in first-occurrence order.
                # The FE uses this for the share-recipient view and archive replay so
                # citation chips render before the live ``citation_made`` events arrive.
                if citation_resolver is not None:
                    cited_ordinals = citation_resolver.sealed_ordinals()
                    if cited_ordinals:
                        final_payload["cited_ordinals"] = cited_ordinals
                    logging.getLogger(__name__).info(
                        "[citations] run.final_response run=%s cited_ordinals=%s",
                        run.run_id,
                        cited_ordinals,
                    )
                await self._append_lifecycle(
                    run,
                    RuntimeApiEventType.FINAL_RESPONSE,
                    final_text,
                    payload=final_payload,
                    metadata=AssistantRunMetrics.metadata(metrics_payload),
                )
        except TimeoutError:
            await self._reconcile_inflight_tool_calls(
                run,
                outcome=ToolOutcome.TIMED_OUT,
                error_code=ToolErrorCode.TOOL_RUN_TIMEOUT,
            )
            failed = await with_optimistic_retry(
                lambda: self.persistence.update_run_status(
                    run_id=command.run_id, status=AgentRunStatus.TIMED_OUT
                )
            )
            # Subagents need settling on THIS path too. A child's terminal frame
            # comes from the `task` tool's result message, so a run that ends
            # mid-delegation emits none and the cockpit keeps a spinning card
            # forever. Appended BEFORE `_emit_receipt_then_terminate` because
            # that is what seals the prefix: settle after it and the append is
            # refused by the seal guard, so the run dies with a
            # `LedgerSealViolation` that hides the real terminal reason.
            await (
                self.stream_event_mapper.update_processor
            ).close_open_subagents_as_cancelled(run=run)
            await self._emit_receipt_then_terminate(
                run=failed,
                terminal_status=AgentRunStatus.TIMED_OUT,
                reason=TerminationReason.RUN_TIMEOUT,
                summary="Run timed out",
            )
            await self.audit_emitter.emit_run_failed(
                failed,
                status=AgentRunStatus.TIMED_OUT,
                error_class="TimeoutError",
                error_code=ToolErrorCode.TOOL_RUN_TIMEOUT.value,
                duration_ms=int((time.perf_counter() - run_start_perf) * 1000),
            )
            await self._record_run_usage(
                failed,
                metrics=metrics,
                completed_at=failed.completed_at or datetime.now(timezone.utc),
                status=AgentRunStatus.TIMED_OUT.value,
                budget_reservations=budget_reservations,
                subject_fingerprint=(
                    prepared_run_control.control.snapshot.subject_fingerprint
                    if prepared_run_control is not None
                    else None
                ),
            )
            await self._observe_e2_shadow_projections(failed)
            self.stream_event_mapper.message_processor.discard_ledger(run.run_id)
            self.stream_event_mapper.update_processor.discard_metrics(run.run_id)
            return
        except asyncio.CancelledError:
            # Cancellation is a BaseException, so ``except Exception`` below
            # never saw it and in-flight tool calls were left open on this
            # path — the ledger's third terminal case, and the one a worker
            # shutdown takes. Reconcile, then re-raise so cancellation keeps
            # propagating: the run's terminal status and event stay owned by
            # the cancel handler, which is the only thing that knows the run
            # was cancelled rather than killed.
            await self._reconcile_inflight_tool_calls(
                run,
                outcome=ToolOutcome.CANCELLED,
                error_code=ToolErrorCode.TOOL_CANCELLED,
            )
            # Subagents need the same settling, and did not get it. A child's
            # terminal frame comes from the ``task`` tool's result message, so a
            # run stopped mid-delegation emitted none and the cockpit kept a
            # spinning card and an "N live" count forever. These are causal
            # facts about this run, so they are appended here — inside the
            # prefix the cancel handler is about to seal — not after it.
            await (
                self.stream_event_mapper.update_processor
            ).close_open_subagents_as_cancelled(run=run)
            raise
        except Exception as exc:
            await self._reconcile_inflight_tool_calls(
                run,
                outcome=ToolOutcome.FAILED,
                error_code=ToolErrorCode.TOOL_EXCEPTION,
            )
            failed = await with_optimistic_retry(
                lambda: self.persistence.update_run_status(
                    run_id=command.run_id, status=AgentRunStatus.FAILED
                )
            )
            # Map typed fatal errors to semantic termination reasons so the FE and
            # audit log can distinguish budget / auth failures from generic errors.
            termination_reason = _termination_reason_for(exc)
            error = RuntimeErrorEnvelope.from_exception(
                exc,
                correlation_id=command.trace_id,
                default_message="We couldn't complete this run. Please try again.",
            )
            # Subagents need settling on THIS path too. A child's terminal frame
            # comes from the `task` tool's result message, so a run that ends
            # mid-delegation emits none and the cockpit keeps a spinning card
            # forever. Appended BEFORE `_emit_receipt_then_terminate` because
            # that is what seals the prefix: settle after it and the append is
            # refused by the seal guard, so the run dies with a
            # `LedgerSealViolation` that hides the real terminal reason.
            await (
                self.stream_event_mapper.update_processor
            ).close_open_subagents_as_cancelled(run=run)
            await self._emit_receipt_then_terminate(
                run=failed,
                terminal_status=AgentRunStatus.FAILED,
                reason=termination_reason,
                summary="Run failed",
                cause=exc,
                extra_payload=error.model_dump(mode="json"),
            )
            await self.audit_emitter.emit_run_failed(
                failed,
                status=AgentRunStatus.FAILED,
                error_class=type(exc).__name__,
                error_code=error.code.value,
                duration_ms=int((time.perf_counter() - run_start_perf) * 1000),
            )
            await self._record_run_usage(
                failed,
                metrics=metrics,
                completed_at=failed.completed_at or datetime.now(timezone.utc),
                status=AgentRunStatus.FAILED.value,
                budget_reservations=budget_reservations,
                subject_fingerprint=(
                    prepared_run_control.control.snapshot.subject_fingerprint
                    if prepared_run_control is not None
                    else None
                ),
            )
            await self._observe_e2_shadow_projections(failed)
            self.stream_event_mapper.message_processor.discard_ledger(run.run_id)
            self.stream_event_mapper.update_processor.discard_metrics(run.run_id)
            raise
        finally:
            if run_control_token is not None:
                RunControlContext.unbind(run_control_token)  # type: ignore[arg-type]
            if shadow_comparison_token is not None:
                ShadowComparisonContext.unbind(shadow_comparison_token)  # type: ignore[arg-type]
            if mcp_operation_gateway_token is not None:
                McpOperationGatewayContext.unbind(mcp_operation_gateway_token)  # type: ignore[arg-type]
            if operation_context_token is not None:
                OperationContext.unbind(operation_context_token)  # type: ignore[arg-type]
            if work_ledger_emitter_token is not None:
                WorkLedgerEmitter.unbind(work_ledger_emitter_token)
            if surface_scheduler_token is not None:
                SurfaceGenerationScheduler.unbind(surface_scheduler_token)
            if surface_shaper_token is not None:
                ReadPathShaper.unbind(surface_shaper_token)
            if resolver_token is not None:
                CitationResolver.unbind(resolver_token)
            if allocator_token is not None:
                ConversationOrdinalAllocator.unbind(allocator_token)
            if ledger_token is not None:
                CitationLedger.unbind(ledger_token)
            if budget_token is not None:
                ToolBudgetGuard.unbind(budget_token)
            if discovery_token is not None:
                McpDiscoveryService.unbind(discovery_token)
            if display_token is not None:
                ToolDisplayLookupContext.unbind(display_token)
            if mcp_display_token is not None:
                McpDisplayRegistryContext.unbind(mcp_display_token)
            if mcp_annotations_token is not None:
                McpToolAnnotationsRegistry.unbind(mcp_annotations_token)
            self._file_store_wiring().discard_tool_result_projections(run_id=run.run_id)
            await WorkspaceBackendWorkerWiring.release_backend(workspace_backend)

        completed = await with_optimistic_retry(
            lambda: self.persistence.update_run_status(
                run_id=command.run_id, status=AgentRunStatus.COMPLETED
            )
        )
        self.stream_event_mapper.message_processor.discard_ledger(run.run_id)
        self.stream_event_mapper.update_processor.discard_metrics(run.run_id)
        completed_at = completed.completed_at or datetime.now(timezone.utc)
        metrics_payload = metrics.to_payload(completed_at=completed_at)
        await self._emit_receipt_then_terminate(
            run=completed,
            terminal_status=AgentRunStatus.COMPLETED,
            reason=TerminationReason.NORMAL_COMPLETION,
            summary="Run completed",
            extra_payload=AssistantRunMetrics.with_payload({}, metrics_payload),
            extra_metadata=AssistantRunMetrics.metadata(metrics_payload),
        )
        await self.audit_emitter.emit_run_completed(
            completed,
            duration_ms=int((time.perf_counter() - run_start_perf) * 1000),
        )
        await self._record_run_usage(
            completed,
            metrics=metrics,
            completed_at=completed_at,
            status=AgentRunStatus.COMPLETED.value,
            budget_reservations=budget_reservations,
            subject_fingerprint=(
                prepared_run_control.control.snapshot.subject_fingerprint
                if prepared_run_control is not None
                else None
            ),
        )
        await self._observe_e2_shadow_projections(completed)

    async def _emit_receipt_then_terminate(
        self, *, run: RunRecord, **terminate_kwargs: object
    ) -> None:
        """Append the run receipt (Generative Surfaces v2, PRD-E1) then terminate.

        The single chokepoint every terminal path in this handler
        (completed / failed / timed-out) routes through: it folds the run's
        ledger into ``surface.created {kind: receipt}`` + ``receipt.emitted`` and
        appends both BEFORE the terminal lifecycle event, because both are
        causal facts and belong inside the run's sealed prefix. ``terminate``
        then drains any remaining registered projections and seals — see
        :mod:`agent_runtime.api.ledger_seal` for why that ordering is now an
        enforced invariant rather than a convention each caller re-derives.
        Gated on the same ``SURFACES_V2`` value the WorkLedgerEmitter binds on,
        so flag-off is byte-identical. Best-effort: emission never blocks
        termination.
        """

        await emit_receipt_if_enabled(
            enabled=self.settings.execution.surfaces_v2,
            event_producer=self.event_producer,
            event_store=self.event_store,
            run=run,
        )
        await self.run_termination.terminate(run=run, **terminate_kwargs)

    def _shadow_comparison_enabled(self) -> bool:
        """D2 binds only when D1 resolved at least one exact shadow lane."""

        return bool(self.settings.execution.rollout.modes.shadowed())

    def _shadow_projection_observation_enabled(self) -> bool:
        """Avoid an additional store read unless a projection lane is shadowing."""

        modes = self.settings.execution.rollout.modes
        return any(
            modes.mode_for(capability) is RolloutMode.SHADOW
            for capability in (
                RolloutCapability.PRESENTATION_V2_1,
                RolloutCapability.ARTIFACT_REPOSITORY,
            )
        )

    async def _observe_e2_shadow_projections(self, run: RunRecord) -> None:
        """Run D2's bounded terminal read-only observer after legacy completion."""

        if not self._shadow_projection_observation_enabled():
            return
        observer = ShadowRunProjectionObserver(
            ShadowComparisonService(resolution=self.settings.execution.rollout)
        )
        await observer.observe(
            event_store=self.event_store,
            org_id=run.org_id,
            run_id=run.run_id,
            run_status=run.status,
        )

    async def _preflight_budgets(
        self,
        run: RunRecord,
        command: RuntimeRunCommand,
    ):
        """Estimate the run's spend and check it against active budgets; fails open on transient errors.

        The estimate is built **lazily** and handed to the enforcer, which
        resolves it only when the tenant has active budgets — so the no-budget
        hot path (single-user desktop) never reads messages or tokenizes. When
        budgets exist, the input-token count comes from the REAL first-call
        messages via ``token_counter`` (litellm, offline). The whole method is
        the outermost fail-open tier of the fallback chain.
        """

        try:
            return await self.budget_enforcer.preflight(
                org_id=command.org_id,
                user_id=command.user_id,
                run_id=command.run_id,
                estimate=lambda: self._build_preflight_estimate(run, command),
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "budget_preflight_failed",
                extra={"metadata": {"run_id": command.run_id}},
                exc_info=True,
            )
            return BudgetPreflightAllow()

    async def _build_preflight_estimate(
        self,
        run: RunRecord,
        command: RuntimeRunCommand,
    ) -> BudgetEstimate:
        """Assemble the real first-call messages, count input tokens, and price the run.

        Resolved by the enforcer only when active budgets exist. ``pricing`` may
        be ``None`` (unpriced model) — the estimator handles that. ``max_output_tokens``
        is a first-class ``ModelConfig`` field; depth-scaled values land there via
        ``DepthBudgetTable.apply`` at run-create, so this is the single read site
        for the post-mapped output cap.
        """

        pricing = await self.pricing_catalog.lookup(
            provider=run.model_provider,
            model_name=run.model_name,
            region="global",
            at=datetime.now(timezone.utc),
        )
        model_profile = command.runtime_context.model_profile
        input_tokens = await self._preflight_input_tokens(run, command, model_profile)
        return BudgetEstimator.estimate(
            input_tokens=input_tokens,
            max_output_tokens=model_profile.max_output_tokens,
            pricing=pricing,
        )

    async def _preflight_input_tokens(
        self,
        run: RunRecord,
        command: RuntimeRunCommand,
        model_profile: object,
    ) -> int:
        """Count the first model call's input tokens with a defence-in-depth fallback chain.

        ``litellm.token_counter`` (real tokenizer where bundled, offline tiktoken
        approximation otherwise) → char/4 heuristic over the same assembled text →
        the model's ``max_input_tokens`` context-window proxy (the pre-slice-3
        conservative worst-case). Each tier runs only when the prior yields no
        positive count. A hard failure (message read, etc.) bubbles to the
        caller's fail-open guard.
        """

        messages = await self._assemble_base_messages(command, run)
        # 1. litellm token_counter. Bare ``model_name`` routes to the provider's
        #    real tokenizer where litellm bundles one (openai/anthropic/gemini)
        #    and to an offline tiktoken approximation otherwise — always
        #    token-based, never char/4, and network-free under the guardrail.
        counted = self._safe_count(self.token_counter, run.model_name, messages)
        if counted is not None and counted > 0:
            return counted
        # 2. char/4 heuristic over the same assembled message text.
        heuristic = self._safe_count(self._char_token_counter, run.model_name, messages)
        if heuristic is not None and heuristic > 0:
            return heuristic
        # 3. Context-window proxy: the model's full input window. Guarantees a
        #    positive, over-biased estimate when both counters come up empty.
        return getattr(model_profile, "max_input_tokens", 1)

    @staticmethod
    def _safe_count(
        counter: TokenCounterPort,
        model: str,
        messages: Sequence[Mapping[str, str]],
    ) -> int | None:
        """Call a token counter, absorbing any error into ``None`` so the chain continues."""

        try:
            return counter.count(model=model, messages=messages)
        except Exception:
            logging.getLogger(__name__).debug(
                "preflight_token_count_failed", exc_info=True
            )
            return None

    async def _assemble_base_messages(
        self,
        command: RuntimeRunCommand,
        run: RunRecord,
    ) -> list[dict[str, str]]:
        """Assemble the base first-call message dicts (no prior-tool-context injection).

        The shared core of :meth:`_messages_for_run`: load the conversation
        history, follow the parent chain to the run's user message, and render
        each USER/ASSISTANT/SYSTEM message to a ``{role, content}`` dict. The
        budget preflight reuses this to count the REAL first-call input without
        paying for the tool-observation index it does not need.
        """

        records = await self.persistence.list_messages(
            org_id=command.org_id,
            conversation_id=command.conversation_id,
            limit=200,
        )
        selected = self._selected_message_chain(records, run.user_message_id)
        return [
            {
                self._Fields.ROLE: message.role.value,
                self._Fields.CONTENT: self._message_content_for_runtime(message),
            }
            for message in selected
            if message.role
            in {MessageRole.USER, MessageRole.ASSISTANT, MessageRole.SYSTEM}
        ]

    async def _reject_run_for_budget(
        self,
        run: RunRecord,
        decision: "BudgetPreflightDeny",
    ) -> None:
        """Mark the run FAILED with a distinct safe_error_code + emit RUN_REJECTED."""

        failed = await with_optimistic_retry(
            lambda: self.persistence.update_run_status(
                run_id=run.run_id,
                status=AgentRunStatus.FAILED,
            )
        )
        await self.event_producer.append_api_event(
            run=failed,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.RUN_REJECTED,
            summary="Run rejected: budget exceeded",
            payload={
                "reason": decision.reason,
                "budget_id": decision.budget.id,
                "scope": decision.budget.scope.value,
                "period": decision.budget.period.value,
                "current_micro_usd": decision.current_micro_usd,
                "current_tokens": decision.current_tokens,
                "limit_micro_usd": decision.budget.limit_micro_usd,
                "limit_tokens": decision.budget.limit_tokens,
            },
        )
        await self.audit_emitter.emit_run_failed(
            failed,
            status=AgentRunStatus.FAILED,
            error_class="BudgetExceeded",
            error_code="budget_exceeded",
            duration_ms=0,
        )

    async def _emit_budget_warning(
        self,
        run: RunRecord,
        decision: "BudgetPreflightWarn",
    ) -> None:
        """Emit a ``BUDGET_WARNING`` event when a soft-cap is crossed at preflight."""
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.BUDGET_WARNING,
            summary="Budget soft cap crossed",
            payload={
                "budget_id": decision.budget.id,
                "scope": decision.budget.scope.value,
                "period": decision.budget.period.value,
                "current_micro_usd": decision.current_micro_usd,
                "current_tokens": decision.current_tokens,
                "limit_micro_usd": decision.budget.limit_micro_usd,
                "limit_tokens": decision.budget.limit_tokens,
                "severity": "soft_cap",
            },
        )

    async def _charge_budgets(
        self,
        run: RunRecord,
        *,
        observed_micro_usd: int | None,
        observed_tokens: int,
        reservations: Sequence[BudgetReservationRecord],
    ) -> None:
        """Best-effort post-run budget charge. Idempotent on run_id."""

        try:
            await self.budget_charger.charge_run(
                org_id=run.org_id,
                user_id=run.user_id,
                run_id=run.run_id,
                observed_micro_usd=observed_micro_usd,
                observed_tokens=observed_tokens,
                reservations=tuple(reservations),
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "budget_charge_failed",
                extra={"metadata": {"run_id": run.run_id}},
                exc_info=True,
            )

    async def _record_run_usage(
        self,
        run: RunRecord,
        *,
        metrics: AssistantRunMetrics,
        completed_at: datetime,
        status: str,
        budget_reservations: Sequence[BudgetReservationRecord] = (),
        subject_fingerprint: str | None = None,
    ) -> None:
        """Persist the per-run and per-LLM-call usage records, then charge budgets.

        Both records share the same ``pricing_at`` snapshot so a clock boundary
        mid-run produces one pricing version. The recorder is fail-soft — write
        failures are absorbed rather than propagated to the run lifecycle.
        """

        await self._model_invocation_terminal.finalize(
            run=run,
            metrics=metrics,
            subject_fingerprint=subject_fingerprint,
            completed_at=completed_at,
        )
        usage_record = metrics.to_usage_record(
            run, completed_at=completed_at, status=status
        )
        run_cost_micro_usd = await self._model_invocation_terminal.record_run_usage(
            run=run,
            metrics=metrics,
            completed_at=completed_at,
            status=status,
        )
        for call_record in metrics.model_call_usage_records(run, trace_id=run.trace_id):
            await self.usage_recorder.record_call(call_record, pricing_at=completed_at)
        # Apply observed spend against budgets; idempotent on run_id. Preflight
        # reservations are consumed in the same call so the budget reaper skips them.
        await self._charge_budgets(
            run,
            observed_micro_usd=run_cost_micro_usd,
            observed_tokens=usage_record.total_tokens,
            reservations=budget_reservations,
        )

    async def _messages_for_run(
        self,
        command: RuntimeRunCommand,
        run: RunRecord,
        *,
        tool_observation_index: ToolObservationIndex | None = None,
    ) -> tuple[dict[str, str], ...]:
        """Build the message list for the LLM call, optionally injecting prior tool-result context."""
        messages = await self._assemble_base_messages(command, run)
        observations = tool_observation_index or await self._tool_observation_index(
            command, run
        )
        if observations.prompt_context is not None:
            self._insert_prior_tool_context(messages, observations.prompt_context)
        return tuple(messages)

    @staticmethod
    def _build_tool_display_lookup(tool_registry: object) -> ToolDisplayLookup:
        """Build the per-run tool-display-template lookup for the producer.

        Probes ``tool_registry.display_for(name)`` first so author-written
        templates take precedence, then falls through to the per-run MCP
        descriptor registry populated lazily as servers load. This makes
        synthesised MCP templates visible to ``PresentationGenerator``
        without coupling the producer to the registry directly.
        """

        from agent_runtime.capabilities.mcp.descriptor_registry import (  # noqa: PLC0415
            McpDisplayRegistryContext,
        )

        display_for = getattr(tool_registry, "display_for", None)
        tool_registry_lookup: ToolDisplayLookup
        if callable(display_for):
            tool_registry_lookup = display_for  # type: ignore[assignment]
        else:
            tool_registry_lookup = lambda _name: None  # noqa: E731

        def composite(tool_name: str) -> object:
            template = tool_registry_lookup(tool_name)
            if template is not None:
                return template
            return McpDisplayRegistryContext.get(tool_name)

        return composite  # type: ignore[return-value]

    async def _hydrated_runtime_context(
        self, context: AgentRuntimeContext
    ) -> AgentRuntimeContext:
        """Re-attach the user's BYOK provider keys before harness construction.

        Returns the context unchanged when no hydrator is wired (tests,
        deployments without the backend lane) or when the user has no stored
        keys — the run then relies on deployment env keys exactly as before.
        """

        if self._provider_keys_hydrator is None:
            return context
        return await self._provider_keys_hydrator.hydrate(context)

    def _file_store_wiring(self) -> FileStoreWorkerWiring:
        """Shared file-store gate + offloader/read-backend builders.

        The event store and persistence port are the same
        ``FileRuntimeApiStore`` instance when the file backend is wired, so
        either would do; the wiring reads from the event store. Kept in one
        place so this path and the approval-resume path cannot drift.
        """

        return self._file_store_worker_wiring

    def _file_backend_store(self) -> object | None:
        """Return the active file store, or ``None`` on non-file backends."""

        return self._file_store_wiring().file_store()

    def _sandbox_worker_bundle(self, context: AgentRuntimeContext) -> object | None:
        """Compose D3 only on the file-native, fully attested desktop path.

        The composition object owns the C1/A2 snapshot chain, sealed source
        bytes, file lifecycle/session/usage/cleanup records, and the
        gateway-routed operation runner.  Any missing authority returns
        ``None``; this handler never substitutes an in-memory/Postgres/direct
        provider path.
        """

        from runtime_worker.sandbox_composition import (  # noqa: PLC0415
            SandboxWorkerBundle,
        )

        return SandboxWorkerBundle.compose(
            runtime_context=context,
            file_store=self._file_backend_store(),
            artifact_service=self.artifact_service,  # type: ignore[arg-type]
            artifact_blob_store=self._artifact_blob_store,  # type: ignore[arg-type]
            workspace_overlay_store=self._workspace_overlay_store,  # type: ignore[arg-type]
            run_store=self.persistence,
            patch_collector=self._sandbox_patch_collector,  # type: ignore[arg-type]
            env=self._capability_env,
            provider_overrides=self._sandbox_provider_overrides,  # type: ignore[arg-type]
        )

    def _build_tool_result_offloader(self) -> object | None:
        """Construct the file-store tool-result offloader, or ``None`` elsewhere."""

        return self._file_store_wiring().tool_result_offloader()

    def _subagent_artifacts_backend(self, command: RuntimeRunCommand) -> object:
        """Return the per-subagent trace backend for the active store backend.

        On the desktop file store this reads the canonical per-subagent JSONL
        directly; elsewhere it is the event-store projection used historically.
        """

        file_backend = self._file_store_wiring().subagent_artifacts_backend(
            org_id=command.org_id,
            conversation_id=command.conversation_id,
        )
        if file_backend is not None:
            return file_backend
        return SubagentArtifactsBackend(
            event_store=self.event_store,
            persistence=self.persistence,
            org_id=command.org_id,
            conversation_id=command.conversation_id,
            current_run_id=command.run_id,
        )

    async def _workspace_backend_for_run(
        self,
        command: RuntimeRunCommand,
        *,
        run: RunRecord,
        mcp_gateway_services: McpOperationGatewayServices | None,
    ) -> object | None:
        """Construct the per-run ``/workspace/`` backend, or ``None``.

        Gated on the desktop capability broker (env config + the run's active
        grant snapshot). Off the desktop path — web / postgres / in-memory — the
        broker env is absent, so this returns ``None`` and the factory composes
        no ``/workspace/`` route, leaving those images byte-identical. Broker
        unavailability or zero active grants likewise yield ``None`` (fail-soft).

        The non-enforced compatibility route is read-only. Direct workspace
        mutation cannot be restored by this mode or any rollout setting.
        """

        if (
            self.settings.execution.workspace_effect_mode
            is OperationGatewayMode.ENFORCE
        ):
            return await self._workspace_effect_backend_for_run(
                run=run,
                mcp_gateway_services=mcp_gateway_services,
            )

        return await self._workspace_wiring().workspace_backend()

    def _workspace_wiring(self) -> WorkspaceBackendWorkerWiring:
        """The desktop capability-broker wiring for this run.

        One construction site for both broker lanes — the ``/workspace/`` route
        and the attached-folder roots — so a test can drive the real handler
        branches over a fake broker transport instead of the loopback socket.
        """

        return WorkspaceBackendWorkerWiring(
            http_client=self._workspace_broker_http_client  # type: ignore[arg-type]
        )

    async def _granted_host_roots_for_run(
        self, workspace_backend: object | None
    ) -> tuple[object, ...] | None:
        """The host folders the user attached, for this run's filesystem rules.

        ``None`` off the desktop path — there is no workspace object at all, the
        factory builds no host rules, and the composition stays byte-identical.

        Otherwise the answer comes from the capability broker, and which lane
        asks it is an implementation detail:

        * the compatibility lane's ``BrokeredWorkspaceBackend`` was itself built
          from this run's grant snapshot and already exposes ``granted_roots``,
          so we read it and issue no second broker call;
        * ENFORCE's ``WorkspaceGatewayBackend`` / ``WorkspaceTombstoneBackend``
          structurally cannot answer — their host-session projection is path-free,
          and that channel is C2's private WRITE bootstrap, not something to widen
          — so the wiring asks the broker directly, over the same
          ``/v1/grants/snapshot`` projection and the same mount-table mapping.

        Both branches therefore produce roots from one broker fact through one
        mapping. Before this, the ENFORCE branch produced nothing, so a folder the
        user had explicitly attached interrupted on every single read.
        """

        if workspace_backend is None:
            return None
        roots = getattr(workspace_backend, "granted_roots", None)
        if isinstance(roots, tuple):
            return roots
        return await self._workspace_wiring().granted_host_roots()

    async def _provision_agent_scratch(
        self,
        command: RuntimeRunCommand,
        *,
        workspace_backend: object | None,
    ) -> None:
        """Create ``$COPILOT_HOME/.tmp/<conversation_id>/`` for this run (D3/D5).

        Runs before the graph does, so the agent's own working area exists the
        first time it writes. Gated on the same desktop signal as the host
        filesystem rules — a ``None`` workspace backend provisions nothing — and
        never raises: a run must not fail for want of a working directory.

        An EFFECT on disk, which is why it is not part of the dependency
        composition next door: the granted roots there are a VALUE the
        composition reads. It lives at the single ``handle`` call site so the
        two run paths (initial + approval resume) cannot drift into one
        provisioning and the other not. The directory it makes real is the one
        ``factory._agent_scratch_root`` resolves from the same installation, so
        this is not a second opinion about where the scratch is.
        """

        wiring = AgentScratchWorkerWiring(workspace_backend=workspace_backend)
        if not wiring.enabled:
            return
        wiring.provision(
            conversation_id=command.conversation_id,
            run_id=command.run_id,
            title=await self._conversation_title(command),
        )

    async def _conversation_title(self, command: RuntimeRunCommand) -> str | None:
        """This chat's human name, for the scratch's ``meta.json`` (D5).

        Read from the conversation record on every run rather than carried on
        the command: a command is a snapshot taken at enqueue time, so a chat
        renamed between runs would keep announcing its old name, and
        ``provision`` rewrites ``meta.json`` each run precisely so the scratch
        reports the CURRENT one.

        ``None`` on any failure. The title is orientation for a human browsing
        ``.tmp/`` — every live ``meta.json`` read `"title": null` before this,
        because the call site passed no title at all — and losing that
        orientation is not worth failing a run over, so a store that cannot
        answer degrades to the untitled scratch we already had.
        """

        try:
            conversation = await self.persistence.get_conversation(
                org_id=command.org_id,
                user_id=command.user_id,
                conversation_id=command.conversation_id,
            )
        except Exception:  # noqa: BLE001 — see the docstring; orientation only.
            logging.getLogger(__name__).warning("agent_scratch.title_lookup_failed")
            return None
        return None if conversation is None else conversation.title

    @staticmethod
    def _tombstone(reason: str) -> None:
        """Record WHY the enforced workspace route fell closed.

        The tombstone is correct — refusing beats silently reading the wrong
        filesystem — but a refusal nobody can explain is a support ticket and a
        wasted live run per hypothesis.
        """

        logging.getLogger(__name__).warning("workspace_effect.tombstone %s", reason)

    def _missing_workspace_dependencies(
        self, mcp_gateway_services: McpOperationGatewayServices | None
    ) -> tuple[str, ...]:
        """Which collaborators the enforced workspace route did not get.

        Named individually rather than folded into one boolean: "the workspace
        lane needs the MCP gateway" and "the desktop broker minted no host
        session" are different problems with opposite fixes, and the previous
        `or`-chain made them indistinguishable from outside.
        """

        candidates = (
            ("mcp_gateway_services", mcp_gateway_services),
            ("workspace_host_sessions", self._workspace_host_sessions),
            ("workspace_overlay_store", self._workspace_overlay_store),
            ("artifact_blob_store", self._artifact_blob_store),
            ("artifact_reference_store", self._artifact_reference_store),
        )
        return tuple(name for name, value in candidates if value is None)

    async def _workspace_read_only(self, reason: str) -> object:
        """No write authority — keep READS working instead of refusing both.

        The enforced lane used to answer every one of its five fail-closed
        conditions with `WorkspaceTombstoneBackend`, which refuses reads too. So
        switching enforce on made a folder the user had just attached LESS
        usable than leaving it off, and the model was told "Local workspace
        access is unavailable. Create an artifact or download instead" — which
        is exactly why it reached for `publish_artifact` instead of the file.

        Losing the commit authority should cost the user WRITES. It should never
        cost them the ability to look at their own folder. The broker can serve
        reads without a host session, so that is what a missing write authority
        degrades to now.

        The tombstone survives for the case it was actually built for: nothing
        is available at all, and a `StateBackend` fallthrough would answer with
        an empty listing and a green tick.
        """

        from agent_runtime.capabilities.workspace.deep_backend import (  # noqa: PLC0415
            WorkspaceTombstoneBackend,
        )

        readable = await self._workspace_wiring().workspace_backend()
        if readable is None:
            self._tombstone(f"{reason}+no_broker_reads")
            return WorkspaceTombstoneBackend()
        self._tombstone(f"{reason}+degraded_to_read_only")
        return readable

    async def _workspace_effect_backend_for_run(
        self,
        *,
        run: RunRecord,
        mcp_gateway_services: McpOperationGatewayServices | None,
    ) -> object:
        """Build C3's only enforced workspace path or a fail-closed tombstone."""

        from agent_runtime.capabilities.operations.gateway import (  # noqa: PLC0415
            OperationGateway,
        )
        from agent_runtime.capabilities.workspace.deep_backend import (  # noqa: PLC0415
            WorkspaceGatewayBackend,
            WorkspaceTombstoneBackend,
        )
        from agent_runtime.capabilities.workspace.effects import (  # noqa: PLC0415
            WorkspaceGatewayServices,
            WorkspaceGrantGate,
            WorkspaceOperationAdapter,
        )
        from agent_runtime.capabilities.workspace.merged_backend import (  # noqa: PLC0415
            MergedWorkspaceBackend,
        )
        from agent_runtime.capabilities.workspace.operation_port import (  # noqa: PLC0415
            WorkspaceOperationPort,
        )
        from agent_runtime.capabilities.workspace.ports import (  # noqa: PLC0415
            WorkspaceOverlayReadPort,
        )
        from runtime_worker.workspace_effect_storage import (  # noqa: PLC0415
            RuntimeWorkspaceProposalStore,
        )

        # A C3 backend is a v2.1 capability exposure, not merely a UI choice.
        # If an explicitly enabled E2 cohort or a targeted rollback does not
        # admit this persisted run, the model receives the tombstone route and
        # cannot stage an overlay or enqueue a workspace effect.
        # Every `return WorkspaceTombstoneBackend()` below SAYS WHY. The branch
        # used to be five silent conditions, and the model's only clue was
        # "Local workspace access is unavailable" — which is what the user sees
        # too, and which cost a full live-run cycle per guess to narrow down.
        # A fail-closed path that records nothing is the same shape as the
        # original defect this whole program started from: `ls ~/Downloads`
        # answering `[]` with a green tick.
        #
        # Reasons only — no paths, no run content. Which DEPENDENCY is missing
        # is deployment truth, not user data.
        if not self._e2_rollout_admission.permits_all(
            capabilities=(
                RolloutCapability.OPERATION_GATEWAY,
                RolloutCapability.EFFECT_STAGER,
                RolloutCapability.EFFECT_COMMIT,
                RolloutCapability.WORKSPACE_OVERLAY,
                RolloutCapability.WORKSPACE_COMMIT,
            ),
            facts_provider=self._rollout_facts_for_run(run),
        ):
            return await self._workspace_read_only("rollout_admission_denied")
        missing = self._missing_workspace_dependencies(mcp_gateway_services)
        if missing:
            return await self._workspace_read_only(
                f"missing_dependencies={'+'.join(missing)}"
            )
        scope = EffectExecutionScope(
            org_id=run.org_id,
            user_id=run.user_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            owner_ref=f"principal://users/{run.user_id}",
        )
        from runtime_worker.workspace_effect_storage import (  # noqa: PLC0415
            resolve_workspace_host_session,
        )

        session = await resolve_workspace_host_session(
            self._workspace_host_sessions,
            scope,
        )
        if session is None or session.base_read is None:
            self._tombstone(
                "no_host_session"
                if session is None
                else "host_session_has_no_base_read"
            )
            return WorkspaceTombstoneBackend()
        merged = MergedWorkspaceBackend(
            run_id=run.run_id,
            base_read=session.base_read,
            overlay_store=WorkspaceOverlayReadPort.bind(self._workspace_overlay_store),
            blob_store=self._artifact_blob_store,
        )
        gate = WorkspaceGrantGate(grants=session.grants)
        gateway = OperationGateway(
            descriptors=DEFAULT_OPERATION_DESCRIPTORS,
            classifier=mcp_gateway_services.classifier,
            gates=gate,
        )
        services = WorkspaceGatewayServices(
            stager=mcp_gateway_services.stager,
            scope=mcp_gateway_services.stage_scope,
            actor=mcp_gateway_services.stage_author,
            proposals=RuntimeWorkspaceProposalStore(
                blobs=self._artifact_blob_store,
                references=self._artifact_reference_store,
                scope=scope,
            ),
            grants=session.grants,
            # Read from the PERSISTED run context, not re-derived here. The
            # decision was sealed once at run-create against the workspace
            # master switch; re-resolving it in the worker would let a
            # Settings change mid-flight retro-authorize a run the user
            # started under a different posture.
            bypass=run.runtime_context.filesystem_bypass,
        )
        return WorkspaceGatewayBackend(
            merged=merged,
            operations=WorkspaceOperationPort.bind(
                gateway=gateway,
                adapter=WorkspaceOperationAdapter(
                    services=services,
                    run_id=run.run_id,
                    base_read=session.base_read,
                    overlay_store=self._workspace_overlay_store,
                    blob_store=self._artifact_blob_store,
                ),
            ),
            grants=session.grants,
        )

    def _dependencies_for_run(
        self,
        command: RuntimeRunCommand,
        tool_observation_index: ToolObservationIndex,
        *,
        workspace_backend: object | None = None,
        granted_host_roots: tuple[object, ...] | None = None,
        run: RunRecord | None = None,
        mcp_gateway_services: McpOperationGatewayServices | None = None,
    ) -> RuntimeDependencies:
        """Build ``RuntimeDependencies`` augmented with per-run backends (drafts, subagent artifacts, workspace)."""
        rollout_facts = (
            self._rollout_facts_for_run(run) if isinstance(run, RunRecord) else None
        )
        if (
            isinstance(self.dependencies_factory, DefaultRuntimeDependenciesFactory)
            and rollout_facts is not None
        ):
            dependencies = self.dependencies_factory.for_run(
                command.runtime_context,
                rollout_admission=self._e2_rollout_admission,
                rollout_facts=rollout_facts,
            )
        else:
            dependencies = self.dependencies_factory(command.runtime_context)
        update: dict[str, object] = {
            "subagent_artifacts_backend": self._subagent_artifacts_backend(command),
        }
        control_binding = RunControlContext.current()
        if self._prompt_observation_store is not None and control_binding is not None:
            update["prompt_assembly_observer"] = PromptAssemblyObserver(
                store=self._prompt_observation_store,
                binding=control_binding,
                org_id=run.org_id if run is not None else command.org_id,
                subject_fingerprint=control_binding.snapshot.subject_fingerprint,
                trace_id=command.trace_id,
            )
        # Route `/workspace/<mount>/<path>` reads to the user-granted host
        # folders exposed by the desktop capability broker. Desktop only —
        # `None` (unrouted) on every other backend and when no folders are
        # granted, so those paths stay on the default `StateBackend`.
        if workspace_backend is not None:
            update["workspace_backend"] = workspace_backend
        # The folders the user attached, for the host filesystem rule set. Set
        # even when EMPTY: `()` says "resolved, nothing attached", which is not
        # the same claim as `None` ("nobody resolved") and must not fall back to
        # reading the workspace object.
        if granted_host_roots is not None:
            update["granted_host_roots"] = granted_host_roots
        # The durable, conversation-scoped MCP catalog the model browses at
        # `/mcp/`. Desktop only — `None` composes the in-process store exactly
        # as before. It MUST also be set on the approval-resume path
        # (`ApprovalHandler._dependencies_for_resume`), or a catalog browsed
        # before a write approval is gone when the run resumes: bug R1's shape.
        mcp_catalog_store = AgentScratchWorkerWiring(
            workspace_backend=workspace_backend
        ).mcp_catalog_store(conversation_id=command.conversation_id)
        if mcp_catalog_store is not None:
            update["mcp_catalog_store"] = mcp_catalog_store
        # Route `/large_tool_results/<sha256>` reads to the object store so the
        # supervisor can pull back an offloaded tool result. Desktop only —
        # `None` (unrouted) on every other backend.
        large_tool_results_backend = (
            self._file_store_wiring().large_tool_results_backend()
        )
        if large_tool_results_backend is not None:
            update["large_tool_results_backend"] = large_tool_results_backend
        drafts_backend = self._drafts_backend(
            org_id=command.org_id,
            conversation_id=command.conversation_id,
            run_id=command.run_id,
            user_id=command.runtime_context.user_id,
            emit_event=self._draft_event_emitter(command),
        )
        if drafts_backend is not None:
            update["drafts_backend"] = drafts_backend
        if tool_observation_index.has_observations:
            update["prior_tool_result_loader"] = PriorToolResultLoader(
                tool_observation_index
            )
        # Gated Wave-1 capability tools (Monty code mode, remote sandbox
        # execute). Each is built only when its flag+desktop gate holds and is
        # `None` (unset) otherwise, so non-desktop / disabled runs are
        # byte-identical. The file store backs Monty's snapshot/result stores.
        from runtime_worker.capability_tool_wiring import (  # noqa: PLC0415
            CapabilityToolWiring,
        )

        capability_tools = CapabilityToolWiring(
            runtime_context=command.runtime_context,
            file_store=self._file_backend_store(),
            env=self._capability_env,
            sandbox_tool_factory=self._sandbox_worker_bundle(command.runtime_context),
            rollout_admission=self._e2_rollout_admission,
            rollout_facts=rollout_facts,
        )
        code_mode_tool = capability_tools.code_mode_tool()
        if code_mode_tool is not None:
            update["code_mode_tool"] = code_mode_tool
        sandbox_execute_tool = capability_tools.sandbox_execute_tool()
        if sandbox_execute_tool is not None:
            update["sandbox_execute_tool"] = sandbox_execute_tool
        # PRD-D3 — the gated bulk row-set staging tool. Built only when SURFACES_V2
        # is on (mirroring the A3 emitter gate); `None` otherwise, so the model's
        # tool surface is byte-identical with the flag off.
        stage_rowset_tool = self._stage_rowset_write_tool(
            command,
            run,
            mcp_gateway_services=mcp_gateway_services,
        )
        if stage_rowset_tool is not None:
            update["stage_rowset_write_tool"] = stage_rowset_tool
        publish_artifact_tool = (
            self._publish_artifact_tool(run) if isinstance(run, RunRecord) else None
        )
        if publish_artifact_tool is not None:
            update["publish_artifact_tool"] = publish_artifact_tool
        revise_artifact_tool = (
            self._revise_artifact_tool(run) if isinstance(run, RunRecord) else None
        )
        if revise_artifact_tool is not None:
            update["revise_artifact_tool"] = revise_artifact_tool
        return dependencies.model_copy(update=update)

    @staticmethod
    def _rollout_facts_for_run(run: RunRecord) -> PersistedRunCohortFactsProvider:
        """Copy cohort identity only from the already-verified run record."""

        return PersistedRunCohortFactsProvider(
            org_id=run.org_id,
            user_id=run.user_id,
        )

    def _artifact_admitted(self, *, org_id: str, user_id: str) -> bool:
        """Check the artifact-repository lane before composing its writer."""

        return self._e2_rollout_admission.permits_all(
            capabilities=(RolloutCapability.ARTIFACT_REPOSITORY,),
            facts_provider=PersistedRunCohortFactsProvider(
                org_id=org_id,
                user_id=user_id,
            ),
        )

    def _artifact_publication_enabled(self, run: RunRecord) -> bool:
        return bool(
            self.settings.execution.artifact_effects_v2
            and self.artifact_service is not None
            and self._artifact_admitted(org_id=run.org_id, user_id=run.user_id)
        )

    def _artifact_drafts_enabled(self, *, org_id: str, user_id: str) -> bool:
        return bool(
            self.settings.execution.artifact_effects_v2
            and self.artifact_service is not None
            and self.settings.execution.artifact_drafts_v2
            and self._artifact_admitted(org_id=org_id, user_id=user_id)
        )

    def _drafts_backend(
        self,
        *,
        org_id: str,
        conversation_id: str,
        run_id: str,
        user_id: str,
        emit_event: Callable[[object], Awaitable[None]],
    ) -> object | None:
        """Select one writable draft authority for this run.

        The artifact path deliberately wins only behind the explicit B1 flag.
        Its legacy store parameter is read-through migration only; it never
        writes a ``runtime_drafts`` row.  The old backend remains byte-for-byte
        available while the flag is off.
        """

        from agent_runtime.capabilities.backends import (  # noqa: PLC0415
            ArtifactDraftBackend,
            DraftBackend,
        )

        if self._artifact_drafts_enabled(org_id=org_id, user_id=user_id):
            return ArtifactDraftBackend(
                artifacts=self.artifact_service,
                org_id=org_id,
                conversation_id=conversation_id,
                run_id=run_id,
                user_id=user_id,
                legacy_store=self.draft_store,
            )
        if self.draft_store is None:
            return None
        return DraftBackend(
            store=self.draft_store,
            org_id=org_id,
            conversation_id=conversation_id,
            run_id=run_id,
            user_id=user_id,
            emit_event=emit_event,
        )

    def _operation_context_required(self) -> bool:
        """Every model tool run needs the canonical MCP operation context.

        Rollout modes may hold or stage work, but they are not authority to
        restore a model-facing MCP client path.  Binding the context is inert
        for non-operation tools and lets known reads retain their receipt and
        replay semantics through the one canonical gateway.
        """

        return True

    def _effective_operation_gateway_mode(
        self, services: McpOperationGatewayServices | None
    ) -> OperationGatewayMode:
        """Keep an incomplete rollout on the established path, never half-enforced."""

        configured = self.settings.execution.operation_gateway_mode
        if configured is OperationGatewayMode.ENFORCE and services is None:
            logging.getLogger(__name__).warning(
                "mcp_operation_gateway_unavailable_falling_back",
                extra={"reason": "durable D1 dependencies are incomplete"},
            )
            return OperationGatewayMode.OFF
        return configured

    def _build_mcp_operation_gateway_services(
        self, run: RunRecord
    ) -> McpOperationGatewayServices | None:
        """Compose D1's only model-facing MCP authority for an enforced run.

        All dependencies are deliberately required together. In particular, a
        missing blob/reference store never degrades to an in-memory argument
        cache: the model-facing tool holds work until the cohort is complete.
        """

        if not self._e2_rollout_admission.permits_all(
            capabilities=(
                RolloutCapability.OPERATION_GATEWAY,
                RolloutCapability.MCP_GATEWAY,
                RolloutCapability.EFFECT_STAGER,
                RolloutCapability.EFFECT_COMMIT,
            ),
            facts_provider=self._rollout_facts_for_run(run),
        ):
            return None
        # The construction is shared with the approval-resume path so an approved
        # write re-enters a byte-identical gateway on resume (P1b). The rollout
        # cohort gate above stays here: it is the one-time admission check, and a
        # resumed run was necessarily admitted when it first parked.
        return McpOperationGatewayComposer.compose(
            surfaces_v2=self.settings.execution.surfaces_v2,
            queue=self._queue,
            blobs=self._artifact_blob_store,
            references=self._artifact_reference_store,
            event_producer=self.event_producer,
            run=run,
        )

    def _publish_artifact_tool(self, run: RunRecord) -> PublishArtifactTool | None:
        if not self._artifact_publication_enabled(run):
            return None
        return PublishArtifactTool(
            gateway=OperationGateway(descriptors=DEFAULT_OPERATION_DESCRIPTORS)
        )

    def _revise_artifact_tool(self, run: RunRecord) -> ReviseArtifactTool | None:
        # Gated by the same switch as publication: a run that may create durable
        # artifacts may also change them. Splitting the gates would leave a run
        # able to mint artifacts but not correct them, which is how duplicates
        # accumulate.
        if not self._artifact_publication_enabled(run):
            return None
        return ReviseArtifactTool(
            gateway=OperationGateway(descriptors=DEFAULT_OPERATION_DESCRIPTORS),
            # The gateway's presentation context deliberately withholds the
            # artifact service from execution, so the read that re-bases a lost
            # compare-and-append is injected here rather than traversed out of
            # a context. Without it the tool still refuses the write safely —
            # it just hands the retry back to the model, which is the coin flip
            # this wiring exists to remove.
            content_reader=self.artifact_service,
        )

    async def _process_model_artifact_content(
        self, result: object, *, run: RunRecord
    ) -> None:
        """Use B1's normalized publication path, or preserve A3 observation."""

        if self._artifact_publication_enabled(run):
            await ArtifactContentPartPublisher().publish(result)
            return
        await OperationShadowProbe.observe_model_result(result)

    def _stage_rowset_write_tool(
        self,
        command: RuntimeRunCommand,
        run: object | None,
        *,
        mcp_gateway_services: McpOperationGatewayServices | None = None,
    ) -> object | None:
        """Build the per-run ``stage_rowset_write`` tool, or ``None`` (flag off).

        Wired to the same event producer every emission uses (via
        ``RuntimeStageLedger``), the durable queue (for an allow-always
        auto-apply), and the C1 policy resolver. The stager never touches an MCP
        client — only the shared effect-dispatch path dispatches.
        """

        if not self.settings.execution.surfaces_v2 or run is None:
            return None
        from agent_runtime.api.stage_commit_queue import (  # noqa: PLC0415
            RuntimeStageCommitQueue,
        )
        from agent_runtime.api.stage_ledger import RuntimeStageLedger  # noqa: PLC0415
        from agent_runtime.capabilities.actions.policy import (  # noqa: PLC0415
            ConnectorWritePolicyOverrides,
            EffectiveActionPolicyResolver,
        )
        from agent_runtime.capabilities.tools.builtin.stage_rowset_write import (  # noqa: PLC0415
            StageRowsetWriteTool,
        )
        from agent_runtime.surfaces_v2.rowset_policy import (  # noqa: PLC0415
            RowsetPolicyResolver,
        )
        from agent_runtime.surfaces_v2.stage_rollout import (  # noqa: PLC0415
            StagedWriteRolloutGate,
        )
        from agent_runtime.surfaces_v2.staging import WriteStager  # noqa: PLC0415
        from runtime_worker.rowset_effect_staging import (  # noqa: PLC0415
            RuntimeRowSetEffectProposalPort,
        )

        rc = command.runtime_context
        overrides = ConnectorWritePolicyOverrides.from_user_policies(
            rc.user_policies_json
        )
        if mcp_gateway_services is not None:
            return StageRowsetWriteTool(
                proposal_stager=RuntimeRowSetEffectProposalPort(
                    stager=mcp_gateway_services.stager,
                    scope=mcp_gateway_services.stage_scope,
                    actor=mcp_gateway_services.stage_author,
                    argument_store=mcp_gateway_services.argument_store,
                    connector_overrides=overrides,
                ),
                run=run,
                org_id=command.org_id,
                run_id=command.run_id,
            )
        resolver = EffectiveActionPolicyResolver(
            snapshot=ToolUsePolicyResolver.resolve(rc),
            overrides=overrides,
        )
        stager = WriteStager(
            draft_store=self.draft_store,  # type: ignore[arg-type] — rowsets never touch drafts
            ledger=RuntimeStageLedger(event_producer=self.event_producer),
            rollout_gate=StagedWriteRolloutGate(admission=self._e2_rollout_admission),
            commit_queue=(
                RuntimeStageCommitQueue(queue=self._queue)  # type: ignore[arg-type]
                if self._queue is not None
                else None
            ),
            policy_resolver=RowsetPolicyResolver(resolver=resolver),
        )
        return StageRowsetWriteTool(
            stager=stager,
            run=run,
            org_id=command.org_id,
            run_id=command.run_id,
        )

    def _draft_event_emitter(
        self, command: RuntimeRunCommand
    ) -> "Callable[[object], object]":
        """Build the ``emit_event`` closure DraftBackend uses to emit DRAFT_UPDATED.

        We reuse the existing :class:`RuntimeEventProducer` so every emission
        flows through redaction + presentation projection + the run sequence
        cursor — same path as every other API-authored event.
        """

        from agent_runtime.api.constants import Keys, Values  # noqa: PLC0415
        from agent_runtime.execution.contracts import StreamEventSource  # noqa: PLC0415
        from runtime_api.schemas import RuntimeApiEventType  # noqa: PLC0415

        async def _emit(record: object) -> None:
            # Lazy-attribute access keeps this file decoupled from DraftRecord.
            payload: dict[str, object] = {
                Keys.Field.RUN_ID: command.run_id,
                Keys.Field.CONVERSATION_ID: command.conversation_id,
                "draft_id": getattr(record, "draft_id"),
                "version": getattr(record, "version"),
                "status": getattr(record, "status").value,
                Keys.Field.TITLE: getattr(record, "title"),
                "target_connector": getattr(record, "target_connector", None),
                "target_metadata": getattr(record, "target_metadata", None) or None,
                "citation_ids": list(getattr(record, "citation_ids", ()) or ()),
                Keys.Field.SUMMARY: f"Draft v{getattr(record, 'version')}: "
                f"{getattr(record, 'title') or 'Untitled'}",
            }
            # PRD-E3: the v1 ``message`` surface attach was retired — a
            # ``DRAFT_UPDATED`` payload no longer carries ``surface`` /
            # ``surface_uri``. Draft surfaces render from D1-wave ``write.staged`` /
            # ``revision.added`` ledger events instead.
            run = await self.persistence.get_run(
                org_id=command.org_id, run_id=command.run_id
            )
            if run is None:  # pragma: no cover — terminal-race fallback
                return
            await self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.DRAFT_UPDATED,
                payload=payload,
                summary=str(payload[Keys.Field.SUMMARY]),
                status=Values.Status.COMPLETED,
            )

        return _emit

    async def _tool_observation_index(
        self,
        command: RuntimeRunCommand,
        run: RunRecord,
    ) -> ToolObservationIndex:
        """Load the message history and build a ``ToolObservationIndex`` for the run."""
        records = await self.persistence.list_messages(
            org_id=command.org_id,
            conversation_id=command.conversation_id,
            limit=200,
        )
        selected = self._selected_message_chain(records, run.user_message_id)
        return await self._tool_observation_index_from_selected(command, run, selected)

    async def _tool_observation_index_from_selected(
        self,
        command: RuntimeRunCommand,
        run: RunRecord,
        selected: Sequence[MessageRecord],
    ) -> ToolObservationIndex:
        """Build a ``ToolObservationIndex`` from already-selected messages, sourcing ordinals from the binding store."""
        return await ToolObservationIndexBuilder(
            self.event_store,
            conversation_tool_ordinal_store=self.conversation_tool_ordinal_store,
        ).build(
            org_id=command.org_id,
            conversation_id=command.conversation_id,
            current_run_id=run.run_id,
            selected_messages=selected,
        )

    @classmethod
    def _insert_prior_tool_context(
        cls,
        messages: list[dict[str, str]],
        prompt_context: str,
    ) -> None:
        """Attach prior observations to the current user turn as untrusted data.

        The Deep Agents builder already owns the trusted system prompt. Adding a
        second system message in the conversation violates Anthropic's Messages
        contract and grants external tool/subagent output inappropriate
        instruction priority. Keeping observations in a user turn is
        provider-neutral and preserves their lower trust level.
        """
        for index in range(len(messages) - 1, -1, -1):
            if messages[index][cls._Fields.ROLE] == MessageRole.USER.value:
                user_content = messages[index][cls._Fields.CONTENT]
                messages[index][cls._Fields.CONTENT] = "\n\n".join(
                    (
                        "<application_context "
                        'source="prior_tool_and_subagent_observations">\n'
                        f"{prompt_context}\n"
                        "</application_context>",
                        user_content,
                    )
                )
                return

        # A run always has a current user message, but retain context rather than
        # silently dropping it if a malformed legacy conversation lacks one.
        messages.append(
            {
                cls._Fields.ROLE: MessageRole.USER.value,
                cls._Fields.CONTENT: "\n".join(
                    (
                        "<application_context "
                        'source="prior_tool_and_subagent_observations">',
                        prompt_context,
                        "</application_context>",
                    )
                ),
            }
        )

    @classmethod
    def _message_content_for_runtime(cls, message: MessageRecord) -> str:
        """Build the full string content to pass to the LLM for this message, including quote/attachment context."""
        if message.role is not MessageRole.USER:
            return message.content_text

        sections = [message.content_text]
        quote = cls._quote_context(message.quote)
        if quote is not None:
            sections.append(f"Quoted context:\n{quote}")
        content_parts = cls._content_parts_context(
            message.content,
            message.content_text,
        )
        if content_parts is not None:
            sections.append(f"Structured content:\n{content_parts}")
        attachments = cls._attachments_context(message.attachments)
        if attachments is not None:
            sections.append(f"Attachments:\n{attachments}")
        branch = cls._branch_context(message)
        if branch is not None:
            sections.append(f"Branch metadata:\n{branch}")
        return "\n\n".join(sections)

    @classmethod
    def _quote_context(cls, quote: Mapping[str, object] | None) -> str | None:
        """Format the quoted-text context block, or return ``None`` if empty."""
        if not quote:
            return None
        text = StreamTextHelper.extract(
            quote.get(cls._Fields.TEXT)
        ) or StreamTextHelper.extract(quote.get(cls._Fields.MESSAGE))
        source = StreamTextHelper.extract(
            quote.get("source")
        ) or StreamTextHelper.extract(quote.get("message_id"))
        parts: list[str] = []
        if text is not None:
            parts.append(cls._truncate(text))
        if source is not None:
            parts.append(f"Source: {source}")
        return "\n".join(parts) if parts else None

    @classmethod
    def _content_parts_context(
        cls,
        parts: Sequence[Mapping[str, object]],
        content_text: str,
    ) -> str | None:
        """Summarise structured content parts, excluding text parts that duplicate ``content_text``."""
        summaries: list[str] = []
        normalized_content = content_text.strip()
        for part in parts:
            part_type = StreamTextHelper.extract(part.get(cls._Fields.TYPE)) or "part"
            text = cls._content_text(part)
            if part_type == cls._Fields.TEXT:
                if text is not None and text.strip() != normalized_content:
                    summaries.append(cls._truncate(text))
                continue
            summaries.append(cls._part_summary(part_type, part, text))
        return "\n".join(summary for summary in summaries if summary) or None

    @classmethod
    def _attachments_context(
        cls,
        attachments: Sequence[Mapping[str, object]],
    ) -> str | None:
        """Summarise message attachments as a bullet list, or return ``None`` if there are none."""
        summaries: list[str] = []
        for attachment in attachments:
            name = (
                StreamTextHelper.extract(attachment.get(cls._Fields.NAME))
                or StreamTextHelper.extract(attachment.get(cls._Fields.FILENAME))
                or StreamTextHelper.extract(attachment.get(cls._Fields.ID))
                or "attachment"
            )
            content_type = StreamTextHelper.extract(
                attachment.get(cls._Fields.CONTENT_TYPE)
            ) or StreamTextHelper.extract(attachment.get(cls._Fields.MIME_TYPE))
            text = cls._content_blocks_text(attachment.get(cls._Fields.CONTENT))
            details = cls._details(attachment, content_type=content_type)
            suffix = f" ({details})" if details else ""
            if text is not None:
                summaries.append(f"- {name}{suffix}: {cls._truncate(text)}")
            else:
                summaries.append(f"- {name}{suffix}")
        return "\n".join(summaries) if summaries else None

    @classmethod
    def _branch_context(cls, message: MessageRecord) -> str | None:
        """Return branch/regeneration metadata as a bullet list, or ``None`` if no branch fields are set."""
        fields = {
            cls._Fields.BRANCH_ID: message.branch_id,
            cls._Fields.SOURCE_MESSAGE_ID: message.source_message_id,
        }
        branch = message.metadata.get(cls._Fields.BRANCH)
        if isinstance(branch, Mapping):
            for key in (
                cls._Fields.REGENERATE_FROM_MESSAGE_ID,
                cls._Fields.REPLACE_FROM_MESSAGE_ID,
            ):
                value = StreamTextHelper.extract(branch.get(key))
                if value is not None:
                    fields[key] = value
        regenerate = StreamTextHelper.extract(
            message.metadata.get(cls._Fields.REGENERATE_FROM_MESSAGE_ID)
        )
        if regenerate is not None:
            fields[cls._Fields.REGENERATE_FROM_MESSAGE_ID] = regenerate
        if any(fields.values()) and message.parent_message_id is not None:
            fields[cls._Fields.PARENT_MESSAGE_ID] = message.parent_message_id
        lines = [f"- {key}: {value}" for key, value in fields.items() if value]
        return "\n".join(lines) if lines else None

    @classmethod
    def _part_summary(
        cls,
        part_type: str,
        part: Mapping[str, object],
        text: str | None,
    ) -> str:
        """Format a single content part as a summary line (type, name, details, truncated text)."""
        name = StreamTextHelper.extract(
            part.get(cls._Fields.FILENAME)
        ) or StreamTextHelper.extract(part.get(cls._Fields.NAME))
        details = cls._details(
            part, content_type=StreamTextHelper.extract(part.get(cls._Fields.MIME_TYPE))
        )
        title = f"- {part_type}"
        if name is not None:
            title = f"{title} {name}"
        if details:
            title = f"{title} ({details})"
        if text is not None:
            return f"{title}: {cls._truncate(text)}"
        return title

    @classmethod
    def _details(
        cls,
        payload: Mapping[str, object],
        *,
        content_type: str | None,
    ) -> str:
        """Build a parenthetical detail string (content type, size, file_id, url) for a part or attachment."""
        details: list[str] = []
        if content_type is not None:
            details.append(content_type)
        size = payload.get(cls._Fields.SIZE)
        if isinstance(size, int):
            details.append(f"{size} bytes")
        file_id = StreamTextHelper.extract(payload.get(cls._Fields.FILE_ID))
        if file_id is not None:
            details.append(f"file_id={file_id}")
        url = StreamTextHelper.extract(payload.get(cls._Fields.URL))
        if url is not None:
            details.append(f"url={url}")
        return ", ".join(details)

    @classmethod
    def _content_text(cls, payload: Mapping[str, object]) -> str | None:
        """Extract plain text from a content-part dict, trying ``text``, ``content``, then block sequences."""
        return (
            StreamTextHelper.extract(payload.get(cls._Fields.TEXT))
            or StreamTextHelper.extract(payload.get(cls._Fields.CONTENT))
            or cls._content_blocks_text(payload.get(cls._Fields.CONTENT))
        )

    @classmethod
    def _content_blocks_text(cls, value: object) -> str | None:
        """Recursively extract plain text from a string, mapping, or sequence of content blocks."""
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, Mapping):
            return StreamTextHelper.extract(
                value.get(cls._Fields.TEXT)
            ) or StreamTextHelper.extract(value.get(cls._Fields.CONTENT))
        if not isinstance(value, Sequence) or isinstance(
            value,
            (str, bytes, bytearray),
        ):
            return None
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, Mapping):
                text = cls._content_text(item)
                if text is not None:
                    parts.append(text)
        text = "\n".join(part.strip() for part in parts if part.strip()).strip()
        return text or None

    @classmethod
    def _truncate(cls, value: str) -> str:
        """Truncate ``value`` to ``MAX_STRUCTURED_CONTEXT_CHARS`` characters, appending ``[truncated]`` if cut."""
        if len(value) <= MAX_STRUCTURED_CONTEXT_CHARS:
            return value
        return f"{value[:MAX_STRUCTURED_CONTEXT_CHARS].rstrip()} [truncated]"

    @classmethod
    def _selected_message_chain(
        cls,
        records: Sequence[MessageRecord],
        user_message_id: str,
    ) -> tuple[MessageRecord, ...]:
        """Return the chain of messages leading to ``user_message_id``, following parent links."""
        run_user = next(
            (message for message in records if message.message_id == user_message_id),
            None,
        )
        if run_user is None:
            return tuple(records)
        by_id = {message.message_id: message for message in records}
        selected_ids: set[str] = set()
        current: MessageRecord | None = run_user
        while current is not None:
            selected_ids.add(current.message_id)
            parent_id = current.parent_message_id
            current = by_id.get(parent_id) if parent_id is not None else None
        if run_user.parent_message_id is None:
            return tuple(
                message
                for message in records
                if message.created_at <= run_user.created_at
            )
        return tuple(
            message for message in records if message.message_id in selected_ids
        )

    async def _stream_runtime(
        self,
        command: RuntimeRunCommand,
        run: RunRecord,
        harness: RuntimeHarness,
        messages: Sequence[object],
        metrics: AssistantRunMetrics,
    ) -> object:
        """Stream the LangGraph run under a timeout and return the composed final result."""
        async with asyncio.timeout(
            command.runtime_context.model_profile.timeout_seconds
        ):
            result = await StreamingExecutor.run(
                stream=self.runtime_streamer(harness, messages),
                run=run,
                metrics=metrics,
                event_store=self.event_store,
                event_producer=self.event_producer,
                stream_event_mapper=self.stream_event_mapper,
                track_subagents=True,
                citation_pipeline=CitationStreamPipeline.for_provider(
                    command.runtime_context.model_profile.provider
                ),
                # The resolver was bound by the run-level try-block; the
                # executor pulls it from the active ContextVar via the
                # same mechanism every other bound capability uses.
                citation_resolver=CitationResolver.active(),
                # Opt-in coalesce window for MODEL_DELTA batching; default
                # 0 (disabled).
                delta_coalesce_window_ms=self.settings.execution.delta_coalesce_window_ms,
                delta_coalesce_max_chunks=self.settings.execution.delta_coalesce_max_chunks,
                # PRD-A2 D5a — gate mid-run ``usage.recorded`` emission on the
                # v2 flag; off ⇒ byte-identical stream.
                surfaces_v2_enabled=self.settings.execution.surfaces_v2,
            )
        return StreamingExecutor.compose_final(result)

    @classmethod
    def _is_action_interrupt(cls, result: object) -> bool:
        """Return ``True`` if the result signals a pending approval or interrupt."""
        interrupts = getattr(result, cls._Fields.INTERRUPTS, None)
        if interrupts:
            return True
        return isinstance(result, Mapping) and (
            result.get(cls._Fields.ACTION_REQUIRED) is True
            or result.get(cls._Fields.APPROVAL_REQUESTED) is True
            or bool(result.get(cls._Fields.INTERRUPTS))
        )

    async def _reconcile_inflight_tool_calls(
        self,
        run: RunRecord,
        *,
        outcome: ToolOutcome,
        error_code: ToolErrorCode,
    ) -> None:
        """Settle every in-flight tool call before the run terminates.

        On run-level failure paths (asyncio.timeout, unhandled exception),
        any tool call still in `tool_call_started` without a matching
        `tool_result` would leave a "Running" card stuck on the client.
        We synthesize a terminal `tool_result` + `tool_call_completed`
        event for each, in started-order, BEFORE emitting `run_failed`
        so SSE consumers see lifecycle terminate top-down.

        Failures inside this loop are logged but never raised — the caller
        is already on a failure path and reconciliation is best-effort. A
        partial reconciliation is still strictly better than none.
        """

        ledger = self.stream_event_mapper.message_processor.ledger_for_run(run.run_id)
        unsettled = ledger.unsettled()
        if not unsettled:
            return
        _, error_summary = _ErrorMessage.for_code(error_code.value)
        for entry in unsettled:
            try:
                payload: dict[str, object] = {
                    "tool_name": entry.tool_name,
                    "call_id": entry.call_id,
                    "status": outcome.value,
                    "error_code": error_code.value,
                    "error_message": error_summary,
                }
                await self.event_producer.append_api_event(
                    run=run,
                    source=StreamEventSource.SYSTEM,
                    event_type=RuntimeApiEventType.TOOL_RESULT,
                    payload=payload,
                    parent_task_id=entry.parent_task_id,
                    subagent_id=entry.subagent_id,
                )
                await self.event_producer.append_api_event(
                    run=run,
                    source=StreamEventSource.SYSTEM,
                    event_type=RuntimeApiEventType.TOOL_CALL_COMPLETED,
                    payload={
                        "tool_name": entry.tool_name,
                        "call_id": entry.call_id,
                        "status": outcome.value,
                        "error_code": error_code.value,
                    },
                    parent_task_id=entry.parent_task_id,
                    subagent_id=entry.subagent_id,
                )
                ledger.observed_settled(entry.call_id)
                # Close the DURABLE row too. Emitting the synthetic events
                # without this is what left a finished run holding invocations
                # still marked ``running``, with no record of the failure that
                # ended them — so the one store built to explain a failed tool
                # call explained nothing.
                await self.stream_event_mapper.message_processor.close_tool_invocation(
                    run=run,
                    call_id=entry.call_id,
                    **ToolInvocationOutcome.from_result_payload(payload),
                )
            except Exception:
                logging.getLogger(__name__).warning(
                    "tool_call_reconcile.failed run=%s call_id=%s outcome=%s",
                    run.run_id,
                    entry.call_id,
                    outcome.value,
                    exc_info=True,
                )

    async def _build_tool_budget_guard(
        self,
        run: RunRecord,
        *,
        task_policy_binding: TaskPolicyRuntimeBinding | None = None,
    ) -> ToolBudgetGuard | None:
        """Load the org's per-tool budgets and build a per-run guard.

        Returns ``None`` when the persistence port doesn't expose the
        method yet (older test stubs) or when the org has neither
        configured rows nor a workspace override. Reuses the per-run
        :class:`ToolCallLedger` already maintained by the stream
        orchestrator so admission decisions and the
        ``tool_call_started``/``tool_result`` reconciler share state.

        The workspace's Settings → Model & behavior cap is layered over
        the configured rows, so the user-facing number governs the run
        without a second budget store to keep in sync.
        """

        admission = self._file_store_wiring().tool_result_admission()
        loader = getattr(self.persistence, "list_tool_budgets_for_org", None)
        budgets: Sequence[ToolBudgetRecord] = ()
        if loader is not None:
            try:
                budgets = await loader(org_id=run.org_id)
            except Exception:
                logging.getLogger(__name__).warning(
                    "tool_budget_load_failed", exc_info=True
                )
                # Desktop admission is a hard model-context boundary and must
                # not disappear because optional budget policy I/O failed.
                budgets = ()
            else:
                budgets = WorkspaceToolBudgetOverride.apply(
                    budgets,
                    max_calls_per_run=await self._workspace_tool_call_cap(run),
                )
        if not budgets and admission is None and task_policy_binding is None:
            return None
        ledger = self.stream_event_mapper.message_processor.ledger_for_run(run.run_id)
        return ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(budgets),
            ledger=ledger,
            run=run,
            event_producer=self.event_producer,
            task_policy_binding=task_policy_binding,
            tool_result_admission=admission,
        )

    async def _workspace_tool_call_cap(self, run: RunRecord) -> int | None:
        """Return the workspace's per-tool call cap, or ``None`` when unset.

        Fails soft: a store that predates the accessor, a missing row, or a
        read error all mean "no workspace preference", which leaves the
        deployment's configured budgets in charge. A settings lookup must
        never be the reason a run cannot start.
        """

        loader = getattr(self.persistence, "get_workspace_defaults", None)
        if loader is None:
            return None
        try:
            record = await loader(org_id=run.org_id)
        except Exception:
            logging.getLogger(__name__).warning(
                "workspace_tool_call_cap_load_failed", exc_info=True
            )
            return None
        if record is None:
            return None
        return record.behavior_overrides.tool_calls_per_run

    def _bind_citation_ledger(self, run: RunRecord) -> CitationLedger | None:
        """Build a per-run :class:`CitationLedger`, or ``None`` when disabled.

        The ledger is the single seam for tools, provider adapters, and replay
        paths. We tag emitted events with ``StreamEventSource.TOOL`` because
        the typical producer is a tool result; provider-native passthroughs
        (Anthropic, OpenAI) reuse the same source — citations are activity
        on the tool/source axis regardless of who surfaced the document.
        """

        if self.citation_store is None:
            return None
        return CitationLedger(
            run=run,
            store=self.citation_store,
            producer=self.event_producer,
            source=StreamEventSource.TOOL,
        )

    def _build_surface_generation_scheduler(
        self, run: RunRecord, *, credentials: ShapingCredentials
    ) -> SurfaceGenerationScheduler | None:
        """Build a run-scoped surface-spec generation scheduler, or ``None``.

        Returns ``None`` (generation disabled) unless ``SURFACE_SPEC_MODEL`` is
        set — the factory owns that gate. The store is selected by
        ``SURFACE_SPEC_STORE_BACKEND`` (``memory`` default test, ``file`` desktop
        single-user, ``backend`` team/web); an unset value preserves the prior
        auto behaviour (durable file store when configured, else in-process).
        The emit callback ships ``surface_spec_generated`` back onto the same
        event producer every other emission uses, so the FE upgrades the surface
        in place.

        ``credentials`` is required, not optional: the refinement model is a
        second outbound call on the run's provider and ``extra_kwargs`` is the
        only channel a BYOK key travels on. Omitting it built the model with no
        credential on every packaged install, which is how refinement was dark.
        """

        import os  # noqa: PLC0415 - local to keep the module import surface small

        def _surface_store() -> SurfaceSpecStorePort:
            return build_surface_spec_store(
                environ=os.environ,
                org_id=run.org_id,
                user_id=run.user_id,
            )

        async def _emit(payload: Mapping[str, object]) -> None:
            await self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.SYSTEM,
                event_type=RuntimeApiEventType.SURFACE_SPEC_GENERATED,
                summary="Prepared a view",
                payload=dict(payload),
            )
            # Generative Surfaces v2 (PRD-A3 D4, Hook 2): the async spec upgrade
            # is a second, generated-basis ``view.derived`` on the ledger. v1
            # first, v2 second (additive). No-op unless SURFACES_V2 bound an
            # emitter; the generation task captured the ContextVar at schedule
            # time (during the tool call, while the emitter is bound).
            emitter = WorkLedgerEmitter.active()
            if emitter is not None:
                await emitter.on_spec_generated(payload=payload)

        # PRD-A2 D5b — the spec-generation path is otherwise unmetered. Bind a
        # per-run VIEW_SHAPING meter: it writes a usage row per attempt (real
        # per-attempt spend) and, when SURFACES_V2 is on, emits usage.recorded.
        async def _emit_usage(payload: Mapping[str, object]) -> None:
            await self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.MODEL,
                event_type=RuntimeApiEventType.USAGE_RECORDED,
                payload=dict(payload),
            )

        meter = UsageMeter(
            recorder=self.usage_recorder,
            emit_event=_emit_usage,
            surfaces_v2=self.settings.execution.surfaces_v2,
            attribution_edge_store=self.persistence,
        )
        invocation = MeteredModelInvocation(
            meter=meter, run=run, purpose=Purpose.VIEW_SHAPING
        )

        try:
            return build_surface_generation_scheduler(
                store=_surface_store(),
                emit=_emit,
                environ=os.environ,
                usage_meter=invocation,
                # PRD-B3 shaping-on default: the run's provider drives the cheapest
                # shaping model when SURFACE_SPEC_MODEL is unset and SURFACES_V2 is on.
                run_provider=run.model_provider,
                credentials=credentials,
            )
        except Exception:  # noqa: BLE001
            # PRD-E3: with SURFACES_V2 default-on, this build now runs for every
            # run. Constructing the shaping chat model can raise (e.g. no
            # resolvable provider key). Spec generation is a display-only upgrade —
            # degrade to no-generation (generic views still render via the honest
            # ladder) rather than crash the run.
            logging.getLogger(__name__).warning(
                "[surfaces] run.generation_scheduler_unavailable run=%s",
                run.run_id,
                exc_info=True,
            )
            return None

    def _build_read_path_shaper(
        self, run: RunRecord, *, credentials: ShapingCredentials
    ) -> ReadPathShaper | None:
        """Build a run-scoped read-path shaper (ladder rung 5), or ``None``.

        Same gate as the refinement scheduler (``ShapingModelResolver``), the
        same required ``credentials`` (see that method), and the same fail-soft
        posture: constructing the shaping chat model can raise (no resolvable
        provider key), and a display-only rung must degrade to "off" rather than
        crash the run. Metered as ``view_shaping``, like every other shaping
        call, so one purpose covers the whole subsystem's spend.
        """

        import os  # noqa: PLC0415 - local to keep the module import surface small

        async def _emit_usage(payload: Mapping[str, object]) -> None:
            await self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.MODEL,
                event_type=RuntimeApiEventType.USAGE_RECORDED,
                payload=dict(payload),
            )

        try:
            return build_read_path_shaper(
                environ=os.environ,
                run_provider=run.model_provider,
                credentials=credentials,
                usage_meter=MeteredModelInvocation(
                    meter=UsageMeter(
                        recorder=self.usage_recorder,
                        emit_event=_emit_usage,
                        surfaces_v2=self.settings.execution.surfaces_v2,
                        attribution_edge_store=self.persistence,
                    ),
                    run=run,
                    purpose=Purpose.VIEW_SHAPING,
                ),
            )
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "[surfaces] run.read_path_shaper_unavailable run=%s",
                run.run_id,
                exc_info=True,
            )
            return None

    def _build_work_ledger_emitter(self, run: RunRecord) -> WorkLedgerEmitter | None:
        """Build a run-scoped Work Ledger emitter, or ``None`` (PRD-A3 D4).

        Returns ``None`` unless ``SURFACES_V2`` is on — gated on the same
        settings value A2 threads, so binding is byte-identical to flag-off when
        off. Its :data:`EmitFn` closure maps a ledger event-type *value* (an A1
        ``LedgerEventType`` string) to the wire enum by value — both enums carry
        identical values (e.g. ``"action.classified"``) — and appends through the
        same event producer every other emission uses.
        """

        if not self.settings.execution.surfaces_v2:
            return None

        async def _emit(
            event_type_value: str,
            payload: Mapping[str, object],
            summary: str | None,
        ) -> None:
            await self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.SYSTEM,
                event_type=RuntimeApiEventType(str(event_type_value)),
                summary=summary,
                payload=dict(payload),
            )

        return WorkLedgerEmitter(emit=_emit)

    def _build_operation_ledger_emitter(
        self,
        run: RunRecord,
        *,
        external_effect_tracker: ModelInvocationEffectTracker | None = None,
    ) -> OperationEventEmitterAdapter:
        """Bind v2.1 operation rows to the existing append-only run transport."""

        async def _emit(
            event_type_value: str,
            payload: Mapping[str, object],
            summary: str | None,
        ) -> None:
            event_type = RuntimeApiEventType(event_type_value)
            await self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.SYSTEM,
                event_type=event_type,
                summary=summary,
                payload=dict(payload),
            )
            if external_effect_tracker is not None:
                external_effect_tracker.mark_event(event_type)

        return OperationEventEmitterAdapter(emit_fn=_emit)

    async def _bind_conversation_ordinal_allocator(
        self,
        command: RuntimeRunCommand,
        run: RunRecord,
    ) -> ConversationOrdinalAllocator:
        """Build the per-conversation ordinal allocator seeded from the persistent binding store.

        Falls back to a memory-only allocator when no store is configured; citations
        degrade to absent for that run rather than crashing the dispatch path.
        """

        if self.conversation_tool_ordinal_store is None:
            logging.getLogger(__name__).info(
                "[citations] run.allocator_no_store conv=%s run=%s — "
                "memory-only allocator (replay/eval fallback)",
                command.conversation_id,
                run.run_id,
            )
            return ConversationOrdinalAllocator(
                org_id=command.org_id,
                conversation_id=command.conversation_id,
                run_id=run.run_id,
            )
        return await ConversationOrdinalAllocator.for_conversation(
            org_id=command.org_id,
            conversation_id=command.conversation_id,
            run_id=run.run_id,
            store=self.conversation_tool_ordinal_store,
        )

    def _bind_citation_resolver(
        self,
        run: RunRecord,
        allocator: ConversationOrdinalAllocator,
    ) -> CitationResolver:
        """Build the per-run :class:`CitationResolver`.

        Tagged with ``StreamEventSource.MODEL`` because the marker that
        produces a ``citation_made`` event lives in the model's
        streamed text — the resolver is observing the model's output,
        not a tool's. The cited tool invocation is referenced by
        ``link.source_tool_call_id`` in the payload.
        """

        return CitationResolver(
            run=run,
            allocator=allocator,
            producer=self.event_producer,
            source=StreamEventSource.MODEL,
        )

    def _bind_mcp_discovery_service(
        self,
        *,
        run: RunRecord,
        runtime_context: AgentRuntimeContext,
        dependencies: RuntimeDependencies,
    ) -> McpDiscoveryService:
        """Build a per-run :class:`McpDiscoveryService`.

        The service mirrors the citation ledger: bound to the worker run
        once, exposed through a class-method (``offer``) so the
        ``suggest_mcp_connector`` tool reaches it without a runtime context
        in its signature. The auth-session creator (when registered with
        the MCP registry) is reused so the discovery card carries the same
        ``auth_url`` / ``expires_at`` fields the blocking gate emits.
        """

        auth_session_creator = None
        for provider in getattr(dependencies.mcp_registry, "providers", ()):
            if callable(getattr(provider, "create_auth_session", None)):
                auth_session_creator = provider
                break
        return McpDiscoveryService(
            run=run,
            runtime_context=runtime_context,
            producer=self.event_producer,
            audit_emitter=self.audit_emitter,
            registry=dependencies.mcp_registry,
            auth_session_creator=auth_session_creator,
        )

    async def _append_lifecycle(
        self,
        run: RunRecord,
        event_type: RuntimeApiEventType,
        summary: str,
        *,
        source: StreamEventSource = StreamEventSource.SYSTEM,
        payload: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Emit a lifecycle event (e.g., ``RUN_STARTED``, ``FINAL_RESPONSE``) via the event producer."""
        await self.event_producer.append_api_event(
            run=run,
            source=source,
            event_type=event_type,
            summary=summary,
            status="completed"
            if event_type == RuntimeApiEventType.FINAL_RESPONSE
            else None,
            payload=payload or {self._Fields.STATUS: event_type.value},
            metadata=metadata,
        )

    async def _append_model_call_started(
        self,
        run: RunRecord,
        metrics: AssistantRunMetrics,
        messages: Sequence[Mapping[str, object]],
    ) -> None:
        """Mark the boundary between local prompt build and the LLM call.

        Splits the previously opaque `run_started → first model_delta` gap into
        prompt-build cost (`prompt_build_ms`) versus LangGraph + network + LLM
        TTFT (which is then `t(model_delta) - t(model_call_started)`).
        """

        now = datetime.now(timezone.utc)
        prompt_build_ms = max(
            0, round((now - metrics.started_at).total_seconds() * 1000)
        )
        prompt_chars = sum(
            len(message.get(self._Fields.CONTENT) or "")
            for message in messages
            if isinstance(message.get(self._Fields.CONTENT), str)
        )
        await self._append_lifecycle(
            run,
            RuntimeApiEventType.MODEL_CALL_STARTED,
            "Model call started",
            payload={
                self._Fields.STATUS: (RuntimeApiEventType.MODEL_CALL_STARTED.value),
                "prompt_build_ms": prompt_build_ms,
                "message_count": len(messages),
                "prompt_chars": prompt_chars,
            },
        )

    @classmethod
    def _extract_final_text(cls, result: object) -> str | None:
        """Extract a best-effort assistant response from common LangChain result shapes."""

        if result is None:
            return None
        if isinstance(result, str):
            return result.strip() or None
        if isinstance(result, dict):
            for key in (
                cls._Fields.FINAL_RESPONSE,
                cls._Fields.RESPONSE,
                cls._Fields.OUTPUT,
                cls._Fields.CONTENT,
            ):
                text = StreamTextHelper.extract(result.get(key))
                if text is not None:
                    return text
            messages = result.get(cls._Fields.MESSAGES)
            if isinstance(messages, Sequence):
                for message in reversed(messages):
                    text = cls._message_content(message)
                    if text is not None:
                        return text
        return cls._message_content(result)

    @classmethod
    def _message_content(cls, message: object) -> str | None:
        """Extract the ``content`` field from a message object or mapping."""
        if isinstance(message, Mapping):
            return cls._content_to_text(message.get(cls._Fields.CONTENT))
        return cls._content_to_text(getattr(message, cls._Fields.CONTENT, None))

    @classmethod
    def _content_to_text(cls, value: object) -> str | None:
        """Convert a raw content value (string, list of blocks, or mapping) to a plain text string."""
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, Mapping):
                    text = item.get(cls._Fields.TEXT) or item.get(cls._Fields.CONTENT)
                    if isinstance(text, str):
                        parts.append(text)
            text = "".join(parts).strip()
            return text or None
        return None

    @classmethod
    def _trace_text(cls, context: AgentRuntimeContext, key: str) -> str | None:
        """Return the string value of ``key`` from ``context.trace_metadata``, or ``None`` if absent or blank."""
        value = context.trace_metadata.get(key)
        return value if isinstance(value, str) and value.strip() else None


def _runtime_context_matches_persisted_run(
    context: AgentRuntimeContext, run: RunRecord
) -> bool:
    """Require the queued execution context to be the run's persisted scope."""

    persisted = run.runtime_context
    return (
        persisted is not None
        and context.run_id == run.run_id == persisted.run_id
        and context.org_id == run.org_id == persisted.org_id
        and context.user_id == run.user_id == persisted.user_id
    )


def _termination_reason_for(exc: BaseException) -> TerminationReason:
    """Map a caught run-fatal exception to its TerminationReason.

    Keeps the run handler's exception block free of branching: every
    typed :class:`RunFatalToolError` subclass picks the matching reason;
    everything else falls back to the generic ``EXECUTION_ERROR``.
    """

    if isinstance(exc, BudgetExceeded):
        return TerminationReason.BUDGET_EXCEEDED
    if isinstance(exc, (AuthDenied, TenantIsolationViolation)):
        return TerminationReason.TOOL_FATAL_ERROR
    if isinstance(exc, RunFatalToolError):
        return TerminationReason.TOOL_FATAL_ERROR
    return TerminationReason.EXECUTION_ERROR
