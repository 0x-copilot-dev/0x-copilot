"""Queued run command handling."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
import asyncio
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
from agent_runtime.api.async_ports import AsyncEventStorePort, AsyncPersistencePort
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.api.presentation import (
    ToolDisplayLookup,
    ToolDisplayLookupContext,
)
from agent_runtime.capabilities.mcp.descriptor_registry import (
    McpDisplayRegistryContext,
)
from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate
from agent_runtime.observability.usage_attribution import UsageAttributionResolver
from agent_runtime.persistence.ports import (
    CitationStorePort,
    ConversationToolOrdinalStorePort,
    DraftStorePort,
)
from runtime_adapters.async_wrappers import (
    adapt_event_store_to_async,
    adapt_persistence_to_async,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness, create_agent_runtime
from agent_runtime.execution.providers.citation_pipeline import CitationStreamPipeline
from agent_runtime.execution.runtime import ainvoke_runtime, astream_runtime
from agent_runtime.persistence import with_optimistic_retry
from agent_runtime.persistence.records import BudgetReservationRecord
from agent_runtime.pricing import CostCalculator, ModelPricingCatalog
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
AgentFactory = Callable[..., RuntimeHarness]
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
        persistence: PersistencePort | AsyncPersistencePort,
        event_store: EventStorePort | AsyncEventStorePort,
        dependencies_factory: RuntimeDependenciesFactory | None = None,
        settings: RuntimeSettings | None = None,
        agent_factory: AgentFactory = create_agent_runtime,
        runtime_invoker: RuntimeInvoker = ainvoke_runtime,
        runtime_streamer: RuntimeStreamer = astream_runtime,
        on_event_appended: Callable[[str], None] | None = None,
        citation_store: CitationStorePort | None = None,
        draft_store: DraftStorePort | None = None,
        conversation_tool_ordinal_store: (
            ConversationToolOrdinalStorePort | None
        ) = None,
    ) -> None:
        self.persistence: AsyncPersistencePort = adapt_persistence_to_async(persistence)
        self.event_store: AsyncEventStorePort = adapt_event_store_to_async(event_store)
        self.settings = settings or RuntimeSettings.load()
        self.dependencies_factory = (
            dependencies_factory or DefaultRuntimeDependenciesFactory(self.settings)
        )
        self.agent_factory = agent_factory
        self.runtime_invoker = runtime_invoker
        self.runtime_streamer = runtime_streamer
        # Citations live registry (PR 1.1). When ``citation_store`` is None
        # the ledger never binds and ``CitationLedger.cite`` returns the
        # empty string — citations degrade to absent without breaking runs.
        self.citation_store = citation_store
        # Workspace-pane drafts (PR 1.3 + 1.3.5). When ``draft_store`` is
        # None the run handler does not construct a DraftBackend; the
        # agent's `/drafts/` writes fall through to deepagents'
        # ``StateBackend`` default and become non-persistent in-state files
        # for that run only. This is the legacy / unconfigured fallback.
        self.draft_store = draft_store
        # PR 04 — persistent (conversation_ordinal ↔ tool_call_id)
        # binding store. Phase 3 makes the allocator write through to
        # this on every ``allocate_for_tool_call`` and read it back on
        # bind so resumes / cross-turn citation resolve to the canonical
        # binding rather than re-deriving ordinals positionally. Until
        # Phase 3 lands the store is held but unused — wiring it now
        # keeps the constructor surface stable across the migration.
        self.conversation_tool_ordinal_store = conversation_tool_ordinal_store
        self.event_producer = RuntimeEventProducer(
            persistence=self.persistence,
            event_store=self.event_store,
            on_event_appended=on_event_appended,
        )
        self.stream_event_mapper = StreamOrchestrator(self.event_producer)
        self._runtime_streamer_explicit = runtime_streamer is not astream_runtime
        self.audit_emitter = WorkerAuditEmitter(persistence=self.persistence)
        self.pricing_catalog = ModelPricingCatalog(self.persistence)
        self.budget_enforcer = BudgetEnforcer(self.persistence)
        self.budget_charger = BudgetCharger(self.persistence)

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

        # B7 — pre-run budget preflight. Allow / Warn / Deny.
        # Done BEFORE flipping status to RUNNING so a Deny path leaves
        # the run in QUEUED→FAILED transition with a distinct
        # safe_error_code='budget_exceeded' (so the UI can show
        # "budget exceeded" instead of generic failure).
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
        # PR 1.1-rev2 — model-declared citation pointers.
        #
        # The ordinal allocator owns a per-conversation monotonic counter
        # used by tool wrappers to prefix each tool result with
        # ``[Tool call #N — cite as [[N]]]`` so the model has a stable
        # pointer to embed in its prose. The seeder counts prior
        # ``TOOL_CALL_STARTED`` events on the active branch so the new
        # run's ordinals don't collide with anything already persisted.
        #
        # The resolver watches streamed assistant text for ``[[N]]``
        # markers and emits one ``citation_made`` event per resolved
        # marker — same wire as every other event, no parallel pipe.
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
        # B8 — per-tool budget guard. Loads the org's
        # ``runtime_tool_budgets`` snapshot, binds it alongside the
        # in-flight ``ToolCallLedger`` so every LangChain
        # :class:`ToolBudgetGuardedTool` invocation goes through
        # :meth:`ToolBudgetMiddleware.check_admit` before reaching the
        # underlying tool. ``None`` when the org has no budgets — the
        # guard is unbound and the wrapper is a passthrough.
        budget_guard = await self._build_tool_budget_guard(run)
        budget_token = (
            ToolBudgetGuard.bind_for_run(budget_guard)
            if budget_guard is not None
            else None
        )
        # PR 3.3 — non-blocking MCP discovery service. Built per-run so
        # idempotency / audit / event emission share the same RunRecord
        # the ledger uses. Returns ``None`` when the feature is off; the
        # tool short-circuits to ``discovery_disabled`` in that case.
        discovery_service: McpDiscoveryService | None = None
        discovery_token: object | None = None
        # Polish-removal Phase 1 + 2.B (docs/refactor/01-presentation-polish-removal.md):
        # bind the per-run tool display lookup so every event the producer
        # emits during this run consults the registry without the producer
        # holding a direct reference. ``None`` until ``dependencies`` is
        # built; cleared in the finally block.
        #
        # Bind the MCP descriptor registry FIRST so that any descriptor
        # registered during the run (lazily, when the agent calls
        # ``load_mcp_server``) lands in the dict our composite lookup
        # consults — not in a pre-bind void.
        display_token: object | None = None
        mcp_display_token: object | None = None
        mcp_display_registry: dict[str, ToolDisplayTemplate] = {}
        try:
            tool_observation_index = await self._tool_observation_index(command, run)
            dependencies = self._dependencies_for_run(
                command,
                tool_observation_index,
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
            discovery_token = (
                McpDiscoveryService.bind_for_run(discovery_service)
                if discovery_service is not None
                else None
            )
            harness = self.agent_factory(
                context=command.runtime_context,
                dependencies=dependencies,
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
                # PR 1.1-rev2 — sealed list of ordinals the model cited
                # in this turn's prose, in first-occurrence order. The
                # FE consumes this for the share-recipient view and the
                # archive replay path so chips render before any
                # ``citation_made`` events arrive (the events are still
                # the live truth; this is a convenience snapshot).
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
            await self._append_lifecycle(
                failed, RuntimeApiEventType.RUN_FAILED, "Run timed out"
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
            await self._append_lifecycle(
                failed, RuntimeApiEventType.RUN_FAILED, "Run failed"
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

        completed = await with_optimistic_retry(
            lambda: self.persistence.update_run_status(
                run_id=command.run_id, status=AgentRunStatus.COMPLETED
            )
        )
        self.stream_event_mapper.message_processor.discard_ledger(run.run_id)
        self.stream_event_mapper.update_processor.discard_metrics(run.run_id)
        completed_at = completed.completed_at or datetime.now(timezone.utc)
        metrics_payload = metrics.to_payload(completed_at=completed_at)
        await self._append_lifecycle(
            completed,
            RuntimeApiEventType.RUN_COMPLETED,
            "Run completed",
            payload=AssistantRunMetrics.with_payload(
                {self._Fields.STATUS: RuntimeApiEventType.RUN_COMPLETED.value},
                metrics_payload,
            ),
            metadata=AssistantRunMetrics.metadata(metrics_payload),
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
        """B7: estimate the run's spend and check it against active budgets.

        Failures fail-open: a transient persistence error must not block
        the run. We log + return Allow so the run proceeds as if no
        budgets were configured. Hard caps are still enforced when the
        DB is healthy, which is the common case.
        """

        try:
            pricing = await self.pricing_catalog.lookup(
                provider=run.model_provider,
                model_name=run.model_name,
                region="global",
                at=datetime.now(timezone.utc),
            )
            request_options = command.runtime_context.model_profile
            # Conservative pre-build proxy: 4 chars/token × the model's
            # configured input window. The estimator multiplies by the
            # safety margin and the post-run charge keys on observed
            # tokens — so over-estimating here only delays a true Deny,
            # it never silently busts a hard cap.
            max_input_tokens = getattr(request_options, "max_input_tokens", None)
            prompt_chars = (max_input_tokens or 0) * 4
            estimate = BudgetEstimator.estimate(
                prompt_chars=prompt_chars,
                max_output_tokens=getattr(request_options, "max_output_tokens", None),
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
        """Best-effort write of the per-run usage row + cost stamp (B1, B3).

        The run-completion event is the source of truth; this denormalized
        row is a derived aggregate that powers fast aggregations (B4) and
        budget enforcement (B7). A failure here must never break the run
        lifecycle, so we swallow exceptions and let observability surface
        them as a metric.

        After the row is written we look up pricing-as-of ``completed_at``
        and stamp the cost. Pricing miss → row stays at ``cost_micro_usd
        IS NULL`` (B3 spec: unknown models are null-safe).

        Finally (B7) we charge the observed spend against any matching
        budgets, idempotent on ``run_id``. Reservations from the
        preflight are consumed inside the charger so the reaper skips
        them.
        """

        cost_micro_usd_observed: int | None = None
        observed_tokens = 0
        try:
            usage_record = metrics.to_usage_record(
                run, completed_at=completed_at, status=status
            )
            await self.persistence.record_run_usage(usage_record)
            observed_tokens = usage_record.total_tokens
        except Exception:
            logging.getLogger(__name__).warning(
                "runtime_run_usage_write_failed",
                extra={"metadata": {"run_id": run.run_id}},
                exc_info=True,
            )
            return
        await self._record_per_call_usage(run, metrics=metrics)
        try:
            pricing = await self.pricing_catalog.lookup(
                provider=run.model_provider,
                model_name=run.model_name,
                region="global",
                at=completed_at,
            )
            if pricing is not None:
                computed_cost: int = CostCalculator.compute(
                    input_tokens=usage_record.input_tokens,
                    output_tokens=usage_record.output_tokens,
                    cached_input_tokens=usage_record.cached_input_tokens,
                    pricing=pricing,
                )
                cost_micro_usd_observed = computed_cost
                await self.persistence.update_run_usage_cost(
                    run_id=run.run_id,
                    cost_micro_usd=computed_cost,
                    pricing_id=pricing.id,
                    pricing_version=pricing.pricing_version,
                )
        except Exception:
            logging.getLogger(__name__).warning(
                "runtime_run_usage_cost_write_failed",
                extra={"metadata": {"run_id": run.run_id}},
                exc_info=True,
            )
        # B7 — apply observed spend against active budgets. Idempotent on
        # run_id; reservations from preflight are consumed in the same
        # call so the reaper skips them.
        await self._charge_budgets(
            run,
            observed_micro_usd=cost_micro_usd_observed,
            observed_tokens=observed_tokens,
            reservations=budget_reservations,
        )

    async def _record_per_call_usage(
        self,
        run: RunRecord,
        *,
        metrics: AssistantRunMetrics,
    ) -> None:
        """Best-effort write of per-LLM-call usage rows (B2).

        Reconciliation invariant: ``sum(model_call_usage rows for run_id)``
        equals ``runtime_run_usage`` for that run. The records are built
        from the same accumulator that produced the run-level row, so the
        invariant holds by construction.

        Cost stamping for per-call rows is best-effort: each row gets
        priced against the same pricing snapshot as the run-level row.
        Failures here never break the run lifecycle.
        """

        try:
            records = metrics.model_call_usage_records(run, trace_id=run.trace_id)
            if not records:
                return
            for record in records:
                await self.persistence.record_model_call_usage(record)
        except Exception:
            logging.getLogger(__name__).warning(
                "runtime_model_call_usage_write_failed",
                extra={"metadata": {"run_id": run.run_id}},
                exc_info=True,
            )
            return
        # Cost stamp per call.
        try:
            pricing = await self.pricing_catalog.lookup(
                provider=run.model_provider,
                model_name=run.model_name,
                region="global",
                at=datetime.now(timezone.utc),
            )
            if pricing is None:
                return
            for record in records:
                cost_micro_usd = CostCalculator.compute(
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    cached_input_tokens=record.cached_input_tokens,
                    pricing=pricing,
                )
                await self.persistence.update_model_call_usage_cost(
                    usage_id=record.id,
                    cost_micro_usd=cost_micro_usd,
                    pricing_id=pricing.id,
                    pricing_version=pricing.pricing_version,
                )
        except Exception:
            logging.getLogger(__name__).warning(
                "runtime_model_call_usage_cost_write_failed",
                extra={"metadata": {"run_id": run.run_id}},
                exc_info=True,
            )

    async def _messages_for_run(
        self,
        command: RuntimeRunCommand,
        run: RunRecord,
        *,
        tool_observation_index: ToolObservationIndex | None = None,
    ) -> tuple[dict[str, str], ...]:
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

        Polish-removal Phases 1 + 2.B (docs/refactor/01-presentation-polish-removal.md):

        - Phase 1 — probe ``tool_registry`` for ``display_for(name)``. Returns
          a stub when the registry doesn't expose the method (today's
          production chain wraps ``WebSearchToolRegistry``, which doesn't).
        - Phase 2.B — fall through to the per-run MCP descriptor registry
          populated by ``BackendMcpClient._tool_descriptor`` as servers
          load. This is what makes synthesised MCP templates visible to
          ``PresentationGenerator``.

        Order: tool_registry first (author-written templates beat
        synthesised MCP templates if a name collides), MCP registry second.
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

    def _dependencies_for_run(
        self,
        command: RuntimeRunCommand,
        tool_observation_index: ToolObservationIndex,
    ) -> RuntimeDependencies:
        dependencies = self.dependencies_factory(command.runtime_context)
        update: dict[str, object] = {
            "subagent_artifacts_backend": SubagentArtifactsBackend(
                event_store=self.event_store,
                persistence=self.persistence,
                org_id=command.org_id,
                conversation_id=command.conversation_id,
                current_run_id=command.run_id,
            ),
            # PR 3.3 — flip the factory's tool-registration switch from
            # the worker's loaded settings. Defaults to ``False`` so a
            # deployment that hasn't opted in never sees the tool.
            "mcp_discovery_enabled": self.settings.mcp.discovery_enabled,
        }
        if self.draft_store is not None:
            # PR 1.3.5 — construct a DraftBackend per run so the agent's
            # write_file/edit_file calls to `/drafts/<uuid>.md` route
            # through to the runtime_drafts table. Tenant identity is bound
            # at construction (org_id, conversation_id, run_id, user_id);
            # the model can never inject org_id via path strings.
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
        from agent_runtime.execution.contracts import StreamEventSource  # noqa: PLC0415
        from runtime_api.schemas import RuntimeApiEventType  # noqa: PLC0415

        async def _emit(record: object) -> None:
            # Lazy-attribute access keeps this file decoupled from DraftRecord.
            payload = {
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
        # PR 04 — pass the persistent binding store so the builder can
        # source ordinals from the canonical map instead of re-counting
        # TOOL_CALL_STARTED events. Passing ``None`` (worker constructed
        # without the store) is fine: observations come back without
        # ordinals and the prompt context omits ``cite as [[N]]`` hints.
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
        return (
            StreamTextHelper.extract(payload.get(cls._Fields.TEXT))
            or StreamTextHelper.extract(payload.get(cls._Fields.CONTENT))
            or cls._content_blocks_text(payload.get(cls._Fields.CONTENT))
        )

    @classmethod
    def _content_blocks_text(cls, value: object) -> str | None:
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
        if len(value) <= MAX_STRUCTURED_CONTEXT_CHARS:
            return value
        return f"{value[:MAX_STRUCTURED_CONTEXT_CHARS].rstrip()} [truncated]"

    @classmethod
    def _selected_message_chain(
        cls,
        records: Sequence[MessageRecord],
        user_message_id: str,
    ) -> tuple[MessageRecord, ...]:
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
                attribution=UsageAttributionResolver(self.persistence),
                track_subagents=True,
                citation_pipeline=CitationStreamPipeline.for_provider(
                    command.runtime_context.model_profile.provider
                ),
                # PR 1.1-rev2 — resolver was bound by the run-level
                # try-block; the executor pulls it from the active
                # ContextVar through the same mechanism every other
                # bound capability uses.
                citation_resolver=CitationResolver.active(),
            )
        return StreamingExecutor.compose_final(result)

    @classmethod
    def _is_action_interrupt(cls, result: object) -> bool:
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
        """B8 — load the org's per-tool budgets and build a per-run guard.

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
        """Build the per-conversation ordinal allocator from the binding store.

        PR 04 — replaces the prior positional-event-counting seeder.
        :meth:`ConversationOrdinalAllocator.for_conversation` reads every
        binding for ``conversation_id`` from
        ``agent_conversation_tool_ordinals`` (migration 0026), seeds the
        in-memory counter to ``max(conversation_ordinal)``, and the
        allocator writes through to the same table on every fresh
        ``allocate_for_tool_call``. Ordinals stay strictly monotonic
        across runs and across approval resumes, with no event-counting.

        When the worker was constructed without a binding store
        (replay / eval / specific unit tests), fall back to a memory-only
        allocator. Citations degrade to absent for that run rather than
        crashing the dispatch path.
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
    ) -> McpDiscoveryService | None:
        """Build a per-run :class:`McpDiscoveryService`, or ``None`` when off.

        The service mirrors the citation ledger: bound to the worker run
        once, exposed through a class-method (``offer``) so the
        ``suggest_mcp_connector`` tool reaches it without a runtime context
        in its signature. The auth-session creator (when registered with
        the MCP registry) is reused so the discovery card carries the same
        ``auth_url`` / ``expires_at`` fields the blocking gate emits.
        """

        if not dependencies.mcp_discovery_enabled:
            return None
        # The auth session creator is a per-provider capability on the
        # MCP registry (same lookup as the blocking ``auth_mcp`` tool
        # uses in :func:`agent_runtime.execution.factory._auth_session_creator`).
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
        if isinstance(message, Mapping):
            return cls._content_to_text(message.get(cls._Fields.CONTENT))
        return cls._content_to_text(getattr(message, cls._Fields.CONTENT, None))

    @classmethod
    def _content_to_text(cls, value: object) -> str | None:
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
        value = context.trace_metadata.get(key)
        return value if isinstance(value, str) and value.strip() else None
