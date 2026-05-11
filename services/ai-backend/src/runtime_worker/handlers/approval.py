"""Queued approval-resolution command handling."""

from __future__ import annotations

import asyncio
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

# PR 1.3.5 — discriminator written into ``approval.metadata['kind']`` by
# DraftService.send so this handler can route draft-send approvals through
# their own resolution path instead of the LangGraph resume path.
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
        # PR 3.2.5 Phase 3 — set on approval.metadata when the original
        # interrupt fired inside a subagent's subgraph. Drives the paired
        # ``SUBAGENT_RESUMED`` emit on resolution so the FE flips the
        # row's status back to ``running`` before any subsequent
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
    ) -> None:
        self.persistence: PersistencePort = persistence
        self.event_store: EventStorePort = event_store
        self.settings = settings or RuntimeSettings.load()
        self.dependencies_factory = (
            dependencies_factory or DefaultRuntimeDependenciesFactory(self.settings)
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
        # PR 1.3.5 — when a draft-send approval lands the handler routes
        # through ``_resolve_draft_send_approval`` instead of the LangGraph
        # resume path. The draft store must be provided for that path to
        # function; absent it, draft-send approvals fall through to the
        # default early-return (status transitions skipped) — surfaced as
        # an audit gap rather than a crash.
        self._draft_store = draft_store
        # PR 04 — bound at construction so the resumed allocator is
        # rebuilt from the persistent binding map rather than re-counting
        # events. Optional so handler unit tests can construct without
        # the store; production wiring (RuntimeWorker) always supplies
        # one (in-memory or postgres adapter).
        self._conversation_tool_ordinal_store: (
            ConversationToolOrdinalStorePort | None
        ) = conversation_tool_ordinal_store
        # PR 3.2.5 Phase 3 — explicit dedup so a transient retry of
        # ``handle()`` for the same approval cannot re-emit
        # ``SUBAGENT_RESUMED``. Upstream approval-status idempotency
        # generally short-circuits the second invocation before it reaches
        # the resume path, but this set is the belt-and-braces guarantee
        # that the FE reducer's ``running → running`` no-op never has to
        # absorb a duplicate (and that audit replay stays single-emit).
        # Keyed by ``(run_id, task_id)`` because handler instances are
        # scoped per-worker and outlive a single approval.
        self._resumed_task_ids: set[tuple[str, str]] = set()

    async def handle(self, command: RuntimeApprovalResolvedCommand) -> None:
        # PR 1.4 — two-stage approval forwarding. The API service has already
        # resolved the parent row to status=FORWARDED, inserted the child
        # row addressed to the recipient, emitted approval_resolved/
        # approval_forwarded/approval_requested events, and audited the
        # forward. The graph stays paused (run.status remains
        # WAITING_FOR_APPROVAL); resume hangs off the leaf child's
        # approve/reject which flows through the existing single-actor
        # path on a different approval_id. So: nothing to do here.
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
        # PR 1.3.5 — Workspace-pane draft-send approvals are conversation-
        # scoped events that don't suspend a LangGraph runtime. We detect
        # them here (after recording the decision through the existing
        # audit emitter) and handle the state transitions inline before
        # the LangGraph-resume path would have run.
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

        # The user's answer flows back to the agent via the LangGraph resume
        # value (and is persisted as part of the tool result event). We do NOT
        # append it as a top-level USER message — doing that surfaced the
        # answer as a stray user-message bubble disconnected from the
        # question card in the chat thread.
        resume = self._resume_payload(command, metadata)
        running = await with_optimistic_retry(
            lambda: self.persistence.update_run_status(
                run_id=run.run_id,
                status=AgentRunStatus.RUNNING,
            )
        )
        # PR 3.2.5 Phase 3 — if the original interrupt fired inside a
        # subagent's subgraph, emit ``SUBAGENT_RESUMED`` BEFORE invoking
        # the LangGraph resumer. The FE reducer keys on ``task_id`` to
        # flip the fleet row's state from ``paused`` back to ``running``;
        # ordering it ahead of the resume avoids a race where a tool
        # event from the resumed branch lands first and gets rendered
        # against a still-paused row.
        await self._maybe_emit_subagent_resumed(
            run=running,
            approval=approval,
            command=command,
        )
        # PR 1.1-rev2 — bind a fresh allocator + resolver for the resume.
        # ``handle_resolved`` runs in a separate async task from the
        # original ``RuntimeRunHandler.handle`` (the original task ended
        # with the run paused; this task is the queue's approval-resolved
        # callback). The original allocator/resolver were unbound when
        # the run paused, so we need a new pair seeded from the
        # already-persisted ``TOOL_CALL_STARTED`` events of all prior
        # runs in the conversation INCLUDING the run being resumed —
        # tools that fired before the pause already burned their
        # ordinals.
        allocator = await self._build_allocator_for_resume(running)
        allocator_token = ConversationOrdinalAllocator.bind_for_run(allocator)
        citation_resolver = CitationResolver(
            run=running,
            allocator=allocator,
            producer=self.event_producer,
            source=StreamEventSource.MODEL,
        )
        resolver_token = CitationResolver.bind_for_run(citation_resolver)
        # Polish-removal Phase 1 + 2.B (docs/refactor/01-presentation-polish-removal.md):
        # bind the per-run tool display lookup AND the MCP descriptor
        # registry before the resumed graph starts emitting tool events.
        # The resumed run runs in a fresh async task from the original
        # handle(), so the bindings set by RuntimeRunHandler are no longer
        # in scope. MCP descriptors loaded post-pause re-register on this
        # fresh registry as the agent re-discovers servers.
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

    # PR 3.2.5 Phase 3 — paired with the ``SUBAGENT_PAUSED`` emit in
    # ``stream_events.append_activity_events``. ``approval`` is the
    # ``ApprovalRequestRecord`` we just resolved; if its ``metadata``
    # carries ``parent_task_id`` (set on creation when the interrupt
    # fired inside a subagent's subgraph), reuse it as the ``task_id``
    # of the resume signal so the FE reducer's existing ``applySubagent``
    # slot finds the row by task_id.
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
        """Build the allocator for a resumed run.

        PR 04 — replaces the prior message-walking + event-counting
        seeder. The resumed allocator is reconstructed from the
        persistent ``(conversation_ordinal ↔ tool_call_id)`` binding
        store; the in-memory counter resumes at ``max(persisted)`` so
        every fresh allocation post-pause is strictly greater than any
        ordinal already burned pre-pause. Tool calls that re-dispatch on
        resume (LangGraph reuses the same ``call_id``) collapse to the
        existing binding instead of allocating again.

        When the worker was constructed without a binding store
        (replay / eval), fall back to a memory-only allocator. This
        path doesn't carry ordinals across the pause but it never
        crashes the resume.
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
            # P4 Stage 2 — opt-in coalesce window for MODEL_DELTA batching.
            # Default 0 (disabled) so this ships dark.
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
        """Apply a draft-send approval decision: persist v+2 + audit + emit.

        Approve → ``status=sent`` + audit ``draft.send.completed``.
        Reject  → ``status=draft`` + audit ``draft.send.rejected``.

        The actual connector tool dispatch is owned by a dedicated send-
        effect outbox worker (out-of-scope for PR 1.3.5 phase 2 — see
        ``docs/new-design/pr-1.3.5-draft-completion.md`` §3.5). This
        handler is responsible only for the draft-state transition + the
        audit chain entry. Once the dispatch worker lands it will read
        ``status=send_pending_approval`` rows and post to the connector;
        the post-dispatch transition to ``status=sent`` (or
        ``send_failed``) will replace the inline transition we do here.
        """

        from agent_runtime.persistence.records import DraftStatus  # noqa: PLC0415

        if self._draft_store is None:
            return
        metadata = approval.metadata if hasattr(approval, "metadata") else {}
        draft_id = str(metadata.get("draft_id", ""))
        if not draft_id:
            return
        latest = await self._maybe_await(
            self._draft_store.latest(org_id=run.org_id, draft_id=draft_id)
        )
        if latest is None or latest.status is not DraftStatus.SEND_PENDING_APPROVAL:
            # State changed since the approval was posted (e.g. a
            # concurrent discard); skip the transition.
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
        persisted = await self._maybe_await(
            self._draft_store.insert_version(next_record)
        )
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
        # Mark the host run completed when the approval was the only
        # outstanding action. We do not fail the run on reject — rejection
        # is a normal outcome.
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
    async def _maybe_await(value: object) -> object:
        if asyncio.iscoroutine(value):
            return await value
        return value

    @staticmethod
    def _next_draft_version(
        *,
        previous: object,
        decided_by_user_id: str,
        status: object,
    ) -> object:
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
        await self._maybe_await(write_audit(event_type=action, record=metadata))
