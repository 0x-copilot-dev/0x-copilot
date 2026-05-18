"""Queued approval-resolution command handling."""

from __future__ import annotations

import inspect
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
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.run_metrics import AssistantRunMetrics
from runtime_worker.stream_events import StreamOrchestrator
from runtime_worker.stream_messages import StreamTextHelper
from runtime_worker.streaming_executor import StreamingExecutor

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
    ) -> None:
        self.persistence: PersistencePort = persistence
        self.event_store: EventStorePort = event_store
        self.settings = settings or RuntimeSettings.load()
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
        self.stream_event_mapper = StreamOrchestrator(self.event_producer)
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
            )
            return
        approval_kind = StreamTextHelper.extract(
            metadata.get(self._Fields.APPROVAL_KIND)
        )
        if (
            metadata.get(self._Fields.NATIVE_INTERRUPT_ID) is None
            and approval_kind != ApiValues.ApprovalKind.MCP_AUTH
        ):
            return

        # The user's answer flows back to the agent via the LangGraph resume value
        # (persisted as part of the tool-result event). It is NOT appended as a USER
        # message — that would render a stray bubble disconnected from the question card.
        resume = self._resume_payload(command, metadata)
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
        dependencies = self.dependencies_factory(running.runtime_context)
        mcp_display_registry: dict[str, ToolDisplayTemplate] = {}
        mcp_display_token = McpDisplayRegistryContext.bind_for_run(mcp_display_registry)
        display_token = ToolDisplayLookupContext.bind_for_run(
            RuntimeRunHandler._build_tool_display_lookup(dependencies.tool_registry)
        )
        try:
            harness_or_coro = self.agent_factory(
                context=running.runtime_context,
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
    ) -> dict[str, object]:
        """Build the LangGraph resume value dict appropriate for the approval kind."""
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
    ) -> None:
        """Apply a draft-send approval: persist the new draft version, emit ``DRAFT_UPDATED``, and complete the run.

        Approve → ``status=sent``; Reject → ``status=draft``. Skips silently when the draft
        store is absent or the draft is no longer in ``send_pending_approval`` state.
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
            # State changed since the approval was posted (e.g. a concurrent discard).
            return

        if decision is ApprovalDecision.APPROVED:
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
        persisted = await self._draft_store.insert_version(next_record)
        await self._emit_draft_updated(run=run, record=persisted)
        await self._write_draft_audit(
            run=run,
            record=persisted,
            action=audit_action,
            extra_metadata={
                "approval_id": getattr(approval, "approval_id", None),
                "decided_by_user_id": decided_by_user_id,
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
