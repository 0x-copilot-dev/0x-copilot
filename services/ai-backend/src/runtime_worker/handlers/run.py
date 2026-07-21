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
    BudgetEstimator,
    BudgetPreflightAllow,
    BudgetPreflightDeny,
    BudgetPreflightWarn,
)
from agent_runtime.api.mcp_discovery_service import McpDiscoveryService
from agent_runtime.capabilities.citation_resolver import CitationResolver
from agent_runtime.capabilities.citations import CitationLedger
from agent_runtime.capabilities.conversation_ordinals import (
    ConversationOrdinalAllocator,
)
from agent_runtime.capabilities.tool_budget_guard import ToolBudgetGuard
from agent_runtime.capabilities.tool_budget_middleware import ToolBudgetMiddleware
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
    StreamEventSource,
)
from agent_runtime.execution.tool_outcomes import ToolErrorCode, ToolOutcome
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_termination import (
    RunTerminationCoordinator,
    TerminationReason,
)
from agent_runtime.api.presentation import (
    ToolDisplayLookup,
    ToolDisplayLookupContext,
)
from agent_runtime.api.user_policies_resolver import (
    ProviderKeysHydrator,
    UserPoliciesResolver,
)
from agent_runtime.capabilities.mcp.descriptor_registry import (
    McpDisplayRegistryContext,
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
from agent_runtime.persistence.records import BudgetReservationRecord
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
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.file_store_wiring import FileStoreWorkerWiring
from runtime_worker.workspace_backend_wiring import WorkspaceBackendWorkerWiring
from runtime_worker.run_metrics import AssistantRunMetrics
from runtime_worker.stream_events import StreamOrchestrator
from runtime_worker.stream_messages import StreamTextHelper
from runtime_worker.streaming_executor import StreamingExecutor
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
    ) -> None:
        self.persistence: PersistencePort = persistence
        self.event_store: EventStorePort = event_store
        self.settings = settings or RuntimeSettings.load()
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
        )
        self.stream_event_mapper = StreamOrchestrator(
            self.event_producer,
            tool_result_offloader=self._build_tool_result_offloader(),
        )
        self._runtime_streamer_explicit = runtime_streamer is not astream_runtime
        self.audit_emitter = WorkerAuditEmitter(persistence=self.persistence)
        self.pricing_catalog = ModelPricingCatalog(self.persistence)
        self.budget_enforcer = BudgetEnforcer(self.persistence)
        self.budget_charger = BudgetCharger(self.persistence)
        # Default-built from collaborators so production gets the live impl;
        # tests inject ``InMemoryUsageRecorder`` to assert records directly.
        self.usage_recorder: UsageRecorder = usage_recorder or PostgresUsageRecorder(
            persistence=self.persistence,
            pricing_catalog=self.pricing_catalog,
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

        # Pre-run budget preflight. Done BEFORE flipping status to RUNNING so a Deny
        # leaves the run in QUEUED→FAILED with a distinct safe_error_code.
        budget_decision = await self._preflight_budgets(run, command)
        if isinstance(budget_decision, BudgetPreflightDeny):
            await self._reject_run_for_budget(run, budget_decision)
            return

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
        budget_guard = await self._build_tool_budget_guard(run)
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
        # Per-run ``/workspace/`` backend. Held across the try so the finally can
        # release its pinned broker grant snapshot (``/v1/runs/end``) on every
        # exit path — completion, failure, timeout, or cancel.
        workspace_backend: object | None = None
        try:
            tool_observation_index = await self._tool_observation_index(command, run)
            workspace_backend = await self._workspace_backend_for_run(command)
            dependencies = self._dependencies_for_run(
                command,
                tool_observation_index,
                workspace_backend=workspace_backend,
            )
            mcp_display_token = McpDisplayRegistryContext.bind_for_run(
                mcp_display_registry
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
                context=await self._hydrated_runtime_context(command.runtime_context),
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
            await self.run_termination.terminate(
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
            )
            self.stream_event_mapper.message_processor.discard_ledger(run.run_id)
            self.stream_event_mapper.update_processor.discard_metrics(run.run_id)
            return
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
            await self.run_termination.terminate(
                run=failed,
                terminal_status=AgentRunStatus.FAILED,
                reason=termination_reason,
                summary="Run failed",
                cause=exc,
            )
            await self.audit_emitter.emit_run_failed(
                failed,
                status=AgentRunStatus.FAILED,
                error_class=type(exc).__name__,
                error_code=ToolErrorCode.TOOL_EXCEPTION.value,
                duration_ms=int((time.perf_counter() - run_start_perf) * 1000),
            )
            await self._record_run_usage(
                failed,
                metrics=metrics,
                completed_at=failed.completed_at or datetime.now(timezone.utc),
                status=AgentRunStatus.FAILED.value,
                budget_reservations=budget_reservations,
            )
            self.stream_event_mapper.message_processor.discard_ledger(run.run_id)
            self.stream_event_mapper.update_processor.discard_metrics(run.run_id)
            raise
        finally:
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
        await self.run_termination.terminate(
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
        )

    async def _preflight_budgets(
        self,
        run: RunRecord,
        command: RuntimeRunCommand,
    ):
        """Estimate the run's spend and check it against active budgets; fails open on transient errors."""

        try:
            pricing = await self.pricing_catalog.lookup(
                provider=run.model_provider,
                model_name=run.model_name,
                region="global",
                at=datetime.now(timezone.utc),
            )
            model_profile = command.runtime_context.model_profile
            # Conservative proxy: 4 chars/token × configured input window.
            # Over-estimating delays a true Deny; it never silently busts a hard cap.
            # ``max_input_tokens`` and ``max_output_tokens`` are first-class
            # fields on ``ModelConfig`` — depth-scaled values land here via
            # ``DepthBudgetTable.apply`` at run-create, so this is the single
            # read site for the post-mapped output cap.
            prompt_chars = model_profile.max_input_tokens * 4
            estimate = BudgetEstimator.estimate(
                prompt_chars=prompt_chars,
                max_output_tokens=model_profile.max_output_tokens,
                pricing=pricing,
            )
            return await self.budget_enforcer.preflight(
                org_id=command.org_id,
                user_id=command.user_id,
                run_id=command.run_id,
                estimate=estimate,
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "budget_preflight_failed",
                extra={"metadata": {"run_id": command.run_id}},
                exc_info=True,
            )
            return BudgetPreflightAllow()

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
    ) -> None:
        """Persist the per-run and per-LLM-call usage records, then charge budgets.

        Both records share the same ``pricing_at`` snapshot so a clock boundary
        mid-run produces one pricing version. The recorder is fail-soft — write
        failures are absorbed rather than propagated to the run lifecycle.
        """

        usage_record = metrics.to_usage_record(
            run, completed_at=completed_at, status=status
        )
        run_result = await self.usage_recorder.record_run(
            usage_record, pricing_at=completed_at
        )
        for call_record in metrics.model_call_usage_records(run, trace_id=run.trace_id):
            await self.usage_recorder.record_call(call_record, pricing_at=completed_at)
        # Apply observed spend against budgets; idempotent on run_id. Preflight
        # reservations are consumed in the same call so the budget reaper skips them.
        await self._charge_budgets(
            run,
            observed_micro_usd=run_result.cost_micro_usd,
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
        records = await self.persistence.list_messages(
            org_id=command.org_id,
            conversation_id=command.conversation_id,
            limit=200,
        )
        selected = self._selected_message_chain(records, run.user_message_id)
        messages = [
            {
                self._Fields.ROLE: message.role.value,
                self._Fields.CONTENT: self._message_content_for_runtime(message),
            }
            for message in selected
            if message.role
            in {MessageRole.USER, MessageRole.ASSISTANT, MessageRole.SYSTEM}
        ]
        observations = (
            tool_observation_index
            or await self._tool_observation_index_from_selected(
                command,
                run,
                selected,
            )
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

        return FileStoreWorkerWiring(self.event_store)

    def _file_backend_store(self) -> object | None:
        """Return the active file store, or ``None`` on non-file backends."""

        return self._file_store_wiring().file_store()

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
        self, command: RuntimeRunCommand
    ) -> object | None:
        """Construct the per-run ``/workspace/`` backend, or ``None``.

        Gated on the desktop capability broker (env config + the run's active
        grant snapshot). Off the desktop path — web / postgres / in-memory — the
        broker env is absent, so this returns ``None`` and the factory composes
        no ``/workspace/`` route, leaving those images byte-identical. Broker
        unavailability or zero active grants likewise yield ``None`` (fail-soft).

        When a writable grant is present, the write triple's durable half — the
        content-addressed object store (``FileObjectStore``) plus a
        snapshot-event emitter — is threaded in so the wiring can mint the run's
        ``run_capability_context`` and enable the approval-gated write path. Both
        are ``None`` off the file backend, so the write path stays inert.
        """

        file_store = self._file_store_wiring().file_store()
        snapshot_store = (
            getattr(file_store, "object_store", None)
            if file_store is not None
            else None
        )
        snapshot_emitter = (
            self._workspace_snapshot_emitter(command)
            if snapshot_store is not None
            else None
        )
        return await WorkspaceBackendWorkerWiring(
            snapshot_store=snapshot_store,
            snapshot_emitter=snapshot_emitter,
        ).workspace_backend()

    def _workspace_snapshot_emitter(self, command: RuntimeRunCommand) -> object:
        """Build the emitter the workspace backend records pre-image references through."""
        from runtime_worker.workspace_backend_wiring import (  # noqa: PLC0415
            WorkspaceSnapshotEventEmitter,
        )

        return WorkspaceSnapshotEventEmitter(
            event_producer=self.event_producer,
            persistence=self.persistence,
            org_id=command.org_id,
            run_id=command.run_id,
        )

    def _dependencies_for_run(
        self,
        command: RuntimeRunCommand,
        tool_observation_index: ToolObservationIndex,
        *,
        workspace_backend: object | None = None,
    ) -> RuntimeDependencies:
        """Build ``RuntimeDependencies`` augmented with per-run backends (drafts, subagent artifacts, workspace)."""
        dependencies = self.dependencies_factory(command.runtime_context)
        update: dict[str, object] = {
            "subagent_artifacts_backend": self._subagent_artifacts_backend(command),
        }
        # Route `/workspace/<mount>/<path>` reads to the user-granted host
        # folders exposed by the desktop capability broker. Desktop only —
        # `None` (unrouted) on every other backend and when no folders are
        # granted, so those paths stay on the default `StateBackend`.
        if workspace_backend is not None:
            update["workspace_backend"] = workspace_backend
        # Route `/large_tool_results/<sha256>` reads to the object store so the
        # supervisor can pull back an offloaded tool result. Desktop only —
        # `None` (unrouted) on every other backend.
        large_tool_results_backend = (
            self._file_store_wiring().large_tool_results_backend()
        )
        if large_tool_results_backend is not None:
            update["large_tool_results_backend"] = large_tool_results_backend
        if self.draft_store is not None:
            # Tenant identity is bound at construction so the model cannot inject
            # org_id via path strings when writing to /drafts/<uuid>.md.
            from agent_runtime.capabilities.backends import (  # noqa: PLC0415 — break import cycle
                DraftBackend,
            )

            update["drafts_backend"] = DraftBackend(
                store=self.draft_store,
                org_id=command.org_id,
                conversation_id=command.conversation_id,
                run_id=command.run_id,
                user_id=command.runtime_context.user_id,
                emit_event=self._draft_event_emitter(command),
            )
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
        )
        code_mode_tool = capability_tools.code_mode_tool()
        if code_mode_tool is not None:
            update["code_mode_tool"] = code_mode_tool
        sandbox_execute_tool = capability_tools.sandbox_execute_tool()
        if sandbox_execute_tool is not None:
            update["sandbox_execute_tool"] = sandbox_execute_tool
        return dependencies.model_copy(update=update)

    def _draft_event_emitter(
        self, command: RuntimeRunCommand
    ) -> "Callable[[object], object]":
        """Build the ``emit_event`` closure DraftBackend uses to emit DRAFT_UPDATED.

        We reuse the existing :class:`RuntimeEventProducer` so every emission
        flows through redaction + presentation projection + the run sequence
        cursor — same path as every other API-authored event.
        """

        from agent_runtime.api.constants import Keys, Values  # noqa: PLC0415
        from agent_runtime.capabilities.backends import (  # noqa: PLC0415
            DraftSurfaceProjector,
        )
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
            # Generative-UI (PRD-02b): attach the same ``message`` surface the
            # in-package emitter builds, so drafts written during the live run
            # carry ``surface_uri`` + ``surface`` (section diff on v2+). Shared
            # builder — no envelope duplication; best-effort + flag-gated.
            await DraftSurfaceProjector.attach(payload, record, self.draft_store)  # type: ignore[arg-type]
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
        """Insert a SYSTEM message with prior tool context just before the last USER message."""
        insert_at = len(messages)
        for index in range(len(messages) - 1, -1, -1):
            if messages[index][cls._Fields.ROLE] == MessageRole.USER.value:
                insert_at = index
                break
        messages.insert(
            insert_at,
            {
                cls._Fields.ROLE: MessageRole.SYSTEM.value,
                cls._Fields.CONTENT: prompt_context,
            },
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
            except Exception:
                logging.getLogger(__name__).warning(
                    "tool_call_reconcile.failed run=%s call_id=%s outcome=%s",
                    run.run_id,
                    entry.call_id,
                    outcome.value,
                    exc_info=True,
                )

    async def _build_tool_budget_guard(self, run: RunRecord) -> ToolBudgetGuard | None:
        """Load the org's per-tool budgets and build a per-run guard.

        Returns ``None`` when the persistence port doesn't expose the
        method yet (older test stubs) or when the org has no rows.
        Reuses the per-run :class:`ToolCallLedger` already maintained by
        the stream orchestrator so admission decisions and the
        ``tool_call_started``/``tool_result`` reconciler share state.
        """

        loader = getattr(self.persistence, "list_tool_budgets_for_org", None)
        if loader is None:
            return None
        try:
            budgets = await loader(org_id=run.org_id)
        except Exception:
            logging.getLogger(__name__).warning(
                "tool_budget_load_failed", exc_info=True
            )
            return None
        if not budgets:
            return None
        ledger = self.stream_event_mapper.message_processor.ledger_for_run(run.run_id)
        return ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(budgets),
            ledger=ledger,
            run=run,
            event_producer=self.event_producer,
        )

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
