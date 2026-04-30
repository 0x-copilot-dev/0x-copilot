"""Queued run command handling."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
import asyncio

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    JsonObject,
    RuntimeDependencies,
    RuntimeErrorCode,
    StreamEventSource,
)
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness, create_agent_runtime
from agent_runtime.execution.runtime import ainvoke_runtime, astream_runtime
from agent_runtime.observability.tracing import TraceContext
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
                self._append_stream_activity_events(run, chunk, delta=delta)
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
        if deltas:
            return {"content": "".join(deltas)}
        if final_result is not None:
            return final_result
        return None

    def _append_stream_activity_events(
        self,
        run: RunRecord,
        chunk: object,
        *,
        delta: str | None,
    ) -> None:
        part = self._stream_part(chunk)
        if part is None:
            return

        stream_type = self._stream_type(part)
        namespace = self._namespace_for(part)
        data = part["data"]
        metadata = self._stream_metadata(stream_type, namespace)
        parent_task_id = self._task_id_from_namespace(namespace)

        for payload in self._explicit_api_payloads(data):
            event_type = self._api_event_type(payload)
            if event_type is None:
                continue
            self.event_producer.append_api_event(
                run=run,
                source=self._source_for_event(event_type, namespace),
                event_type=event_type,
                payload=self._payload_for_api_event(event_type, payload),
                metadata=metadata,
                parent_task_id=parent_task_id,
            )

        if stream_type == "messages":
            message = self._message_from_stream_payload(data)
            self._append_message_activity_events(
                run=run,
                namespace=namespace,
                message=message,
                delta=delta,
            )
            return

        if stream_type not in {"updates", "custom"} or self._contains_explicit_api_event(data):
            return

        payload = self._payload_mapping(data)
        if not payload:
            return
        is_subagent = self._is_subagent_namespace(namespace)
        self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SUBAGENT if is_subagent else StreamEventSource.MAIN_AGENT,
            event_type=RuntimeApiEventType.SUBAGENT_PROGRESS if is_subagent else RuntimeApiEventType.PROGRESS,
            payload=payload,
            metadata=metadata,
            parent_task_id=parent_task_id,
        )

    def _append_message_activity_events(
        self,
        *,
        run: RunRecord,
        namespace: tuple[str, ...],
        message: object,
        delta: str | None,
    ) -> None:
        metadata = self._stream_metadata("messages", namespace)
        parent_task_id = self._task_id_from_namespace(namespace)

        for tool_call in self._tool_call_chunks(message):
            payload = self._tool_call_payload(tool_call)
            event_type = (
                RuntimeApiEventType.TOOL_CALL_STARTED
                if payload.get("tool_name") != "unknown_tool"
                else RuntimeApiEventType.TOOL_CALL_DELTA
            )
            self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.TOOL,
                event_type=event_type,
                payload=payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )

        if self._is_tool_result_message(message):
            payload = self._tool_result_payload(message)
            self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_RESULT,
                payload=payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )
            self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_CALL_COMPLETED,
                payload={
                    "tool_name": payload["tool_name"],
                    "call_id": payload["call_id"],
                    "status": "completed",
                },
                metadata=metadata,
                parent_task_id=parent_task_id,
            )
            return

        if delta is not None or self._tool_call_chunks(message) or self._is_internal_namespace(namespace):
            return

        payload = self._payload_mapping(message)
        if not payload:
            return
        is_subagent = self._is_subagent_namespace(namespace)
        self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SUBAGENT if is_subagent else StreamEventSource.MAIN_AGENT,
            event_type=RuntimeApiEventType.SUBAGENT_PROGRESS if is_subagent else RuntimeApiEventType.PROGRESS,
            payload=payload,
            metadata=metadata,
            parent_task_id=parent_task_id,
        )

    @classmethod
    def _stream_part(cls, chunk: object) -> dict[str, object] | None:
        if not isinstance(chunk, Mapping):
            return None
        stream_type = chunk.get("type")
        if not isinstance(stream_type, str) or "data" not in chunk:
            return None
        return dict(chunk)

    @classmethod
    def _stream_type(cls, part: Mapping[str, object]) -> str:
        return str(part["type"])

    @classmethod
    def _namespace_for(cls, part: Mapping[str, object]) -> tuple[str, ...]:
        value = part.get("ns", ())
        if isinstance(value, str):
            return (value,)
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            return tuple(str(item) for item in value)
        return ()

    @classmethod
    def _stream_metadata(cls, stream_type: str, namespace: tuple[str, ...]) -> JsonObject:
        metadata: JsonObject = {"stream_type": stream_type}
        if namespace:
            metadata["namespace"] = list(namespace)
        return metadata

    @classmethod
    def _explicit_api_payloads(cls, value: object) -> tuple[JsonObject, ...]:
        payloads: list[JsonObject] = []
        cls._collect_explicit_api_payloads(value, payloads)
        return tuple(payloads)

    @classmethod
    def _collect_explicit_api_payloads(cls, value: object, payloads: list[JsonObject]) -> None:
        if isinstance(value, Mapping):
            payload = cls._payload_mapping(value)
            if cls._api_event_type(payload) is not None:
                payloads.append(payload)
                return
            for item in value.values():
                cls._collect_explicit_api_payloads(item, payloads)
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                cls._collect_explicit_api_payloads(item, payloads)

    @classmethod
    def _contains_explicit_api_event(cls, value: object) -> bool:
        return bool(cls._explicit_api_payloads(value))

    @classmethod
    def _api_event_type(cls, payload: Mapping[str, object]) -> RuntimeApiEventType | None:
        value = payload.get("api_event_type") or payload.get("event_type") or payload.get("event")
        if not isinstance(value, str):
            return None
        try:
            return RuntimeApiEventType(value)
        except ValueError:
            return None

    @classmethod
    def _payload_for_api_event(
        cls,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> JsonObject:
        if event_type is RuntimeApiEventType.MCP_AUTH_REQUIRED:
            return {
                key: value
                for key, value in payload.items()
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
        if event_type in {
            RuntimeApiEventType.REASONING_SUMMARY,
            RuntimeApiEventType.REASONING_SUMMARY_DELTA,
        }:
            return {
                key: value
                for key, value in payload.items()
                if key in {"summary", "delta", "message", "status"}
            }
        return payload

    @classmethod
    def _source_for_event(
        cls,
        event_type: RuntimeApiEventType,
        namespace: tuple[str, ...],
    ) -> StreamEventSource:
        if event_type is RuntimeApiEventType.MCP_AUTH_REQUIRED:
            return StreamEventSource.MCP
        if event_type in {
            RuntimeApiEventType.TOOL_CALL,
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.TOOL_CALL_DELTA,
            RuntimeApiEventType.TOOL_RESULT,
            RuntimeApiEventType.TOOL_CALL_COMPLETED,
        }:
            return StreamEventSource.TOOL
        if event_type in {
            RuntimeApiEventType.SUBAGENT_UPDATE,
            RuntimeApiEventType.SUBAGENT_STARTED,
            RuntimeApiEventType.SUBAGENT_PROGRESS,
            RuntimeApiEventType.SUBAGENT_COMPLETED,
        } or cls._is_subagent_namespace(namespace):
            return StreamEventSource.SUBAGENT
        return StreamEventSource.MAIN_AGENT

    @classmethod
    def _is_subagent_namespace(cls, namespace: tuple[str, ...]) -> bool:
        return any(part.startswith("tools:") or "subagent" in part.lower() for part in namespace)

    @classmethod
    def _is_internal_namespace(cls, namespace: tuple[str, ...]) -> bool:
        return any("summar" in part.lower() for part in namespace)

    @classmethod
    def _task_id_from_namespace(cls, namespace: tuple[str, ...]) -> str | None:
        for part in namespace:
            if part.startswith("tools:"):
                return part.split(":", maxsplit=1)[1] or None
        return None

    @classmethod
    def _tool_call_chunks(cls, message: object) -> tuple[object, ...]:
        if isinstance(message, Mapping):
            value = message.get("tool_call_chunks") or message.get("tool_calls") or ()
        else:
            value = getattr(message, "tool_call_chunks", None) or getattr(message, "tool_calls", ())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return tuple(value)
        return ()

    @classmethod
    def _tool_call_payload(cls, tool_call: object) -> JsonObject:
        payload = cls._payload_mapping(tool_call)
        tool_name = cls._text(payload.get("name")) or cls._text(payload.get("tool_name")) or "unknown_tool"
        call_id = (
            cls._text(payload.get("id"))
            or cls._text(payload.get("call_id"))
            or TraceContext.event_id()
        )
        args = payload.get("args", {})
        return {
            "tool_name": tool_name,
            "call_id": call_id,
            "args": args if isinstance(args, Mapping) else {"delta": str(args)},
            "delta": str(args) if args and not isinstance(args, Mapping) else "",
            "status": payload.get("status", "running"),
        }

    @classmethod
    def _is_tool_result_message(cls, message: object) -> bool:
        if isinstance(message, Mapping):
            return message.get("type") in {"tool", "tool_result"}
        return bool(getattr(message, "tool_call_id", None)) or getattr(message, "type", None) == "tool"

    @classmethod
    def _tool_result_payload(cls, message: object) -> JsonObject:
        payload = cls._payload_mapping(message)
        tool_name = cls._text(payload.get("name")) or cls._text(payload.get("tool_name")) or "unknown_tool"
        call_id = (
            cls._text(payload.get("tool_call_id"))
            or cls._text(payload.get("id"))
            or cls._text(payload.get("call_id"))
            or TraceContext.event_id()
        )
        excluded = {"type", "name", "id", "tool_call_id", "call_id", "tool_name", "status"}
        output = {key: value for key, value in payload.items() if key not in excluded}
        return {
            "tool_name": tool_name,
            "call_id": call_id,
            "status": payload.get("status", "completed"),
            "output": output or payload,
        }

    @classmethod
    def _payload_mapping(cls, value: object) -> JsonObject:
        if isinstance(value, Mapping):
            return {str(key): cls._json_value(item) for key, item in value.items()}
        if value is None:
            return {}
        return {"content": cls._json_value(value)}

    @classmethod
    def _json_value(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return {str(key): cls._json_value(item) for key, item in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            values = [cls._json_value(item) for item in value]
            if all(isinstance(item, str | int | float | bool) or item is None for item in values):
                return values
            text = cls._text_from_content_blocks(values)
            return text if text is not None else str(values)
        if isinstance(value, str | int | float | bool) or value is None:
            return value
        return str(value)

    @classmethod
    def _text_from_content_blocks(cls, values: Sequence[object]) -> str | None:
        parts: list[str] = []
        for item in values:
            if isinstance(item, Mapping):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        text = "".join(parts).strip()
        return text or None

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
        part = cls._stream_part(chunk)
        if part is None or cls._stream_type(part) != "messages":
            return None
        message = cls._message_from_stream_payload(part["data"])
        if cls._tool_call_chunks(message) or cls._is_tool_result_message(message):
            return None
        return cls._message_delta(message)

    @classmethod
    def _stream_result_candidate(cls, chunk: object) -> object | None:
        part = cls._stream_part(chunk)
        if part is not None and cls._stream_type(part) == "values":
            return part["data"]
        return None

    @classmethod
    def _message_from_stream_payload(cls, payload: object) -> object:
        if isinstance(payload, tuple) and payload:
            return payload[0]
        if isinstance(payload, Mapping):
            return payload.get("message") or payload
        return payload

    @classmethod
    def _message_delta(cls, message: object) -> str | None:
        if isinstance(message, Mapping):
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
                elif isinstance(item, Mapping):
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
        if isinstance(message, Mapping):
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
