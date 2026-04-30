"""Queued run command handling."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import asyncio

from agent_runtime.agent.contracts import AgentRuntimeContext, RuntimeDependencies, RuntimeErrorCode, StreamEventSource
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness, create_agent_runtime
from agent_runtime.execution.runtime import ainvoke_runtime
from runtime_api.schemas import (
    AgentRunStatus,
    MessageRole,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeRunCommand,
)
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory

RuntimeDependenciesFactory = Callable[[AgentRuntimeContext], RuntimeDependencies]
AgentFactory = Callable[..., RuntimeHarness]
RuntimeInvoker = Callable[[RuntimeHarness, Sequence[object]], object]


class RuntimeRunHandler:
    """Execute a queued runtime run command asynchronously."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        dependencies_factory: RuntimeDependenciesFactory | None = None,
        agent_factory: AgentFactory = create_agent_runtime,
        runtime_invoker: RuntimeInvoker = ainvoke_runtime,
    ) -> None:
        self.persistence = persistence
        self.event_store = event_store
        self.dependencies_factory = dependencies_factory or DefaultRuntimeDependenciesFactory()
        self.agent_factory = agent_factory
        self.runtime_invoker = runtime_invoker

    async def handle(self, command: RuntimeRunCommand) -> None:
        """Run the agent and persist lifecycle events."""

        run = self.persistence.get_run(org_id=command.org_id, run_id=command.run_id)
        if run is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Run command references an unknown run.",
                retryable=False,
                correlation_id=command.trace_id,
            )

        self.persistence.update_run_status(run_id=command.run_id, status=AgentRunStatus.RUNNING)
        self._append_lifecycle(command, RuntimeApiEventType.RUN_STARTED, "Run started")

        try:
            harness = self.agent_factory(
                context=command.runtime_context,
                dependencies=self.dependencies_factory(command.runtime_context),
            )
            await asyncio.wait_for(
                self.runtime_invoker(
                    harness,
                    self._messages_for_run(command),
                ),
                timeout=command.runtime_context.model_profile.timeout_seconds,
            )
        except TimeoutError as exc:
            self.persistence.update_run_status(run_id=command.run_id, status=AgentRunStatus.TIMED_OUT)
            self._append_lifecycle(command, RuntimeApiEventType.RUN_FAILED, "Run timed out")
            raise AgentRuntimeError(
                RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
                "Runtime invocation timed out.",
                retryable=True,
                correlation_id=command.trace_id,
            ) from exc
        except Exception:
            self.persistence.update_run_status(run_id=command.run_id, status=AgentRunStatus.FAILED)
            self._append_lifecycle(command, RuntimeApiEventType.RUN_FAILED, "Run failed")
            raise

        self.persistence.update_run_status(run_id=command.run_id, status=AgentRunStatus.COMPLETED)
        self._append_lifecycle(command, RuntimeApiEventType.RUN_COMPLETED, "Run completed")
        latest_sequence = self.event_store.get_latest_sequence(run_id=command.run_id)
        self.persistence.set_run_latest_sequence(
            run_id=command.run_id,
            latest_sequence_no=latest_sequence,
        )

    def _messages_for_run(self, command: RuntimeRunCommand) -> tuple[dict[str, str], ...]:
        records = self.persistence.list_messages(
            org_id=command.org_id,
            conversation_id=command.conversation_id,
            limit=200,
        )
        return tuple(
            {"role": message.role.value, "content": message.content_text}
            for message in records
            if message.role in {MessageRole.USER, MessageRole.ASSISTANT, MessageRole.SYSTEM}
        )

    def _append_lifecycle(
        self,
        command: RuntimeRunCommand,
        event_type: RuntimeApiEventType,
        summary: str,
    ) -> None:
        self.event_store.append_event(
            RuntimeEventDraft(
                run_id=command.run_id,
                conversation_id=command.conversation_id,
                source=StreamEventSource.SYSTEM,
                event_type=event_type,
                trace_id=command.trace_id,
                summary=summary,
                payload={"status": event_type.value},
            )
        )
