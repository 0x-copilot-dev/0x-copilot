"""Queued run command handling."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
import asyncio

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
    StreamEventSource,
)
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness, create_agent_runtime
from agent_runtime.execution.runtime import ainvoke_runtime, astream_runtime
from agent_runtime.settings import RuntimeSettings
from runtime_api.schemas import (
    AgentRunStatus,
    MessageRecord,
    MessageRole,
    RunRecord,
    RuntimeApiEventType,
    RuntimeRunCommand,
)
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.stream_events import RuntimeStreamPartAdapter

RuntimeDependenciesFactory = Callable[[AgentRuntimeContext], RuntimeDependencies]
AgentFactory = Callable[..., RuntimeHarness]
RuntimeInvoker = Callable[[RuntimeHarness, Sequence[object]], object]
RuntimeStreamer = Callable[[RuntimeHarness, Sequence[object]], AsyncIterator[object]]


class RuntimeRunHandler:
    """Execute a queued runtime run command asynchronously."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        dependencies_factory: RuntimeDependenciesFactory | None = None,
        settings: RuntimeSettings | None = None,
        agent_factory: AgentFactory = create_agent_runtime,
        runtime_invoker: RuntimeInvoker = ainvoke_runtime,
        runtime_streamer: RuntimeStreamer = astream_runtime,
    ) -> None:
        self.persistence = persistence
        self.event_store = event_store
        self.settings = settings or RuntimeSettings.load()
        self.dependencies_factory = (
            dependencies_factory or DefaultRuntimeDependenciesFactory(self.settings)
        )
        self.agent_factory = agent_factory
        self.runtime_invoker = runtime_invoker
        self.runtime_streamer = runtime_streamer
        self.event_producer = RuntimeEventProducer(
            persistence=self.persistence,
            event_store=self.event_store,
        )
        self.stream_event_mapper = RuntimeStreamPartAdapter(self.event_producer)
        self._runtime_streamer_explicit = runtime_streamer is not astream_runtime

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

        run = self.persistence.update_run_status(
            run_id=command.run_id, status=AgentRunStatus.RUNNING
        )
        self._append_lifecycle(run, RuntimeApiEventType.RUN_STARTED, "Run started")

        try:
            harness = self.agent_factory(
                context=command.runtime_context,
                dependencies=self.dependencies_factory(command.runtime_context),
            )
            messages = self._messages_for_run(command, run)
            if command.runtime_context.model_profile.supports_streaming and (
                self._runtime_streamer_explicit
                or callable(getattr(harness.agent, "astream", None))
            ):
                result = await self._stream_runtime(command, run, harness, messages)
            else:
                result = await asyncio.wait_for(
                    self.runtime_invoker(
                        harness,
                        messages,
                    ),
                    timeout=command.runtime_context.model_profile.timeout_seconds,
                )
            final_text = self._extract_final_text(result)
            if final_text is not None:
                self.persistence.append_message(
                    MessageRecord(
                        conversation_id=command.conversation_id,
                        org_id=command.org_id,
                        run_id=command.run_id,
                        role=MessageRole.ASSISTANT,
                        content_text=final_text,
                        parent_message_id=run.user_message_id,
                        branch_id=self._trace_text(
                            command.runtime_context, "branch_id"
                        ),
                        trace_id=command.trace_id,
                    )
                )
                self._append_lifecycle(
                    run,
                    RuntimeApiEventType.FINAL_RESPONSE,
                    final_text,
                    payload={"message": final_text},
                )
        except TimeoutError as exc:
            failed = self.persistence.update_run_status(
                run_id=command.run_id, status=AgentRunStatus.TIMED_OUT
            )
            self._append_lifecycle(
                failed, RuntimeApiEventType.RUN_FAILED, "Run timed out"
            )
            raise AgentRuntimeError(
                RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
                "Runtime invocation timed out.",
                retryable=True,
                correlation_id=command.trace_id,
            ) from exc
        except Exception:
            failed = self.persistence.update_run_status(
                run_id=command.run_id, status=AgentRunStatus.FAILED
            )
            self._append_lifecycle(failed, RuntimeApiEventType.RUN_FAILED, "Run failed")
            raise

        completed = self.persistence.update_run_status(
            run_id=command.run_id, status=AgentRunStatus.COMPLETED
        )
        self._append_lifecycle(
            completed, RuntimeApiEventType.RUN_COMPLETED, "Run completed"
        )

    def _messages_for_run(
        self, command: RuntimeRunCommand, run: RunRecord
    ) -> tuple[dict[str, str], ...]:
        records = self.persistence.list_messages(
            org_id=command.org_id,
            conversation_id=command.conversation_id,
            limit=200,
        )
        selected = self._selected_message_chain(records, run.user_message_id)
        return tuple(
            {"role": message.role.value, "content": message.content_text}
            for message in selected
            if message.role
            in {MessageRole.USER, MessageRole.ASSISTANT, MessageRole.SYSTEM}
        )

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
    ) -> object:
        final_result: object | None = None
        response_deltas: list[str] = []
        subagent_summaries: list[str] = []
        active_subagent_tasks: set[str] = set()
        completed_subagent_tasks: set[str] = set()
        saw_task_subagent = False
        async with asyncio.timeout(
            command.runtime_context.model_profile.timeout_seconds
        ):
            async for chunk in self.runtime_streamer(harness, messages):
                latest_before = self.event_store.get_latest_sequence(run_id=run.run_id)
                candidate = self.stream_event_mapper.stream_result_candidate(chunk)
                if candidate is not None and not active_subagent_tasks:
                    final_result = candidate
                delta = self.stream_event_mapper.stream_delta(chunk)
                self.stream_event_mapper.append_activity_events(
                    run=run, chunk=chunk, delta=delta
                )
                new_events = self.event_store.list_events_after(
                    org_id=command.org_id,
                    run_id=run.run_id,
                    after_sequence=latest_before,
                )
                for event in new_events:
                    if (
                        event.event_type == RuntimeApiEventType.SUBAGENT_STARTED
                        and event.task_id is not None
                    ):
                        active_subagent_tasks.add(event.task_id)
                        saw_task_subagent = True
                    if (
                        event.event_type == RuntimeApiEventType.SUBAGENT_COMPLETED
                        and event.task_id is not None
                    ):
                        active_subagent_tasks.discard(event.task_id)
                        if event.task_id not in completed_subagent_tasks:
                            completed_subagent_tasks.add(event.task_id)
                            if event.summary:
                                subagent_summaries.append(event.summary)
                if delta is None:
                    continue
                if not active_subagent_tasks:
                    response_deltas.append(delta)
                self._append_lifecycle(
                    run,
                    RuntimeApiEventType.MODEL_DELTA,
                    delta,
                    source=StreamEventSource.MODEL,
                    payload={"delta": delta, "message": delta},
                )
        if final_result is not None:
            return final_result
        if response_deltas:
            return {"content": "".join(response_deltas)}
        if saw_task_subagent and subagent_summaries:
            return {"content": "\n\n".join(subagent_summaries)}
        return None

    def _append_lifecycle(
        self,
        run: RunRecord,
        event_type: RuntimeApiEventType,
        summary: str,
        *,
        source: StreamEventSource = StreamEventSource.SYSTEM,
        payload: dict[str, object] | None = None,
    ) -> None:
        self.event_producer.append_api_event(
            run=run,
            source=source,
            event_type=event_type,
            summary=summary,
            status="completed"
            if event_type == RuntimeApiEventType.FINAL_RESPONSE
            else None,
            payload=payload or {"status": event_type.value},
        )

    @classmethod
    def _extract_final_text(cls, result: object) -> str | None:
        """Extract a best-effort assistant response from common LangChain result shapes."""

        if result is None:
            return None
        if isinstance(result, str):
            return result.strip() or None
        if isinstance(result, dict):
            for key in ("final_response", "response", "output", "content"):
                text = cls._text(result.get(key))
                if text is not None:
                    return text
            messages = result.get("messages")
            if isinstance(messages, Sequence):
                for message in reversed(messages):
                    text = cls._message_content(message)
                    if text is not None:
                        return text
        return cls._message_content(result)

    @classmethod
    def _message_content(cls, message: object) -> str | None:
        if isinstance(message, Mapping):
            return cls._content_to_text(message.get("content"))
        return cls._content_to_text(getattr(message, "content", None))

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
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            text = "".join(parts).strip()
            return text or None
        return None

    @classmethod
    def _text(cls, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        return value.strip() or None

    @classmethod
    def _trace_text(cls, context: AgentRuntimeContext, key: str) -> str | None:
        value = context.trace_metadata.get(key)
        return value if isinstance(value, str) and value.strip() else None
