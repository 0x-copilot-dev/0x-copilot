"""Queued approval-resolution command handling."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from datetime import datetime, timezone

from agent_runtime.api.constants import Values as ApiValues
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
    StreamEventSource,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness, create_agent_runtime
from agent_runtime.execution.runtime import astream_runtime_resume
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
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.run_metrics import AssistantRunMetrics
from runtime_worker.stream_events import StreamOrchestrator
from runtime_worker.stream_messages import StreamTextHelper
from runtime_worker.streaming_executor import StreamingExecutor

RuntimeDependenciesFactory = Callable[[AgentRuntimeContext], RuntimeDependencies]
AgentFactory = Callable[..., RuntimeHarness]
RuntimeResumer = Callable[[RuntimeHarness, object], AsyncIterator[object]]


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

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        dependencies_factory: RuntimeDependenciesFactory | None = None,
        settings: RuntimeSettings | None = None,
        agent_factory: AgentFactory = create_agent_runtime,
        runtime_resumer: RuntimeResumer = astream_runtime_resume,
        on_event_appended: Callable[[str], None] | None = None,
    ) -> None:
        self.persistence = persistence
        self.event_store = event_store
        self.settings = settings or RuntimeSettings.load()
        self.dependencies_factory = (
            dependencies_factory or DefaultRuntimeDependenciesFactory(self.settings)
        )
        self.agent_factory = agent_factory
        self.runtime_resumer = runtime_resumer
        self.event_producer = RuntimeEventProducer(
            persistence=persistence,
            event_store=event_store,
            on_event_appended=on_event_appended,
        )
        self.stream_event_mapper = StreamOrchestrator(self.event_producer)

    async def handle(self, command: RuntimeApprovalResolvedCommand) -> None:
        run = self.persistence.get_run(org_id=command.org_id, run_id=command.run_id)
        if run is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Approval command references an unknown run.",
                retryable=False,
            )
        approval = self.persistence.get_approval_request(
            org_id=command.org_id,
            approval_id=command.approval_id,
        )
        if approval is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Approval command references an unknown approval.",
                retryable=False,
            )
        metadata = approval.metadata
        approval_kind = StreamTextHelper.extract(
            metadata.get(self._Fields.APPROVAL_KIND)
        )
        if (
            metadata.get(self._Fields.NATIVE_INTERRUPT_ID) is None
            and approval_kind != ApiValues.ApprovalKind.MCP_AUTH
        ):
            return

        if (
            approval_kind == ApiValues.ApprovalKind.ASK_A_QUESTION
            and command.answer is not None
        ):
            self._append_user_answer_message(run=run, command=command)

        resume = self._resume_payload(command, metadata)
        running = self.persistence.update_run_status(
            run_id=run.run_id,
            status=AgentRunStatus.RUNNING,
        )
        try:
            harness = self.agent_factory(
                context=running.runtime_context,
                dependencies=self.dependencies_factory(running.runtime_context),
            )
            metrics = AssistantRunMetrics.from_run(running)
            result = await self._stream_resume(
                run=running,
                harness=harness,
                resume=resume,
                metrics=metrics,
            )
            if RuntimeRunHandler._is_action_interrupt(result):
                self.persistence.update_run_status(
                    run_id=run.run_id,
                    status=AgentRunStatus.WAITING_FOR_APPROVAL,
                )
                return
            final_text = RuntimeRunHandler._extract_final_text(result)
            self._complete_run_with_result(running, final_text, metrics)
        except Exception:
            failed = self.persistence.update_run_status(
                run_id=run.run_id,
                status=AgentRunStatus.FAILED,
            )
            self.event_producer.append_api_event(
                run=failed,
                source=StreamEventSource.SYSTEM,
                event_type=RuntimeApiEventType.RUN_FAILED,
                payload={self._Fields.STATUS: RuntimeApiEventType.RUN_FAILED.value},
                summary="Run failed",
            )
            raise

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
        )
        return StreamingExecutor.compose_final(result)

    def _complete_run_with_result(
        self,
        run: RunRecord,
        final_text: str | None,
        metrics: AssistantRunMetrics,
    ) -> None:
        metrics_payload = metrics.to_payload(completed_at=datetime.now(timezone.utc))
        if final_text is not None:
            usage = metrics_payload.get("usage")
            output_tokens = usage.get("output") if isinstance(usage, dict) else None
            self.persistence.append_message(
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
            self.event_producer.append_api_event(
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
        completed = self.persistence.update_run_status(
            run_id=run.run_id,
            status=AgentRunStatus.COMPLETED,
        )
        self.event_producer.append_api_event(
            run=completed,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.RUN_COMPLETED,
            payload=AssistantRunMetrics.with_payload(
                {self._Fields.STATUS: RuntimeApiEventType.RUN_COMPLETED.value},
                metrics_payload,
            ),
            metadata=AssistantRunMetrics.metadata(metrics_payload),
            summary="Run completed",
        )

    def _append_user_answer_message(
        self,
        *,
        run: RunRecord,
        command: RuntimeApprovalResolvedCommand,
    ) -> None:
        answer = command.answer
        if answer is None or not answer.strip():
            return
        self.persistence.append_message(
            MessageRecord(
                conversation_id=run.conversation_id,
                org_id=run.org_id,
                run_id=run.run_id,
                role=MessageRole.USER,
                content_text=answer,
                parent_message_id=run.user_message_id,
                trace_id=run.trace_id,
                metadata={
                    self._Fields.APPROVAL_ID: command.approval_id,
                    self._Fields.APPROVAL_KIND: ApiValues.ApprovalKind.ASK_A_QUESTION,
                },
            )
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
