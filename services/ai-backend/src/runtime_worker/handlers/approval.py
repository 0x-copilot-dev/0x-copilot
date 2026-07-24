"""Queued approval-resolution command handling."""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import datetime, timezone

from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.api.constants import Values as ApiValues
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_termination import (
    RunTerminationCoordinator,
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
from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate
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
from agent_runtime.execution.factory import (
    RuntimeHarness,
    acreate_agent_runtime,
)
from agent_runtime.execution.providers.citation_pipeline import CitationStreamPipeline
from agent_runtime.execution.runtime import astream_runtime_resume
from agent_runtime.persistence import with_optimistic_retry
from agent_runtime.persistence.ports import ConversationToolOrdinalStorePort
from agent_runtime.persistence.records import (
    BatchOutcomeStatus,
    BatchTransitionOutcome,
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
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.file_store_wiring import FileStoreWorkerWiring
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.run_metrics import AssistantRunMetrics
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
RuntimeResumer = Callable[[RuntimeHarness, object], AsyncIterator[object]]

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
    ) -> None:
        self.persistence: PersistencePort = persistence
        self.event_store: EventStorePort = event_store
        self.settings = settings or RuntimeSettings.load()
        # BYOK re-hydration on resume: the persisted run record's context was
        # serialized without ``provider_keys`` (excluded field), so the resumed
        # harness re-fetches them in memory only — same seam as the run handler.
        self._provider_keys_hydrator = (
            ProviderKeysHydrator(resolver=user_policies_resolver)
            if user_policies_resolver is not None
            else None
        )
        # Same pattern as ``RuntimeRunHandler``: caller-supplied factory wins
        # (tests inject their own); otherwise the default factory threads the
        # process-wide MCP discovery cache through ``RuntimeDependencies``.
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
        )
        # Single source of truth for the desktop file-store gate shared with the
        # run handler. On non-file backends every method returns ``None`` so the
        # resume path stays byte-identical to before (offloader ``None`` → inline).
        self._file_store_wiring = FileStoreWorkerWiring(self.event_store)
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
        dependencies = self._dependencies_for_resume(
            running, workspace_backend=workspace_backend
        )
        mcp_display_registry: dict[str, ToolDisplayTemplate] = {}
        mcp_display_token = McpDisplayRegistryContext.bind_for_run(mcp_display_registry)
        display_token = ToolDisplayLookupContext.bind_for_run(
            RuntimeRunHandler._build_tool_display_lookup(dependencies.tool_registry)
        )
        try:
            resume_context = running.runtime_context
            if self._provider_keys_hydrator is not None:
                resume_context = await self._provider_keys_hydrator.hydrate(
                    resume_context
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
            metrics = AssistantRunMetrics.from_run(running)
            result = await self._stream_resume(
                run=running,
                harness=harness,
                resume=resume,
                metrics=metrics,
            )
            if RuntimeRunHandler._is_action_interrupt(result):
                await with_optimistic_retry(
                    lambda: self.persistence.update_run_status(
                        run_id=run.run_id,
                        status=AgentRunStatus.WAITING_FOR_APPROVAL,
                    )
                )
                return
            final_text = RuntimeRunHandler._extract_final_text(result)
            await self._complete_run_with_result(running, final_text, metrics)
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
            raise
        finally:
            CitationResolver.unbind(resolver_token)
            ConversationOrdinalAllocator.unbind(allocator_token)
            ToolDisplayLookupContext.unbind(display_token)
            McpDisplayRegistryContext.unbind(mcp_display_token)
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
        desktop broker, and — when a writable grant is present — threaded with the
        write triple's durable half (the object store + a snapshot-event emitter)
        so the approved host write that lands DURING resume is snapshotted and
        approval-gated exactly as on the initial run path. Both are ``None`` off
        the file backend, so the write path stays inert.
        """
        file_store = self._file_store_wiring.file_store()
        snapshot_store = (
            getattr(file_store, "object_store", None)
            if file_store is not None
            else None
        )
        snapshot_emitter = (
            self._workspace_snapshot_emitter(run)
            if snapshot_store is not None
            else None
        )
        return await WorkspaceBackendWorkerWiring(
            snapshot_store=snapshot_store,
            snapshot_emitter=snapshot_emitter,
        ).workspace_backend()

    def _workspace_snapshot_emitter(self, run: RunRecord) -> object:
        """Build the emitter the workspace backend records pre-image references through."""
        from runtime_worker.workspace_backend_wiring import (  # noqa: PLC0415
            WorkspaceSnapshotEventEmitter,
        )

        return WorkspaceSnapshotEventEmitter(
            event_producer=self.event_producer,
            persistence=self.persistence,
            org_id=run.org_id,
            run_id=run.run_id,
        )

    def _dependencies_for_resume(
        self,
        run: RunRecord,
        *,
        workspace_backend: object | None = None,
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

        dependencies = self.dependencies_factory(run.runtime_context)
        update: dict[str, object] = {}
        if workspace_backend is not None:
            update["workspace_backend"] = workspace_backend
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
        if self._draft_store is not None:
            # Tenant identity is bound at construction so the model cannot inject
            # org_id via path strings when writing to /drafts/<uuid>.md.
            from agent_runtime.capabilities.backends import (  # noqa: PLC0415 — break import cycle
                DraftBackend,
            )

            update["drafts_backend"] = DraftBackend(
                store=self._draft_store,
                org_id=run.org_id,
                conversation_id=run.conversation_id,
                run_id=run.run_id,
                user_id=run.runtime_context.user_id,
                emit_event=self._draft_backend_event_emitter(run),
            )
        return dependencies.model_copy(update=update)

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
    ) -> object:
        """Stream a resumed LangGraph run and return the composed final result."""
        result = await StreamingExecutor.run(
            stream=self.runtime_resumer(harness, resume),
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
    ) -> None:
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
        if not draft_id:
            return
        latest = await self._draft_store.latest(org_id=run.org_id, draft_id=draft_id)
        if latest is None or latest.status is not DraftStatus.SEND_PENDING_APPROVAL:
            # State changed since the approval was posted (e.g. a concurrent
            # discard, or an already-applied send). Idempotent no-op.
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
            decided_by_user_id=decided_by_user_id or run.user_id,
            status=terminal_status,
        )
        applied_edit_keys: list[str] = []
        if decision is ApprovalDecision.APPROVE_WITH_EDITS and edits is not None:
            next_record, applied_edit_keys = self._apply_edits_to_draft(
                record=next_record, edits=edits
            )
        persisted = await self._draft_store.insert_version(next_record)
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
        """Return whether this draft has a ``write.staged`` event on the run's ledger.

        A ``write.staged`` for this draft means the write was re-homed onto the v2
        staged-write engine (the WYSIWYG-guarded path); a stale v1 approval must
        NOT independently send. Scans the run's persisted events for a
        ``write.staged`` whose ``proposal_ref`` names ``draft_id``. Best-effort: any
        read failure returns ``False`` (the v1 send proceeds unchanged — the guard
        never breaks the existing flow, it only refuses a proven-superseded send).
        """

        from agent_runtime.surfaces_v2.ledger_models import (  # noqa: PLC0415
            LedgerEventType,
        )
        from agent_runtime.surfaces_v2.staging import DraftRef  # noqa: PLC0415

        try:
            events = await self.event_store.list_events_after(
                org_id=run.org_id, run_id=run.run_id, after_sequence=0
            )
        except Exception:  # noqa: BLE001 — never break the v1 flow on a read error.
            return False
        staged_value = LedgerEventType.WRITE_STAGED.value
        for event in events:
            event_type = getattr(getattr(event, "event_type", None), "value", None)
            if event_type != staged_value:
                continue
            payload = getattr(event, "payload", None)
            if not isinstance(payload, Mapping):
                continue
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
        decided_by_user_id: str,
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
            user_id=decided_by_user_id,
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
