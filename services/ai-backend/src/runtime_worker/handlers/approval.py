"""Queued approval-resolution command handling."""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone

from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.api.constants import Values as ApiValues
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_termination import (
    RunTerminationCoordinator,
    TerminalRunObserverPort,
    TerminationReason,
)
from agent_runtime.api.presentation import ToolDisplayLookupContext
from agent_runtime.api.user_policies_resolver import (
    ProviderKeysHydrator,
    UserPoliciesResolver,
)
from agent_runtime.capabilities.mcp.descriptor_registry import (
    McpDisplayRegistryContext,
)
from agent_runtime.capabilities.mcp.gateway_context import McpOperationGatewayContext
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationEventEmitterAdapter,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from runtime_worker.mcp_operation_storage import McpOperationGatewayComposer
from agent_runtime.capabilities.operations.probes import OperationShadowProbe
from agent_runtime.capabilities.operations.catalog import DEFAULT_OPERATION_DESCRIPTORS
from agent_runtime.capabilities.operations.gateway import OperationGateway
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
from agent_runtime.capabilities.tools.tool_use_enforcement import (
    ToolUsePolicyResolver,
)
from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate
from agent_runtime.capabilities.tool_budget_guard import ToolBudgetGuard
from agent_runtime.capabilities.tool_budget_middleware import (
    ToolBudgetMiddleware,
    WorkspaceToolBudgetOverride,
)
from agent_runtime.budgets import BudgetCharger
from agent_runtime.control_plane.context import (
    RunControlBinding,
    TaskPolicyRuntimeBinding,
)
from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.capabilities.citation_resolver import CitationResolver
from agent_runtime.capabilities.conversation_ordinals import (
    ConversationOrdinalAllocator,
)
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
    StreamEventSource,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.model_invocation.journal import ModelInvocationStorePort
from agent_runtime.prompts.observation import (
    PromptAssemblyObserver,
    PromptObservationStorePort,
)
from agent_runtime.execution.factory import (
    RuntimeHarness,
    acreate_agent_runtime,
)
from agent_runtime.execution.providers.citation_pipeline import CitationStreamPipeline
from agent_runtime.execution.runtime import (
    astream_runtime_resume,
    is_native_interrupt_id,
)
from agent_runtime.persistence import with_optimistic_retry
from agent_runtime.persistence.ports import (
    ConversationToolOrdinalStorePort,
    DraftOwnershipConflict,
)
from agent_runtime.persistence.records import (
    BatchOutcomeStatus,
    BatchTransitionOutcome,
    ToolBudgetRecord,
)
from agent_runtime.observability.usage_recorder import (
    NullUsageRecorder,
    UsageRecorder,
)
from agent_runtime.settings import RuntimeSettings
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    MessageRecord,
    MessageRole,
    RuntimeApiEventType,
    RuntimeApprovalResolvedCommand,
    RunRecord,
)
from runtime_worker.audit import WorkerAuditEmitter
from runtime_worker.dependencies import (
    DefaultRuntimeDependenciesFactory,
)
from runtime_worker.agent_scratch_wiring import AgentScratchWorkerWiring
from runtime_worker.file_store_wiring import FileStoreWorkerWiring
from runtime_worker.turn_content import AssistantTurnContent
from runtime_worker.handlers.run import RuntimeRunHandler
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
from runtime_worker.workspace_backend_wiring import WorkspaceBackendWorkerWiring

RuntimeDependenciesFactory = Callable[[AgentRuntimeContext], RuntimeDependencies]
# Sync- or async-returning. Default is the async ``acreate_agent_runtime`` so
# the worker's event loop is not blocked by the registry-listing HTTP calls
# inside the factory; tests injecting sync fakes (``lambda **_: _FakeHarness()``)
# continue to work because the call site awaits via ``inspect.isawaitable``.
AgentFactory = Callable[..., RuntimeHarness | Awaitable[RuntimeHarness]]
# ``interrupt_id`` is keyword-only and optional: it names the native LangGraph
# interrupt the decision answers, and is REQUIRED in practice whenever a run can
# hold more than one pending interrupt (LangGraph refuses an ambiguous resume).
RuntimeResumer = Callable[..., AsyncIterator[object]]

# Discriminator written into ``approval.metadata['kind']`` by the draft-send path so
# this handler routes draft-send approvals through their own resolution path instead of
# the LangGraph resume path.
_APPROVAL_KIND_DRAFT_SEND = "draft_send"

_AUDIT_DRAFT_SEND_COMPLETED = "draft.send.completed"
_AUDIT_DRAFT_SEND_REJECTED = "draft.send.rejected"

_LOGGER = logging.getLogger("runtime_worker.approval")


