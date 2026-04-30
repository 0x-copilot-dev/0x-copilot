"""Map runtime stream chunks into persisted runtime API events."""

from __future__ import annotations

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import RunRecord, RuntimeApiEventType
from runtime_worker.stream_parts import StreamNamespace, StreamPartParser
from runtime_worker.stream_subagents import SubagentEventProjector
from runtime_worker.stream_tools import ToolCallStreamState


class RuntimeStreamPartAdapter(SubagentEventProjector, StreamPartParser):
    """Project LangGraph v2 StreamPart chunks into stable runtime API events."""

    def __init__(self, event_producer: RuntimeEventProducer) -> None:
        self.event_producer = event_producer
        self._tool_call_states: dict[
            tuple[str, tuple[str, ...], str], ToolCallStreamState
        ] = {}
        self._tool_call_ids: dict[tuple[str, str], ToolCallStreamState] = {}
        self._subagent_lifecycle_keys: set[tuple[str, RuntimeApiEventType, str]] = set()

    def append_activity_events(
        self,
        *,
        run: RunRecord,
        chunk: object,
        delta: str | None,
    ) -> None:
        part = self.stream_part(chunk)
        if part is None:
            return

        stream_type = self.stream_type(part)
        namespace = self.namespace_for(part)
        data = part["data"]
        metadata = namespace.metadata(stream_type)
        parent_task_id = namespace.subagent_task_id

        for payload in self.explicit_api_payloads(data):
            event_type = self.api_event_type(payload)
            if event_type is None:
                continue
            self.event_producer.append_api_event(
                run=run,
                source=self.source_for_event(event_type, namespace),
                event_type=event_type,
                payload=payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )

        if stream_type == "messages":
            message = self.message_from_stream_payload(data)
            self.append_message_activity_events(
                run=run,
                namespace=namespace,
                message=message,
                delta=delta,
            )
            return

        if stream_type not in {"updates", "custom"} or self.contains_explicit_api_event(
            data
        ):
            return

        if stream_type == "updates" and self.append_subagent_lifecycle_events(
            run=run,
            namespace=namespace,
            data=data,
            metadata=metadata,
        ):
            return

        payload = self.safe_activity_payload(data)
        if not payload:
            return
        self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SUBAGENT
            if namespace.is_subagent
            else StreamEventSource.MAIN_AGENT,
            event_type=RuntimeApiEventType.SUBAGENT_PROGRESS
            if namespace.is_subagent
            else RuntimeApiEventType.PROGRESS,
            payload=payload,
            metadata=metadata,
            parent_task_id=parent_task_id,
        )

    def append_message_activity_events(
        self,
        *,
        run: RunRecord,
        namespace: StreamNamespace,
        message: object,
        delta: str | None,
    ) -> None:
        metadata = namespace.metadata("messages")
        parent_task_id = namespace.subagent_task_id

        for tool_call in self.tool_call_chunks(message):
            self.append_tool_call_chunk_event(
                run=run,
                namespace=namespace,
                tool_call=tool_call,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )

        if self.is_tool_result_message(message):
            payload = self.tool_result_payload(message)
            payload = self.tool_result_payload_with_state(run.run_id, payload)
            if payload["tool_name"] == "task":
                state = self.tool_call_state_for_payload(run.run_id, payload)
                self.append_task_lifecycle_event(
                    run=run,
                    event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
                    payload=self.task_tool_result_payload(
                        payload,
                        subagent_name=state.subagent_name
                        if state is not None
                        else None,
                    ),
                    metadata=metadata,
                )
                return
            self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_RESULT,
                payload=payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )
            completed_payload = {
                "tool_name": payload["tool_name"],
                "call_id": payload["call_id"],
                "status": "completed",
            }
            self.apply_tool_visibility(completed_payload)
            self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_CALL_COMPLETED,
                payload=completed_payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
            )

    @classmethod
    def stream_delta(cls, chunk: object) -> str | None:
        part = cls.stream_part(chunk)
        if part is None or cls.stream_type(part) != "messages":
            return None
        if cls.namespace_for(part).is_subagent:
            return None
        message = cls.message_from_stream_payload(part["data"])
        if cls.tool_call_chunks(message) or cls.is_tool_result_message(message):
            return None
        return cls.message_delta(message)

    @classmethod
    def stream_result_candidate(cls, chunk: object) -> object | None:
        part = cls.stream_part(chunk)
        if (
            part is not None
            and cls.stream_type(part) == "values"
            and not cls.namespace_for(part).is_subagent
        ):
            return part["data"]
        return None

    @classmethod
    def source_for_event(
        cls,
        event_type: RuntimeApiEventType,
        namespace: StreamNamespace,
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
        if (
            event_type
            in {
                RuntimeApiEventType.SUBAGENT_UPDATE,
                RuntimeApiEventType.SUBAGENT_STARTED,
                RuntimeApiEventType.SUBAGENT_PROGRESS,
                RuntimeApiEventType.SUBAGENT_COMPLETED,
            }
            or namespace.is_subagent
        ):
            return StreamEventSource.SUBAGENT
        return StreamEventSource.MAIN_AGENT
