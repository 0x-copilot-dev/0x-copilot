"""Queued run command handling."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
import asyncio

from agent_runtime.agent.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
    StreamEvent,
    StreamEventSource,
    StreamEventType,
)
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness, create_agent_runtime
from agent_runtime.execution.runtime import ainvoke_runtime, astream_runtime
from agent_runtime.observability.constants import Keys as StreamKeys
from agent_runtime.observability.constants import Values as StreamValues
from agent_runtime.settings import RuntimeSettings
from runtime_api.schemas import (
    AgentRunStatus,
    MessageRecord,
    MessageRole,
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeRunCommand,
)
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory

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
        self.dependencies_factory = dependencies_factory or DefaultRuntimeDependenciesFactory(
            self.settings
        )
        self.agent_factory = agent_factory
        self.runtime_invoker = runtime_invoker
        self.runtime_streamer = runtime_streamer
        self.event_producer = RuntimeEventProducer(
            persistence=self.persistence,
            event_store=self.event_store,
        )
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

        self.persistence.update_run_status(run_id=command.run_id, status=AgentRunStatus.RUNNING)
        self._append_lifecycle(command, RuntimeApiEventType.RUN_STARTED, "Run started")

        try:
            harness = self.agent_factory(
                context=command.runtime_context,
                dependencies=self.dependencies_factory(command.runtime_context),
            )
            messages = self._messages_for_run(command)
            if command.runtime_context.model_profile.supports_streaming and (
                self._runtime_streamer_explicit or callable(getattr(harness.agent, "astream", None))
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
                        trace_id=command.trace_id,
                    )
                )
                self._append_lifecycle(
                    command,
                    RuntimeApiEventType.FINAL_RESPONSE,
                    final_text,
                    payload={"message": final_text},
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

    async def _stream_runtime(
        self,
        command: RuntimeRunCommand,
        run: RunRecord,
        harness: RuntimeHarness,
        messages: Sequence[object],
    ) -> object:
        final_result: object | None = None
        deltas: list[str] = []
        async with asyncio.timeout(command.runtime_context.model_profile.timeout_seconds):
            async for chunk in self.runtime_streamer(harness, messages):
                candidate = self._stream_result_candidate(chunk)
                if candidate is not None:
                    final_result = candidate
                delta = self._stream_delta(chunk)
                self._append_non_model_events(command, run, harness, chunk, delta=delta)
                if delta is None:
                    continue
                deltas.append(delta)
                self._append_lifecycle(
                    command,
                    RuntimeApiEventType.MODEL_DELTA,
                    delta,
                    source=StreamEventSource.MODEL,
                    payload={"delta": delta, "message": delta},
                )
        if final_result is not None:
            return final_result
        if deltas:
            return {"content": "".join(deltas)}
        return None

    def _append_non_model_events(
        self,
        command: RuntimeRunCommand,
        run: RunRecord,
        harness: RuntimeHarness,
        chunk: object,
        *,
        delta: str | None,
    ) -> None:
        for payload in self._mcp_auth_payloads(chunk):
            self._append_lifecycle(
                command,
                RuntimeApiEventType.MCP_AUTH_REQUIRED,
                "MCP authentication required",
                source=StreamEventSource.MCP,
                payload=payload,
            )
        raw_event = self._raw_stream_event(chunk)
        if raw_event is None or self._is_mcp_auth_event(raw_event):
            return
        normalized = harness.dependencies.stream_normalizer.normalize(raw_event, command.runtime_context)
        stream_events = tuple(
            event
            for event in normalized
            if isinstance(event, StreamEvent)
            and self._should_append_stream_event(event, raw_event=raw_event, delta=delta)
        )
        if stream_events:
            self.event_producer.append_stream_events(run=run, stream_events=stream_events)

    @classmethod
    def _raw_stream_event(cls, chunk: object) -> dict[str, object] | None:
        if isinstance(chunk, tuple) and len(chunk) == 2 and isinstance(chunk[0], str):
            if chunk[0] == StreamValues.StreamMode.VALUES and not cls._chunk_has_explicit_stream_event(
                chunk[1]
            ):
                return None
            return {StreamKeys.Raw.MODE: chunk[0], StreamKeys.Raw.CHUNK: chunk[1]}
        if not isinstance(chunk, Mapping):
            return None
        raw_event = dict(chunk)
        if StreamKeys.Raw.MODE in raw_event:
            return raw_event
        if cls._has_explicit_stream_event(raw_event):
            raw_event[StreamKeys.Raw.MODE] = StreamValues.StreamMode.CUSTOM
            return raw_event
        if StreamKeys.Raw.NS in raw_event or StreamKeys.Raw.NAMESPACE in raw_event:
            raw_event[StreamKeys.Raw.MODE] = StreamValues.StreamMode.CUSTOM
            return raw_event
        return None

    @classmethod
    def _has_explicit_stream_event(cls, raw_event: Mapping[str, object]) -> bool:
        return any(
            isinstance(raw_event.get(key), str)
            for key in (
                StreamKeys.Field.API_EVENT_TYPE,
                StreamKeys.Raw.EVENT,
                StreamKeys.Raw.EVENT_TYPE,
            )
        )

    @classmethod
    def _chunk_has_explicit_stream_event(cls, chunk: object) -> bool:
        if isinstance(chunk, Mapping):
            if cls._has_explicit_stream_event(chunk):
                return True
            return any(cls._chunk_has_explicit_stream_event(value) for value in chunk.values())
        if isinstance(chunk, Sequence) and not isinstance(chunk, (str, bytes, bytearray)):
            return any(cls._chunk_has_explicit_stream_event(value) for value in chunk)
        return False

    @classmethod
    def _is_mcp_auth_event(cls, raw_event: Mapping[str, object]) -> bool:
        event_type = (
            raw_event.get(StreamKeys.Field.API_EVENT_TYPE)
            or raw_event.get(StreamKeys.Raw.EVENT_TYPE)
            or raw_event.get(StreamKeys.Raw.EVENT)
        )
        return event_type == RuntimeApiEventType.MCP_AUTH_REQUIRED.value

    @classmethod
    def _should_append_stream_event(
        cls,
        event: StreamEvent,
        *,
        raw_event: Mapping[str, object],
        delta: str | None,
    ) -> bool:
        mode = raw_event.get(StreamKeys.Raw.MODE)
        has_api_event_type = isinstance(event.payload.get(StreamKeys.Field.API_EVENT_TYPE), str)
        if (
            delta is not None
            and mode == StreamValues.StreamMode.MESSAGES
            and event.source is StreamEventSource.MAIN_AGENT
            and event.event_type in {StreamEventType.PROGRESS, StreamEventType.CUSTOM}
            and not has_api_event_type
        ):
            return False
        if (
            mode == StreamValues.StreamMode.VALUES
            and event.event_type is StreamEventType.PROGRESS
            and not has_api_event_type
        ):
            return False
        return True

    @classmethod
    def _mcp_auth_payloads(cls, value: object) -> tuple[dict[str, object], ...]:
        payloads: list[dict[str, object]] = []
        cls._collect_mcp_auth_payloads(value, payloads)
        return tuple(payloads)

    @classmethod
    def _collect_mcp_auth_payloads(cls, value: object, payloads: list[dict[str, object]]) -> None:
        if isinstance(value, dict):
            event_type = value.get("api_event_type") or value.get("event_type")
            if event_type == RuntimeApiEventType.MCP_AUTH_REQUIRED.value:
                payloads.append(
                    {
                        key: item
                        for key, item in value.items()
                        if key
                        in {
                            "server_id",
                            "server_name",
                            "display_name",
                            "auth_url",
                            "expires_at",
                            "message",
                            "api_event_type",
                        }
                    }
                )
                return
            for item in value.values():
                cls._collect_mcp_auth_payloads(item, payloads)
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                cls._collect_mcp_auth_payloads(item, payloads)

    def _append_lifecycle(
        self,
        command: RuntimeRunCommand,
        event_type: RuntimeApiEventType,
        summary: str,
        *,
        source: StreamEventSource = StreamEventSource.SYSTEM,
        payload: dict[str, object] | None = None,
    ) -> None:
        self.event_store.append_event(
            RuntimeEventDraft(
                run_id=command.run_id,
                conversation_id=command.conversation_id,
                source=source,
                event_type=event_type,
                trace_id=command.trace_id,
                summary=summary,
                status="completed" if event_type == RuntimeApiEventType.FINAL_RESPONSE else None,
                payload=payload or {"status": event_type.value},
            )
        )

    @classmethod
    def _stream_delta(cls, chunk: object) -> str | None:
        if isinstance(chunk, tuple) and len(chunk) == 2 and chunk[0] == "messages":
            message = cls._message_from_stream_payload(chunk[1])
            return cls._message_delta(message)
        if isinstance(chunk, dict) and chunk.get("event") in {"on_chat_model_stream", "on_llm_stream"}:
            data = chunk.get("data")
            if isinstance(data, dict):
                return cls._message_delta(data.get("chunk"))
        return None

    @classmethod
    def _stream_result_candidate(cls, chunk: object) -> object | None:
        if isinstance(chunk, tuple) and len(chunk) == 2 and chunk[0] == "values":
            return chunk[1]
        if isinstance(chunk, dict) and chunk.get("event") in {"on_chain_end", "on_chat_model_end"}:
            data = chunk.get("data")
            if isinstance(data, dict):
                output = data.get("output")
                if output is not None:
                    return output
        return None

    @classmethod
    def _message_from_stream_payload(cls, payload: object) -> object:
        if isinstance(payload, tuple) and payload:
            return payload[0]
        if isinstance(payload, dict):
            return payload.get("chunk") or payload.get("message") or payload
        return payload

    @classmethod
    def _message_delta(cls, message: object) -> str | None:
        if isinstance(message, dict):
            return cls._content_delta_to_text(message.get("content"))
        return cls._content_delta_to_text(getattr(message, "content", None))

    @classmethod
    def _content_delta_to_text(cls, value: object) -> str | None:
        if isinstance(value, str):
            return value or None
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            text = "".join(parts)
            return text or None
        return None

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
        if isinstance(message, dict):
            return cls._content_to_text(message.get("content"))
        return cls._content_to_text(getattr(message, "content", None))

    @classmethod
    def _content_to_text(cls, value: object) -> str | None:
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
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