class RuntimeApprovalHandler:
    """Consume durable approval-resolution commands after the API records the decision."""

    class _Fields:
        APPROVAL_KIND = "approval_kind"
        NATIVE_INTERRUPT_ID = "native_interrupt_id"
        # Stamped from the same interrupt id as ``native_interrupt_id`` (the
        # batch is the interrupt's 1:1 persistence projection).
        BATCH_ID = "batch_id"
        APPROVAL_ID = "approval_id"
        ANSWER = "answer"
        DECISION = "decision"
        DECISIONS = "decisions"
        TYPE = "type"
        STATUS = "status"
        MESSAGE = "message"
        # Set on approval.metadata when the interrupt fired inside a subagent's
        # subgraph. Drives the paired ``SUBAGENT_RESUMED`` emit on resolution so
        # the FE flips the row's status back to ``running`` before the next
        # progress event arrives.
        PARENT_TASK_ID = "parent_task_id"
        REASON = "reason"
        TASK_ID = "task_id"

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        dependencies_factory: RuntimeDependenciesFactory | None = None,
        settings: RuntimeSettings | None = None,
        agent_factory: AgentFactory = acreate_agent_runtime,
        runtime_resumer: RuntimeResumer = astream_runtime_resume,
        on_event_appended: Callable[[str], None] | None = None,
        draft_store: object | None = None,
        conversation_tool_ordinal_store: (
            ConversationToolOrdinalStorePort | None
        ) = None,
        mcp_discovery_cache: object | None = None,
        user_policies_resolver: UserPoliciesResolver | None = None,
        artifact_service: object | None = None,
        # P1b: the durable stores + queue the model-facing MCP operation gateway
        # needs. On resume they let an approved write re-enter the same gateway
        # and EXECUTE in this run; absent (the pre-P1b default), the resumed tool
        # holds instead of dispatching, exactly as before this wiring.
        queue: object | None = None,
        artifact_blob_store: object | None = None,
        artifact_reference_store: object | None = None,
        run_control_builder: RunControlPlaneBuilder | None = None,
        prompt_observation_store: PromptObservationStorePort | None = None,
        run_control_decision_store: object | None = None,
        model_invocation_store: ModelInvocationStorePort | None = None,
        model_invocation_composer: ModelInvocationWorkerComposer | None = None,
        usage_recorder: UsageRecorder | None = None,
        model_invocation_terminal: ModelInvocationTerminalIntegration | None = None,
        terminal_run_observer: TerminalRunObserverPort | None = None,
        workspace_broker_http_client: object | None = None,
    ) -> None:
        self.persistence: PersistencePort = persistence
        self.event_store: EventStorePort = event_store
        # Same projection object as ``RuntimeRunHandler``: both terminal paths
        # write the assistant message, and one fold rule serves both.
        self._turn_content = AssistantTurnContent(self.event_store)
        # Test/extension seam for the desktop capability broker, mirroring
        # ``RuntimeRunHandler``. ``None`` in production leaves the wiring on the
        # process-shared loopback pool.
        self._workspace_broker_http_client = workspace_broker_http_client
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
        self.usage_recorder: UsageRecorder = usage_recorder or NullUsageRecorder()
        self._model_invocation_terminal = (
            model_invocation_terminal
            or ModelInvocationTerminalIntegration(
                journal=model_invocation_store,
                usage_recorder=self.usage_recorder,
                persistence=self.persistence,
            )
        )
        self._budget_charger = BudgetCharger(self.persistence)
        self._e2_rollout_admission = E2RolloutAdmission(
            resolution=self.settings.execution.rollout,
            cohorts=self.settings.execution.rollout_cohorts,
            kill_switches=self.settings.execution.rollout_kill_switches,
        )
        self.artifact_service = artifact_service
        self._queue = queue
        self._artifact_blob_store = artifact_blob_store
        self._artifact_reference_store = artifact_reference_store
        self._run_control_builder = run_control_builder
        self._prompt_observation_store = prompt_observation_store
        # BYOK re-hydration on resume: the persisted run record's context was
        # serialized without ``provider_keys`` (excluded field), so the resumed
        # harness re-fetches them in memory only — same seam as the run handler.
        self._provider_keys_hydrator = (
            ProviderKeysHydrator(resolver=user_policies_resolver)
            if user_policies_resolver is not None
            else None
        )
        # Single source of truth for the desktop file-store gate shared with the
        # run handler. On non-file backends every method returns ``None`` so the
        # resume path stays byte-identical to before (offloader ``None`` → inline).
        # Built before the dependency factory because the F3 schema-artifact
        # writer is read off it.
        self._file_store_wiring = FileStoreWorkerWiring(self.event_store)
        # Same pattern as ``RuntimeRunHandler``: caller-supplied factory wins
        # (tests inject their own); otherwise the default factory threads the
        # process-wide MCP discovery cache through ``RuntimeDependencies``, and
        # the same F3 composer the run path uses, so a resumed run's bridge is
        # wired identically to the one its first turn had.
        self.dependencies_factory = dependencies_factory or (
            DefaultRuntimeDependenciesFactory(
                self.settings,
                mcp_discovery_cache=mcp_discovery_cache,  # type: ignore[arg-type]
            )
        )
        self.agent_factory = agent_factory
        self.runtime_resumer = runtime_resumer
        self.event_producer = RuntimeEventProducer(
            persistence=self.persistence,
            event_store=self.event_store,
            on_event_appended=on_event_appended,
        )
        self.run_termination = RunTerminationCoordinator(
            event_producer=self.event_producer,
            terminal_observer=terminal_run_observer,
        )
        # Mirror the run handler: on the desktop file store, oversized tool
        # output produced *after* an approval is offloaded to the object store
        # instead of persisted inline in ``events.jsonl``. ``None`` everywhere
        # else keeps the historical inline behavior.
        self.stream_event_mapper = StreamOrchestrator(
            self.event_producer,
            tool_result_offloader=self._file_store_wiring.tool_result_offloader(),
        )
        self.audit_emitter = WorkerAuditEmitter(persistence=self.persistence)
        # Required for draft-send approvals; absent on unit-test construction.
        # Without it, draft-send approvals skip status transitions rather than crashing.
        self._draft_store = draft_store
        # Bound at construction so the resumed allocator is rebuilt from the
        # persistent binding map rather than re-counting events. Optional; production
        # always supplies one.
        self._conversation_tool_ordinal_store: (
            ConversationToolOrdinalStorePort | None
        ) = conversation_tool_ordinal_store
        # Dedup guard keyed by (run_id, task_id) so retried ``handle()`` calls cannot
        # re-emit ``SUBAGENT_RESUMED`` for the same approval.
        self._resumed_task_ids: set[tuple[str, str]] = set()

    async def handle(self, command: RuntimeApprovalResolvedCommand) -> None:
        """Process an approval-resolved command: audit the decision, then resume or terminate the run.

        Forwarded approvals are no-ops here — the graph stays paused until the
        leaf recipient's own approve/reject flows through the existing path.
        """
        # Forwarded decisions are handled by the leaf recipient's command; nothing to do.
        if command.decision is ApprovalDecision.FORWARDED:
            return
        run = await self.persistence.get_run(
            org_id=command.org_id, run_id=command.run_id
        )
        if run is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Approval command references an unknown run.",
                retryable=False,
            )
        approval = await self.persistence.get_approval_request(
            org_id=command.org_id,
            approval_id=command.approval_id,
        )
        if approval is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Approval command references an unknown approval.",
                retryable=False,
            )
        if approval.run_id != command.run_id:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Approval command run_id does not match persisted approval.",
                retryable=False,
            )
        await self.audit_emitter.emit_approval_decision(
            approval,
            decision=command.decision,
            decided_by_user_id=getattr(command, "decided_by_user_id", None),
            reason=getattr(command, "reason", None),
        )
        metadata = approval.metadata
        # Draft-send approvals don't suspend a LangGraph runtime, so we handle
        # their state transitions inline before the LangGraph-resume path runs.
        if metadata.get("kind") == _APPROVAL_KIND_DRAFT_SEND:
            await self._resolve_draft_send_approval(
                run=run,
                approval=approval,
                decision=command.decision,
                decided_by_user_id=getattr(command, "decided_by_user_id", None),
                edits=getattr(command, "edits", None),
            )
            return
        # PRD-09 — ``approve_with_edits`` is an approval variant. For the
        # LangGraph-resume / batch path below it resumes exactly as a plain
        # approve; the reviewer's edits are applied into the committed side
        # effect on the draft-send / commit path, not the resume value. Coercing
        # here keeps the batch primitive and resume payload (which only know
        # approve/reject) crash-free. (v1 edit surfaces are message body +
        # record fields — not MCP tool-call args, per PRD-09 non-goals.)
        if command.decision is ApprovalDecision.APPROVE_WITH_EDITS:
            command = command.model_copy(update={"decision": ApprovalDecision.APPROVED})
        approval_kind = StreamTextHelper.extract(
            metadata.get(self._Fields.APPROVAL_KIND)
        )
        if (
            metadata.get(self._Fields.NATIVE_INTERRUPT_ID) is None
            and approval_kind != ApiValues.ApprovalKind.MCP_AUTH
        ):
            return

        # PR #43 — ApprovalBatch is the resume gate, not the per-item approval.
        #
        # Multi-tool-call interrupts (N >= 2 ``action_requests`` from one
        # LangGraph interrupt) fan out into N ``approval_requested`` events
        # backed by N ``ApprovalBatchItem`` rows in one ``ApprovalBatch``.
        # The graph cannot resume until every item is resolved — resuming
        # with a partial ``decisions[]`` raises ``ValueError`` inside the
        # HITL middleware and crashes the run.
        #
        # The atomic primitive ``record_item_decision_and_maybe_lock_batch``
        # records this item's decision and, if it just completed the batch,
        # flips ``PENDING -> RESUMING`` under a transactional lock. Exactly
        # one concurrent caller wins ``READY_TO_RESUME``; the others get
        # ``LOST_RACE`` and no-op. ``BATCH_INCOMPLETE`` means siblings are
        # still pending — the handler stops here and the run stays
        # ``WAITING_FOR_APPROVAL``.
        outcome = await self.persistence.record_item_decision_and_maybe_lock_batch(
            org_id=command.org_id,
            item_id=command.approval_id,
            decision=command.decision,
        )
        if outcome.status is BatchOutcomeStatus.BATCH_INCOMPLETE:
            # Other items in the same interrupt are still unresolved; the run
            # stays paused on the same WAITING_FOR_APPROVAL state until the
            # last item resolves and another invocation of this handler wins
            # READY_TO_RESUME.
            return
        if outcome.status is BatchOutcomeStatus.LOST_RACE:
            # Another worker already drove the resume (or the batch is no
            # longer PENDING). Idempotent no-op.
            return

        # READY_TO_RESUME: this caller owns the resume. Build the resume value
        # from the aligned per-item decisions so LangGraph sees N decisions
        # for N action_requests.
        resume = self._resume_payload(command, metadata, outcome=outcome)
        # The interrupt this decision answers. Without it LangGraph cannot tell
        # which of several pending interrupts the resume value belongs to and
        # refuses the resume outright — the run would die holding a decision the
        # user already made.
        interrupt_id = self._native_interrupt_id_for(metadata, outcome=outcome)
        run_control_snapshot = (
            await self._run_control_builder.ensure_snapshot(
                run=run,
                trace_id=run.trace_id,
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
        # Rehydrate exactly once before the F10 binding and resumed graph are
        # constructed.  The binding captures only this ephemeral copy.
        resume_context = run.runtime_context
        if self._provider_keys_hydrator is not None:
            resume_context = await self._provider_keys_hydrator.hydrate(resume_context)
        running = await with_optimistic_retry(
            lambda: self.persistence.update_run_status(
                run_id=run.run_id,
                status=AgentRunStatus.RUNNING,
            )
        )
        # Emit SUBAGENT_RESUMED before invoking the LangGraph resumer so the FE
        # reducer can flip the subagent row to ``running`` before any tool event
        # from the resumed branch arrives.
        await self._maybe_emit_subagent_resumed(
            run=running,
            approval=approval,
            command=command,
        )
        # Bind a fresh allocator + resolver: the original task ended when the run
        # paused, so its bindings are gone. The new allocator is seeded from the
        # persistent binding map so ordinals burned before the pause are not reused.
        allocator = await self._build_allocator_for_resume(running)
        allocator_token = ConversationOrdinalAllocator.bind_for_run(allocator)
        citation_resolver = CitationResolver(
            run=running,
            allocator=allocator,
            producer=self.event_producer,
            source=StreamEventSource.MODEL,
        )
        resolver_token = CitationResolver.bind_for_run(citation_resolver)
        # Bind the per-run tool display lookup and MCP descriptor registry before
        # the resumed graph starts emitting tool events. The resumed run runs in a
        # fresh async task, so the original RuntimeRunHandler bindings are gone.
        workspace_backend = await self._workspace_backend_for_resume(running)
        operation_context_token: object | None = None
        mcp_operation_gateway_token: object | None = None
        shadow_comparison_token: object | None = None
        model_invocation_effect_tracker: ModelInvocationEffectTracker | None = None
        # A resumed graph is a fresh tool-execution context. Bind the desktop
        # model-admission boundary again even when the org has no tool-budget
        # rows. Empty middleware carries admission without changing policy.
        tool_result_admission = self._file_store_wiring.tool_result_admission()
        tool_admission_guard = await self._build_tool_budget_guard_for_resume(
            running,
            task_policy_binding=(
                prepared_run_control.task_policy
                if prepared_run_control is not None
                else None
            ),
            tool_result_admission=tool_result_admission,
        )
        dependencies = self._dependencies_for_resume(
            running,
            workspace_backend=workspace_backend,
            granted_host_roots=await self._granted_host_roots_for_resume(
                workspace_backend
            ),
            control_binding=(
                prepared_run_control.control
                if prepared_run_control is not None
                else None
            ),
        )
        mcp_display_registry: dict[str, ToolDisplayTemplate] = {}
        mcp_display_token = McpDisplayRegistryContext.bind_for_run(mcp_display_registry)
        display_token = ToolDisplayLookupContext.bind_for_run(
            RuntimeRunHandler._build_tool_display_lookup(dependencies.tool_registry)
        )
        tool_admission_token = (
            ToolBudgetGuard.bind_for_run(tool_admission_guard)
            if tool_admission_guard is not None
            else None
        )
        run_control_token: object | None = None
        metrics = AssistantRunMetrics.from_run(running)
        try:
            if prepared_run_control is not None:
                run_control_token = RunControlContext.bind_for_run(
                    prepared_run_control.control,
                    task_policy=prepared_run_control.task_policy,
                )
                composed_model_invocation = (
                    await self._model_invocation_composer.compose(
                        run=running,
                        context=resume_context,
                        control=prepared_run_control.control,
                    )
                )
                if composed_model_invocation is not None:
                    RunControlContext.install_model_invocation_runtime(
                        composed_model_invocation.binding
                    )
                if composed_model_invocation is not None:
                    model_invocation_effect_tracker = (
                        composed_model_invocation.effect_tracker
                    )
            if self._shadow_comparison_enabled():
                shadow_comparison_token = ShadowComparisonContext.bind_for_run(
                    resolution=self.settings.execution.rollout
                )
            if self._operation_context_required():

                async def _emit_operation(
                    event_type_value: str,
                    payload: Mapping[str, object],
                    summary: str | None,
                ) -> None:
                    event_type = RuntimeApiEventType(event_type_value)
                    await self.event_producer.append_api_event(
                        run=running,
                        source=StreamEventSource.SYSTEM,
                        event_type=event_type,
                        summary=summary,
                        payload=dict(payload),
                    )
                    if model_invocation_effect_tracker is not None:
                        model_invocation_effect_tracker.mark_event(event_type)

                # P1b: re-compose the model-facing MCP operation gateway so an
                # approved write re-enters an identical gateway on resume and
                # EXECUTES in this run. Without it the resumed ``call_mcp_tool``
                # finds no canonical operation context and holds — re-opening the
                # orphan the interrupt closed. ``None`` (no durable stores wired,
                # the pre-P1b default) preserves the prior hold-on-resume shape.
                mcp_gateway_services = McpOperationGatewayComposer.compose(
                    surfaces_v2=self.settings.execution.surfaces_v2,
                    queue=self._queue,
                    blobs=self._artifact_blob_store,
                    references=self._artifact_reference_store,
                    event_producer=self.event_producer,
                    run=running,
                )
                operation_context_token = OperationContext.bind_for_run(
                    identity=VerifiedOperationIdentity(
                        org_id=running.org_id,
                        user_id=running.user_id,
                        conversation_id=running.conversation_id,
                        run_id=running.run_id,
                    ),
                    policy_snapshot=ToolUsePolicyResolver.resolve(
                        running.runtime_context
                    ),
                    ledger_emitter=OperationEventEmitterAdapter(
                        emit_fn=_emit_operation
                    ),
                    artifact_service=(
                        self.artifact_service
                        if self._artifact_publication_enabled(running)
                        else None
                    ),
                    mode=self.settings.execution.operation_gateway_mode,
                    canonical_arguments_durable=mcp_gateway_services is not None,
                )
                if mcp_gateway_services is not None:
                    mcp_operation_gateway_token = (
                        McpOperationGatewayContext.bind_for_run(mcp_gateway_services)
                    )
            harness_or_coro = self.agent_factory(
                context=resume_context,
                dependencies=dependencies,
            )
            harness = (
                await harness_or_coro
                if inspect.isawaitable(harness_or_coro)
                else harness_or_coro
            )
            result = await self._stream_resume(
                run=running,
                harness=harness,
                resume=resume,
                metrics=metrics,
                interrupt_id=interrupt_id,
            )
            await self._process_model_artifact_content(result, run=running)
            if RuntimeRunHandler._is_action_interrupt(result):
                await with_optimistic_retry(
                    lambda: self.persistence.update_run_status(
                        run_id=run.run_id,
                        status=AgentRunStatus.WAITING_FOR_APPROVAL,
                    )
                )
                return
            final_text = RuntimeRunHandler._extract_final_text(result)
            completed = await self._complete_run_with_result(
                running, final_text, metrics
            )
            await self._record_terminal_usage_safely(
                run=completed,
                metrics=metrics,
                subject_fingerprint=(
                    prepared_run_control.control.snapshot.subject_fingerprint
                    if prepared_run_control is not None
                    else None
                ),
            )
            await self._observe_e2_shadow_projections(
                running.model_copy(update={"status": AgentRunStatus.COMPLETED})
            )
        except Exception as exc:
            failed = await with_optimistic_retry(
                lambda: self.persistence.update_run_status(
                    run_id=run.run_id,
                    status=AgentRunStatus.FAILED,
                )
            )
            await self.run_termination.terminate(
                run=failed,
                terminal_status=AgentRunStatus.FAILED,
                reason=TerminationReason.EXECUTION_ERROR,
                summary="Run failed",
                cause=exc,
            )
            await self._record_terminal_usage_safely(
                run=failed,
                metrics=metrics,
                subject_fingerprint=(
                    prepared_run_control.control.snapshot.subject_fingerprint
                    if prepared_run_control is not None
                    else None
                ),
            )
            await self._observe_e2_shadow_projections(failed)
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
            CitationResolver.unbind(resolver_token)
            ConversationOrdinalAllocator.unbind(allocator_token)
            ToolDisplayLookupContext.unbind(display_token)
            McpDisplayRegistryContext.unbind(mcp_display_token)
            if tool_admission_token is not None:
                ToolBudgetGuard.unbind(tool_admission_token)
            self._file_store_wiring.discard_tool_result_projections(
                run_id=running.run_id
            )
            # Release this resume invocation's pinned grant snapshot
            # (``/v1/runs/end``) — the approved host write lands during resume,
            # so its pinned authority must not outlive the invocation.
            await WorkspaceBackendWorkerWiring.release_backend(workspace_backend)
            # PR #43 — stamp ``RESUMING -> RESOLVED`` on the batch row so a
            # subsequent crash + retry on the same batch does not double-resume.
            # Idempotent for terminal statuses (RESOLVED / EXPIRED).
            if outcome.batch is not None:
                await self.persistence.mark_approval_batch_resolved(
                    org_id=command.org_id,
                    batch_id=outcome.batch.batch_id,
                )

    # Paired with the ``SUBAGENT_PAUSED`` emit; if ``approval.metadata`` carries
    # ``parent_task_id`` the same task_id is reused in the resume signal so the
    # FE reducer finds the subagent row by task_id.
    _SUBAGENT_RESUME_REASONS = {
        ApprovalDecision.APPROVED: "approved",
        ApprovalDecision.REJECTED: "rejected",
    }

    async def _maybe_emit_subagent_resumed(
        self,
        *,
        run: RunRecord,
        approval: object,
        command: RuntimeApprovalResolvedCommand,
    ) -> None:
        """Emit ``SUBAGENT_RESUMED`` if the resolved approval originated inside a subagent subgraph."""
        metadata = getattr(approval, "metadata", None)
        if not isinstance(metadata, Mapping):
            return
        parent_task_id = StreamTextHelper.extract(
            metadata.get(self._Fields.PARENT_TASK_ID)
        )
        if parent_task_id is None:
            return
        reason = self._SUBAGENT_RESUME_REASONS.get(command.decision)
        if reason is None:
            return
        dedup_key = (run.run_id, parent_task_id)
        if dedup_key in self._resumed_task_ids:
            return
        self._resumed_task_ids.add(dedup_key)
        payload: dict[str, object] = {
            self._Fields.TASK_ID: parent_task_id,
            self._Fields.REASON: reason,
            self._Fields.APPROVAL_ID: command.approval_id,
        }
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SUBAGENT,
            event_type=RuntimeApiEventType.SUBAGENT_RESUMED,
            payload=payload,
            parent_task_id=parent_task_id,
        )

    async def _build_tool_budget_guard_for_resume(
        self,
        run: RunRecord,
        *,
        task_policy_binding: TaskPolicyRuntimeBinding | None,
        tool_result_admission: object | None,
    ) -> ToolBudgetGuard | None:
        """Rebind capability budgets over durable F4 spend on approval resume."""

        if (
            task_policy_binding is None
            or task_policy_binding.mode is not FeatureMode.ENFORCE
        ):
            if tool_result_admission is None and task_policy_binding is None:
                return None
            return ToolBudgetGuard(
                middleware=ToolBudgetMiddleware(()),
                ledger=self.stream_event_mapper.message_processor.ledger_for_run(
                    run.run_id
                ),
                run=run,
                event_producer=self.event_producer,
                task_policy_binding=task_policy_binding,
                tool_result_admission=tool_result_admission,  # type: ignore[arg-type]
            )
        loader = getattr(self.persistence, "list_tool_budgets_for_org", None)
        budgets: Sequence[ToolBudgetRecord] = ()
        if loader is not None:
            try:
                budgets = await loader(org_id=run.org_id)
            except Exception:
                _LOGGER.warning("tool_budget_resume_load_failed", exc_info=True)
                budgets = ()
            else:
                budgets = WorkspaceToolBudgetOverride.apply(
                    budgets,
                    max_calls_per_run=await self._workspace_tool_call_cap(run),
                )
        if (
            not budgets
            and tool_result_admission is None
            and task_policy_binding is None
        ):
            return None
        return ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(budgets),
            ledger=self.stream_event_mapper.message_processor.ledger_for_run(
                run.run_id
            ),
            run=run,
            event_producer=self.event_producer,
            task_policy_binding=task_policy_binding,
            tool_result_admission=tool_result_admission,  # type: ignore[arg-type]
        )

    async def _workspace_tool_call_cap(self, run: RunRecord) -> int | None:
        loader = getattr(self.persistence, "get_workspace_defaults", None)
        if loader is None:
            return None
        try:
            record = await loader(org_id=run.org_id)
        except Exception:
            _LOGGER.warning("workspace_tool_call_cap_resume_load_failed", exc_info=True)
            return None
        if record is None:
            return None
        overrides = getattr(record, "behavior_overrides", None)
        value = getattr(overrides, "tool_calls_per_run", None)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    async def _build_allocator_for_resume(
        self,
        run: RunRecord,
    ) -> ConversationOrdinalAllocator:
        """Rebuild the ordinal allocator from the persistent binding store for a resumed run.

        Falls back to a fresh memory-only allocator when no binding store is available
        (replay / eval paths); ordinals are not carried across the pause in that case.
        """

        if self._conversation_tool_ordinal_store is None:
            return ConversationOrdinalAllocator(
                org_id=run.org_id,
                conversation_id=run.conversation_id,
                run_id=run.run_id,
            )
        return await ConversationOrdinalAllocator.for_conversation(
            org_id=run.org_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            store=self._conversation_tool_ordinal_store,
        )

    async def _workspace_backend_for_resume(self, run: RunRecord) -> object | None:
        """Construct the resumed run's ``/workspace/`` backend, or ``None``.

        Mirrors :meth:`RuntimeRunHandler._workspace_backend_for_run`: gated on the
            desktop broker. The compatibility backend is read-only; stale
        filesystem approvals never resume into a host mutation.
        """
        # A resumed graph is a fresh model invocation.  It must re-evaluate
        # the same persisted-run cohort and rollback decision as the initial
        # invocation before mounting any workspace route.  A denial gets the
        # tombstone; it cannot revive a pre-pause compatibility backend.
        if not self._e2_rollout_admission.permits_all(
            capabilities=(
                RolloutCapability.OPERATION_GATEWAY,
                RolloutCapability.EFFECT_STAGER,
                RolloutCapability.EFFECT_COMMIT,
                RolloutCapability.WORKSPACE_OVERLAY,
                RolloutCapability.WORKSPACE_COMMIT,
            ),
            facts_provider=PersistedRunCohortFactsProvider(
                org_id=run.org_id,
                user_id=run.user_id,
            ),
        ) or (
            self.settings.execution.workspace_effect_mode
            is OperationGatewayMode.ENFORCE
        ):
            # Enforce never resumes the retired filesystem interrupt into a
            # broker mutation. A stale pre-cutover approval receives a mounted
            # tombstone, so CompositeBackend cannot fall through to StateBackend.
            from agent_runtime.capabilities.workspace.deep_backend import (  # noqa: PLC0415
                WorkspaceTombstoneBackend,
            )

            return WorkspaceTombstoneBackend()

        return await self._workspace_wiring().workspace_backend()

    def _workspace_wiring(self) -> WorkspaceBackendWorkerWiring:
        """The desktop capability-broker wiring for this resumed run."""

        return WorkspaceBackendWorkerWiring(
            http_client=self._workspace_broker_http_client  # type: ignore[arg-type]
        )

    async def _granted_host_roots_for_resume(
        self, workspace_backend: object | None
    ) -> tuple[object, ...] | None:
        """The attached folders this resumed turn's filesystem rules admit.

        A resume rebuilds the agent, so it rebuilds the rule set — which means
        omitting this here would make an approved run start asking again about
        folders it had stopped asking about, mid-conversation. Mirrors
        :meth:`RuntimeRunHandler._granted_host_roots_for_run`: read the backend's
        own capability when it has one, otherwise ask the broker, because the
        ENFORCE lane resumes into a tombstone that can never name a host root.
        """

        if workspace_backend is None:
            return None
        roots = getattr(workspace_backend, "granted_roots", None)
        if isinstance(roots, tuple):
            return roots
        return await self._workspace_wiring().granted_host_roots()

    def _dependencies_for_resume(
        self,
        run: RunRecord,
        *,
        workspace_backend: object | None = None,
        granted_host_roots: tuple[object, ...] | None = None,
        control_binding: RunControlBinding | None = None,
    ) -> RuntimeDependencies:
        """Build ``RuntimeDependencies`` for a resumed run with per-run backends.

        Mirrors :meth:`RuntimeRunHandler._dependencies_for_run`: the bare factory
        output is augmented with the file-native ``/subagents/`` +
        ``/large_tool_results/`` read backends (so a reference produced *before*
        the pause is readable through the composed backend after resume), the
        persistent ``/drafts/`` backend, and the read-only ``/workspace/`` host
        folder backend. All are ``None``-gated (file store / draft store /
        desktop broker), so non-file, non-desktop backends get an empty
        ``model_copy`` update and stay byte-identical to the previous
        bare-factory behavior. Keeping ``/workspace/`` here as well as on the
        run path means a pre-pause ``/workspace/`` reference stays readable after
        an approval, exactly as the file-native routes do.
        """

        if isinstance(self.dependencies_factory, DefaultRuntimeDependenciesFactory):
            dependencies = self.dependencies_factory.for_run(
                run.runtime_context,
                rollout_admission=self._e2_rollout_admission,
                rollout_facts=PersistedRunCohortFactsProvider(
                    org_id=run.org_id,
                    user_id=run.user_id,
                ),
            )
        else:
            dependencies = self.dependencies_factory(run.runtime_context)
        update: dict[str, object] = {}
        if self._prompt_observation_store is not None and control_binding is not None:
            update["prompt_assembly_observer"] = PromptAssemblyObserver(
                store=self._prompt_observation_store,
                binding=control_binding,
                org_id=run.org_id,
                subject_fingerprint=control_binding.snapshot.subject_fingerprint,
                trace_id=run.trace_id,
            )
        if workspace_backend is not None:
            update["workspace_backend"] = workspace_backend
        # `()` ("resolved: nothing attached") is a different claim from `None`
        # ("nobody resolved"), so an empty tuple must still be carried.
        if granted_host_roots is not None:
            update["granted_host_roots"] = granted_host_roots
        # The same conversation-scoped MCP catalog the interrupted turn was
        # browsing. Without this the resumed harness silently falls back to a
        # fresh in-process store and `/mcp/` empties after every write approval
        # — literally bug R1 again, one directory over.
        mcp_catalog_store = AgentScratchWorkerWiring(
            workspace_backend=workspace_backend
        ).mcp_catalog_store(conversation_id=run.conversation_id)
        if mcp_catalog_store is not None:
            update["mcp_catalog_store"] = mcp_catalog_store
        subagent_backend = self._file_store_wiring.subagent_artifacts_backend(
            org_id=run.org_id,
            conversation_id=run.conversation_id,
        )
        if subagent_backend is not None:
            update["subagent_artifacts_backend"] = subagent_backend
        large_tool_results_backend = (
            self._file_store_wiring.large_tool_results_backend()
        )
        if large_tool_results_backend is not None:
            update["large_tool_results_backend"] = large_tool_results_backend
        drafts_backend = self._drafts_backend(run)
        if drafts_backend is not None:
            update["drafts_backend"] = drafts_backend
        publish_artifact_tool = self._publish_artifact_tool(run)
        if publish_artifact_tool is not None:
            update["publish_artifact_tool"] = publish_artifact_tool
        revise_artifact_tool = self._revise_artifact_tool(run)
        if revise_artifact_tool is not None:
            update["revise_artifact_tool"] = revise_artifact_tool
        return dependencies.model_copy(update=update)

    def _artifact_publication_enabled(self, run: RunRecord) -> bool:
        return bool(
            self.settings.execution.artifact_effects_v2
            and self.artifact_service is not None
            and self._e2_rollout_admission.permits_all(
                capabilities=(RolloutCapability.ARTIFACT_REPOSITORY,),
                facts_provider=PersistedRunCohortFactsProvider(
                    org_id=run.org_id,
                    user_id=run.user_id,
                ),
            )
        )

    def _drafts_backend(self, run: RunRecord) -> object | None:
        """Resume against the same single draft authority as the run path."""

        from agent_runtime.capabilities.backends import (  # noqa: PLC0415
            ArtifactDraftBackend,
            DraftBackend,
        )

        if (
            self._artifact_publication_enabled(run)
            and self.settings.execution.artifact_drafts_v2
        ):
            return ArtifactDraftBackend(
                artifacts=self.artifact_service,
                org_id=run.org_id,
                conversation_id=run.conversation_id,
                run_id=run.run_id,
                user_id=run.runtime_context.user_id,
                legacy_store=self._draft_store,
            )
        if self._draft_store is None:
            return None
        return DraftBackend(
            store=self._draft_store,
            org_id=run.org_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            user_id=run.runtime_context.user_id,
            emit_event=self._draft_backend_event_emitter(run),
        )

    def _operation_context_required(self) -> bool:
        """Always bind the canonical context on a resumed model invocation.

        The context is inert in ``off`` mode, but makes a resumed run use the
        same operation boundary as its initial invocation.  In particular, a
        paused run cannot discover an unbound direct-client path after a
        cohort becomes denied or a targeted rollback is applied.
        """

        return True

    def _shadow_comparison_enabled(self) -> bool:
        """D2 has no independent switch; only D1's typed shadow lanes bind it."""

        return bool(self.settings.execution.rollout.modes.shadowed())

    def _shadow_projection_observation_enabled(self) -> bool:
        modes = self.settings.execution.rollout.modes
        return any(
            modes.mode_for(capability) is RolloutMode.SHADOW
            for capability in (
                RolloutCapability.PRESENTATION_V2_1,
                RolloutCapability.ARTIFACT_REPOSITORY,
            )
        )

    async def _observe_e2_shadow_projections(self, run: RunRecord) -> None:
        """Observe terminal replay state after legacy handling, never before it."""

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

    def _artifact_family_model_visible(self, *, lane_enabled: bool) -> bool:
        """Read the SAME exposure decision the run handler read.

        This is the mid-task guarantee. The knob lives on the frozen document
        resolved once at the composition root, so a run that started with the
        artifact family keeps it when it resumes past an approval, and a run
        that started without it does not suddenly gain three tool schemas
        (which would invalidate the prompt-cache prefix for the whole
        remainder of the run — 97% of input tokens are cache reads).
        """

        return self.settings.hyperparameters.tool_surface.admits_artifact_family(
            lane_enabled=lane_enabled
        )

    def _publish_artifact_tool(self, run: RunRecord) -> PublishArtifactTool | None:
        if not self._artifact_family_model_visible(
            lane_enabled=self._artifact_publication_enabled(run)
        ):
            return None
        return PublishArtifactTool(
            gateway=OperationGateway(descriptors=DEFAULT_OPERATION_DESCRIPTORS)
        )

    def _revise_artifact_tool(self, run: RunRecord) -> ReviseArtifactTool | None:
        # Same gate as publication — see the run handler for why they are paired.
        if not self._artifact_family_model_visible(
            lane_enabled=self._artifact_publication_enabled(run)
        ):
            return None
        return ReviseArtifactTool(
            gateway=OperationGateway(descriptors=DEFAULT_OPERATION_DESCRIPTORS),
            # Injected for the same reason as the run handler's copy: a resumed
            # run must recover a lost compare-and-append identically, or the
            # behaviour would differ either side of an approval.
            content_reader=self.artifact_service,
        )

    async def _process_model_artifact_content(
        self, result: object, *, run: RunRecord
    ) -> None:
        if self._artifact_publication_enabled(run):
            await ArtifactContentPartPublisher().publish(result)
            return
        await OperationShadowProbe.observe_model_result(result)

    def _draft_backend_event_emitter(
        self, run: RunRecord
    ) -> "Callable[[object], Awaitable[None]]":
        """Build the ``emit_event`` closure ``DraftBackend`` uses to emit ``DRAFT_UPDATED``.

        Reuses :meth:`_emit_draft_updated` so a draft the agent writes during the
        resumed graph flows through the same redaction + projection + sequence
        cursor path as every other API-authored event.
        """

        async def _emit(record: object) -> None:
            await self._emit_draft_updated(run=run, record=record)

        return _emit

    async def _stream_resume(
        self,
        *,
        run: RunRecord,
        harness: RuntimeHarness,
        resume: object,
        metrics: AssistantRunMetrics,
        interrupt_id: str | None = None,
    ) -> object:
        """Stream a resumed LangGraph run and return the composed final result."""
        stream = (
            self.runtime_resumer(harness, resume, interrupt_id=interrupt_id)
            if interrupt_id is not None
            else self.runtime_resumer(harness, resume)
        )
        result = await StreamingExecutor.run(
            stream=stream,
            run=run,
            metrics=metrics,
            event_store=self.event_store,
            event_producer=self.event_producer,
            stream_event_mapper=self.stream_event_mapper,
            track_subagents=False,
            citation_pipeline=CitationStreamPipeline.for_provider(
                run.runtime_context.model_profile.provider
            ),
            delta_coalesce_window_ms=self.settings.execution.delta_coalesce_window_ms,
            delta_coalesce_max_chunks=self.settings.execution.delta_coalesce_max_chunks,
        )
        return StreamingExecutor.compose_final(result)

    async def _complete_run_with_result(
        self,
        run: RunRecord,
        final_text: str | None,
        metrics: AssistantRunMetrics,
    ) -> RunRecord:
        """Persist the final assistant message (if any), emit ``FINAL_RESPONSE``, and mark the run completed."""
        metrics_payload = metrics.to_payload(completed_at=datetime.now(timezone.utc))
        if final_text is not None:
            usage = metrics_payload.get("usage")
            output_tokens = usage.get("output") if isinstance(usage, dict) else None
            await self.persistence.append_message(
                MessageRecord(
                    conversation_id=run.conversation_id,
                    org_id=run.org_id,
                    run_id=run.run_id,
                    role=MessageRole.ASSISTANT,
                    content_text=final_text,
                    # This is the resume-after-approval completion, so the turn
                    # provably contains a mid-stream card — the approval the run
                    # parked on. Persisting the ordered parts is what keeps the
                    # prose on both sides of it after a reload.
                    content=await self._turn_content.blocks(
                        org_id=run.org_id,
                        run_id=run.run_id,
                        final_text=final_text,
                    ),
                    parent_message_id=run.user_message_id,
                    metadata=AssistantRunMetrics.metadata(metrics_payload),
                    token_count=output_tokens
                    if isinstance(output_tokens, int)
                    else None,
                    trace_id=run.trace_id,
                )
            )
            await self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.SYSTEM,
                event_type=RuntimeApiEventType.FINAL_RESPONSE,
                payload=AssistantRunMetrics.with_payload(
                    {self._Fields.MESSAGE: final_text},
                    metrics_payload,
                ),
                metadata=AssistantRunMetrics.metadata(metrics_payload),
                summary=final_text,
                status="completed",
            )
        completed = await with_optimistic_retry(
            lambda: self.persistence.update_run_status(
                run_id=run.run_id,
                status=AgentRunStatus.COMPLETED,
            )
        )
        await self.run_termination.terminate(
            run=completed,
            terminal_status=AgentRunStatus.COMPLETED,
            reason=TerminationReason.NORMAL_COMPLETION,
            summary="Run completed",
            extra_payload=AssistantRunMetrics.with_payload({}, metrics_payload),
            extra_metadata=AssistantRunMetrics.metadata(metrics_payload),
        )
        return completed

    async def _record_terminal_usage(
        self,
        *,
        run: RunRecord,
        metrics: AssistantRunMetrics,
        subject_fingerprint: str | None,
    ) -> None:
        """Reconcile the resumed segment after its outer terminal event."""

        completed_at = run.completed_at or datetime.now(timezone.utc)
        await self._model_invocation_terminal.finalize(
            run=run,
            metrics=metrics,
            subject_fingerprint=subject_fingerprint,
            completed_at=completed_at,
        )
        observed_cost = await self._model_invocation_terminal.record_run_usage(
            run=run,
            metrics=metrics,
            completed_at=completed_at,
            status=run.status.value,
        )
        try:
            await self._budget_charger.charge_run(
                org_id=run.org_id,
                user_id=run.user_id,
                run_id=run.run_id,
                observed_micro_usd=observed_cost,
                observed_tokens=metrics.to_usage_record(
                    run,
                    completed_at=completed_at,
                    status=run.status.value,
                ).total_tokens,
            )
        except Exception:
            _LOGGER.warning(
                "approval_resume_budget_charge_failed",
                extra={"metadata": {"run_id": run.run_id}},
                exc_info=True,
            )

    async def _record_terminal_usage_safely(
        self,
        *,
        run: RunRecord,
        metrics: AssistantRunMetrics,
        subject_fingerprint: str | None,
    ) -> None:
        """Never rewrite a durable user-visible terminal outcome on projection loss."""

        try:
            await self._record_terminal_usage(
                run=run,
                metrics=metrics,
                subject_fingerprint=subject_fingerprint,
            )
        except Exception:
            _LOGGER.warning(
                "approval_resume_terminal_projection_failed",
                extra={"metadata": {"run_id": run.run_id}},
                exc_info=True,
            )

    @classmethod
    def _native_interrupt_id_for(
        cls,
        metadata: Mapping[str, object],
        *,
        outcome: BatchTransitionOutcome | None = None,
    ) -> str | None:
        """Return the native LangGraph interrupt id this approval belongs to.

        Three sources carry the same value by construction — the stream mapper
        stamps ``native_interrupt_id`` and ``batch_id`` from one interrupt id,
        and ``ApprovalBatchRecord.batch_id`` is that id (the batch is the
        interrupt's persistence projection). ``batch_id`` is preferred because
        the batch row is the resume gate; ``native_interrupt_id`` covers
        single-action kinds (``mcp_auth``, ``ask_a_question``) whose payloads
        predate batches.

        Returns ``None`` when neither is a real LangGraph id, which makes the
        resume fall back to the bare (untargeted) form — correct as long as only
        one interrupt is pending.
        """

        candidates = (
            outcome.batch.batch_id
            if outcome is not None and outcome.batch is not None
            else None,
            StreamTextHelper.extract(metadata.get(cls._Fields.BATCH_ID)),
            StreamTextHelper.extract(metadata.get(cls._Fields.NATIVE_INTERRUPT_ID)),
        )
        for candidate in candidates:
            if is_native_interrupt_id(candidate):
                return candidate
        return None

    @classmethod
    def _resume_payload(
        cls,
        command: RuntimeApprovalResolvedCommand,
        metadata: Mapping[str, object],
        *,
        outcome: BatchTransitionOutcome | None = None,
    ) -> dict[str, object]:
        """Build the LangGraph resume value dict appropriate for the approval kind.

        For MCP tool batches, the resume payload contains the aligned per-item
        ``decisions`` list (N entries for an N-action interrupt). N=1 and N=N
        follow the same code path — the substitution principle that pinned the
        fix.

        For ``mcp_auth`` and ``ask_a_question`` (single-action interrupts), the
        resume shape is unchanged from before — those harness paths consume a
        flat ``{approval_id, decision[, answer]}`` dict.
        """
        approval_kind = StreamTextHelper.extract(
            metadata.get(cls._Fields.APPROVAL_KIND)
        )
        decision = (
            "approved" if command.decision is ApprovalDecision.APPROVED else "rejected"
        )
        if approval_kind == ApiValues.ApprovalKind.MCP_AUTH:
            return {
                cls._Fields.APPROVAL_ID: command.approval_id,
                cls._Fields.DECISION: decision,
            }
        if approval_kind == ApiValues.ApprovalKind.ASK_A_QUESTION:
            return {
                cls._Fields.APPROVAL_ID: command.approval_id,
                cls._Fields.DECISION: decision,
                cls._Fields.ANSWER: command.answer,
            }
        # MCP tool path. With ``outcome`` populated (the production path) we
        # project the actual per-item decisions in interrupt order so a mixed
        # approve/reject N=5 batch sends LangGraph the literal mix and not 5
        # copies of the last decision. Without ``outcome`` (legacy / test
        # fixtures that bypass the batch primitive) we fall back to the
        # 1-element single-decision shape so older tests still pass.
        if outcome is not None and outcome.status is BatchOutcomeStatus.READY_TO_RESUME:
            # ``decisions_in_order`` returns ``BatchItemDecision`` (records-
            # layer enum). Compare by string value so the runtime API enum
            # ``ApprovalDecision`` and the persistence enum stay decoupled.
            return {
                cls._Fields.DECISIONS: [
                    {
                        cls._Fields.TYPE: "approve"
                        if item_decision.value == ApprovalDecision.APPROVED.value
                        else "reject",
                    }
                    for item_decision in outcome.decisions_in_order()
                ]
            }
        return {
            cls._Fields.DECISIONS: [
                {
                    cls._Fields.TYPE: "approve"
                    if command.decision is ApprovalDecision.APPROVED
                    else "reject",
                }
            ]
        }

    async def _resolve_draft_send_approval(
        self,
        *,
        run: RunRecord,
        approval: object,
        decision: ApprovalDecision,
        decided_by_user_id: str | None,
        edits: object | None = None,
    ) -> None:
        """Apply a draft-send approval: persist the new draft version, emit ``DRAFT_UPDATED``, and complete the run.

        Approve (or ``approve_with_edits``) → ``status=sent``; Reject →
        ``status=draft``. Skips silently when the draft store is absent or the
        draft is no longer in ``send_pending_approval`` state (idempotent: a
        replay after the send observes ``status=sent`` and no-ops, so the send
        cannot fire twice).

        PRD-09 — for ``approve_with_edits`` the reviewer's edit deltas (``edits``)
        are merged server-side INTO the committed draft version before it is
        marked sent: ``body`` replaces the content, ``fields`` overlay the target
        metadata. The client never sends a merged artifact — the base is always
        the server-held pending draft.
        """

        from agent_runtime.persistence.records import DraftStatus  # noqa: PLC0415

        if self._draft_store is None:
            return
        metadata = approval.metadata if hasattr(approval, "metadata") else {}
        draft_id = str(metadata.get("draft_id", ""))
        approval_owner_id = getattr(approval, "user_id", None)
        if not draft_id:
            return
        latest = await self._draft_store.latest(org_id=run.org_id, draft_id=draft_id)
        if (
            latest is None
            or latest.user_id != approval_owner_id
            or latest.status is not DraftStatus.SEND_PENDING_APPROVAL
        ):
            # State changed since the approval was posted (e.g. a concurrent
            # discard, owner change attempt, or an already-applied send).
            # Idempotent no-op; this worker cannot take ownership on approval.
            return

        # PRD-D2 flag-flip hardening (WYSIWYG). A v1 draft-send approval created
        # while ``SURFACES_V2`` was OFF sends ``draft_store.latest(draft_id)``. If
        # the flag then flipped ON and the SAME draft was re-sent v2-staged, that
        # "latest" is now NEWER content the user never approved at v1 time. Refuse
        # to resolve an approval whose draft has since been staged on the v2
        # ledger — the v2 stage supersedes it. Fail-closed: refuse (no send)
        # rather than send un-approved content.
        if await self._draft_superseded_by_v2_stage(run=run, draft_id=draft_id):
            _LOGGER.warning(
                "draft_send.superseded_by_v2_stage draft_id=%s run_id=%s — refusing "
                "stale v1 send",
                draft_id,
                run.run_id,
            )
            return

        if decision in (
            ApprovalDecision.APPROVED,
            ApprovalDecision.APPROVE_WITH_EDITS,
        ):
            terminal_status = DraftStatus.SENT
            audit_action = _AUDIT_DRAFT_SEND_COMPLETED
        elif decision is ApprovalDecision.REJECTED:
            terminal_status = DraftStatus.DRAFT
            audit_action = _AUDIT_DRAFT_SEND_REJECTED
        else:
            return

        next_record = self._next_draft_version(
            previous=latest,
            status=terminal_status,
        )
        applied_edit_keys: list[str] = []
        if decision is ApprovalDecision.APPROVE_WITH_EDITS and edits is not None:
            next_record, applied_edit_keys = self._apply_edits_to_draft(
                record=next_record, edits=edits
            )
        try:
            persisted = await self._draft_store.insert_version(next_record)
        except DraftOwnershipConflict:
            # A direct writer raced this approval with an owner change attempt.
            # Never emit an event or audit a transition that did not persist.
            return
        await self._emit_draft_updated(run=run, record=persisted)
        await self._write_draft_audit(
            run=run,
            record=persisted,
            action=audit_action,
            extra_metadata={
                "approval_id": getattr(approval, "approval_id", None),
                "decided_by_user_id": decided_by_user_id,
                "edited": bool(applied_edit_keys),
                "edited_keys": applied_edit_keys or None,
            },
        )
        # Rejection is a normal outcome — mark the run completed either way.
        completed = await with_optimistic_retry(
            lambda: self.persistence.update_run_status(
                run_id=run.run_id,
                status=AgentRunStatus.COMPLETED,
            )
        )
        await self.run_termination.terminate(
            run=completed,
            terminal_status=AgentRunStatus.COMPLETED,
            reason=TerminationReason.NORMAL_COMPLETION,
            summary="Run completed",
        )

    async def _draft_superseded_by_v2_stage(
        self, *, run: RunRecord, draft_id: str
    ) -> bool:
        """Return whether a v2 stage supersedes this legacy draft approval.

        A ``write.staged`` for this draft means the write was re-homed onto the v2
        staged-write engine (the WYSIWYG-guarded path); a stale v1 approval must
        NOT independently send. F-006 persists a canonical owner-scoped
        draft→effect-stage binding before the effect is exposed. That lookup is
        intentionally independent of mutable ``DraftRecord.run_id`` and is the
        authority for cross-host-run supersession. Every safety read fails
        closed: an unavailable proof of safety must never send mutable bytes.
        """

        from agent_runtime.surfaces_v2.ledger_models import (  # noqa: PLC0415
            LedgerEventType,
        )
        from agent_runtime.surfaces_v2.staging import DraftRef  # noqa: PLC0415

        supersessions = getattr(self._draft_store, "has_effect_supersession", None)
        if not callable(supersessions):
            _LOGGER.error(
                "draft_send.supersession_lookup_unavailable draft_id=%s run_id=%s",
                draft_id,
                run.run_id,
            )
            return True
        try:
            if await supersessions(
                org_id=run.org_id,
                user_id=run.user_id,
                draft_id=draft_id,
            ):
                return True
        except Exception:  # noqa: BLE001 - approval safety must fail closed.
            _LOGGER.exception(
                "draft_send.supersession_lookup_failed draft_id=%s run_id=%s",
                draft_id,
                run.run_id,
            )
            return True

        # The prior D1 staged-write guard remains run-scoped because D1's
        # legacy stage facts predate the canonical F-006 correlation. It too
        # is fail-closed: replay failure cannot authorize a stale v1 send.
        try:
            events = await self.event_store.list_events_after(
                org_id=run.org_id, run_id=run.run_id, after_sequence=0
            )
        except Exception:  # noqa: BLE001 - approval safety must fail closed.
            _LOGGER.exception(
                "draft_send.stage_lookup_failed draft_id=%s run_id=%s",
                draft_id,
                run.run_id,
            )
            return True
        staged_value = LedgerEventType.WRITE_STAGED.value
        for event in events:
            event_type = getattr(getattr(event, "event_type", None), "value", None)
            payload = getattr(event, "payload", None)
            if not isinstance(payload, Mapping):
                continue
            if event_type == staged_value:
                parsed = DraftRef.parse_proposal(payload.get("proposal_ref"))
                if parsed is not None and parsed[0] == draft_id:
                    return True
        return False

    @staticmethod
    def _apply_edits_to_draft(
        *,
        record: object,
        edits: object,
    ) -> tuple[object, list[str]]:
        """Merge reviewer edit deltas into a draft version (PRD-09), returning the edited keys.

        ``body`` replaces the draft content; ``fields`` overlay the target
        metadata. The base is the server-held draft ``record`` — the client's
        deltas can only replace body/target-metadata values, never redirect the
        draft (draft_id, org, connector all stay as persisted). Returns the
        (possibly unchanged) record plus the list of applied edit keys for audit.
        """

        update: dict[str, object] = {}
        applied: list[str] = []
        body = getattr(edits, "body", None)
        if body is not None:
            update["content_text"] = body
            applied.append("body")
        fields = getattr(edits, "fields", None)
        if fields:
            existing = dict(getattr(record, "target_metadata", None) or {})
            # Defense in depth (PRD-09b review): re-assert the editable-fields
            # allowlist at the WORKER, not only at the API edge. The coordinator
            # already 422s unknown ``edits.fields`` keys, but a directly-enqueued
            # (unvalidated) ``RuntimeApprovalResolvedCommand`` would bypass that
            # check — so reject here too, BEFORE any draft mutation. The allowlist
            # is the draft's explicit editable set when present, else the keys
            # already in the server-held target_metadata: a reviewer delta may
            # overwrite an existing field but can never introduce a new one.
            allowlist = getattr(record, "editable_fields", None)
            allowed = (
                {str(key) for key in allowlist}
                if allowlist is not None
                else set(existing.keys())
            )
            unknown = sorted({str(key) for key in fields} - allowed)
            if unknown:
                raise AgentRuntimeError(
                    RuntimeErrorCode.VALIDATION_ERROR,
                    "One or more edited fields are not editable for this draft.",
                    retryable=False,
                )
            merged = dict(existing)
            merged.update({str(key): value for key, value in fields.items()})
            update["target_metadata"] = merged
            applied.extend(f"fields.{key}" for key in fields)
        if not update:
            return record, applied
        return record.model_copy(update=update), applied

    @staticmethod
    def _next_draft_version(
        *,
        previous: object,
        status: object,
    ) -> object:
        """Return a new ``DraftRecord`` at ``previous.version + 1`` with the given status."""
        from datetime import datetime, timezone  # noqa: PLC0415
        from agent_runtime.persistence.records import DraftRecord  # noqa: PLC0415

        return DraftRecord(
            draft_id=previous.draft_id,
            version=previous.version + 1,
            org_id=previous.org_id,
            conversation_id=previous.conversation_id,
            run_id=previous.run_id,
            user_id=previous.user_id,
            title=previous.title,
            content_text=previous.content_text,
            target_connector=previous.target_connector,
            target_metadata=dict(previous.target_metadata or {}),
            citation_ids=previous.citation_ids,
            status=status,
            encryption_version=previous.encryption_version,
            created_at=datetime.now(timezone.utc),
        )

    async def _emit_draft_updated(self, *, run: RunRecord, record: object) -> None:
        """Emit a ``DRAFT_UPDATED`` event carrying the persisted draft's new version and status."""
        payload: dict[str, object] = {
            "draft_id": record.draft_id,
            "version": record.version,
            "status": record.status.value,
            "title": record.title,
            "target_connector": record.target_connector,
            "target_metadata": record.target_metadata or None,
            "citation_ids": list(record.citation_ids),
            "summary": f"Draft v{record.version}: {record.title or 'Untitled'}",
        }
        # PRD-E3: the v1 ``message`` surface attach was retired — a
        # ``DRAFT_UPDATED`` payload no longer carries ``surface`` / ``surface_uri``.
        # Draft surfaces render from D1-wave ``write.staged`` / ``revision.added``
        # ledger events instead.
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.DRAFT_UPDATED,
            payload=payload,
            summary=str(payload["summary"]),
            status=ApiValues.Status.COMPLETED,
        )

    async def _write_draft_audit(
        self,
        *,
        run: RunRecord,
        record: object,
        action: str,
        extra_metadata: dict[str, object] | None = None,
    ) -> None:
        """Write a draft-send audit log entry; no-ops when the persistence port has no audit method."""
        write_audit = getattr(self.persistence, "write_audit_log", None)
        if write_audit is None:
            return
        metadata: dict[str, object] = {
            "org_id": run.org_id,
            "user_id": run.user_id,
            "draft_id": record.draft_id,
            "version": record.version,
            "status": record.status.value,
            "target_connector": record.target_connector,
            "run_id": run.run_id,
        }
        if extra_metadata:
            metadata.update({k: v for k, v in extra_metadata.items() if v is not None})
        await write_audit(event_type=action, record=metadata)
