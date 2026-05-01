"""Queued approval-resolution command handling."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime

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
from runtime_worker.stream_events import RuntimeStreamPartAdapter

RuntimeDependenciesFactory = Callable[[AgentRuntimeContext], RuntimeDependencies]
AgentFactory = Callable[..., RuntimeHarness]
RuntimeResumer = Callable[[RuntimeHarness, object], AsyncIterator[object]]


class RuntimeApprovalHandler:
    """Consume durable approval-resolution commands after the API records the decision."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        dependencies_factory: RuntimeDependenciesFactory | None = None,
        settings: RuntimeSettings | None = None,
        agent_factory: AgentFactory = create_agent_runtime,
        runtime_resumer: RuntimeResumer = astream_runtime_resume,
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
        )
        self.stream_event_mapper = RuntimeStreamPartAdapter(self.event_producer)

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
        if metadata.get("native_interrupt_id") is None:
            return

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
                payload={"status": RuntimeApiEventType.RUN_FAILED.value},
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
        final_result: object | None = None
        last_result: object | None = None
        response_deltas: list[str] = []
        async for chunk in self.runtime_resumer(harness, resume):
            last_result = chunk
            metrics.record_usage_from(chunk)
            latest_before = self.event_store.get_latest_sequence(run_id=run.run_id)
            candidate = self.stream_event_mapper.stream_result_candidate(chunk)
            if candidate is not None:
                final_result = candidate
                metrics.record_usage_from(candidate)
            delta = self.stream_event_mapper.stream_delta(chunk)
            self.stream_event_mapper.append_activity_events(
                run=run,
                chunk=chunk,
                delta=delta,
            )
            new_events = self.event_store.list_events_after(
                org_id=run.org_id,
                run_id=run.run_id,
                after_sequence=latest_before,
            )
            for event in new_events:
                if event.event_type in RuntimeRunHandler.action_interrupt_events:
                    return {"action_required": True}
            if delta is None:
                continue
            response_deltas.append(delta)
            metrics.record_model_delta(delta)
            self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.MODEL,
                event_type=RuntimeApiEventType.MODEL_DELTA,
                payload={"delta": delta, "message": delta},
                summary=delta,
            )
        if final_result is not None:
            return final_result
        if response_deltas:
            return {"content": "".join(response_deltas)}
        return last_result

    def _complete_run_with_result(
        self,
        run: RunRecord,
        final_text: str | None,
        metrics: AssistantRunMetrics,
    ) -> None:
        metrics_payload = metrics.to_payload(completed_at=datetime.now(UTC))
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
                    {"message": final_text},
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
                {"status": RuntimeApiEventType.RUN_COMPLETED.value},
                metrics_payload,
            ),
            metadata=AssistantRunMetrics.metadata(metrics_payload),
            summary="Run completed",
        )

    @classmethod
    def _resume_payload(
        cls,
        command: RuntimeApprovalResolvedCommand,
        metadata: Mapping[str, object],
    ) -> dict[str, object]:
        if cls._text(metadata.get("approval_kind")) == "mcp_auth":
            return {
                "approval_id": command.approval_id,
                "decision": "approved"
                if command.decision is ApprovalDecision.APPROVED
                else "rejected",
            }
        return {
            "decisions": [
                {
                    "type": "approve"
                    if command.decision is ApprovalDecision.APPROVED
                    else "reject",
                }
            ]
        }

    @staticmethod
    def _text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text or None
