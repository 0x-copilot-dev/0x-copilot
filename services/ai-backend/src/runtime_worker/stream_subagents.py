"""Subagent lifecycle projection helpers for runtime stream events."""

from __future__ import annotations

from collections.abc import Mapping

from agent_runtime.execution.contracts import JsonObject, StreamEventSource
from agent_runtime.observability.tracing import TraceContext
from runtime_api.schemas import RunRecord, RuntimeApiEventType
from runtime_worker.stream_parts import StreamNamespace
from runtime_worker.stream_tools import ToolCallStreamState, ToolEventProjector


class SubagentEventProjector(ToolEventProjector):
    """Project Deep Agents task-tool activity into subagent lifecycle events."""

    _subagent_lifecycle_keys: set[tuple[str, RuntimeApiEventType, str]]

    def append_task_tool_call_event(
        self,
        *,
        run: RunRecord,
        state: ToolCallStreamState,
        metadata: JsonObject,
    ) -> None:
        if state.started_emitted or state.call_id is None:
            return
        args = state.args or self.parse_args_text(state.args_text)
        if not args:
            return
        payload = self.task_tool_call_payload(
            call_id=state.call_id,
            args_payload=args,
        )
        state.subagent_name = self.text(payload.get("subagent_name"))
        self.append_task_lifecycle_event(
            run=run,
            event_type=RuntimeApiEventType.SUBAGENT_STARTED,
            payload=payload,
            metadata=metadata,
        )
        state.started_emitted = True

    def append_task_lifecycle_event(
        self,
        *,
        run: RunRecord,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> None:
        task_id = self.text(payload.get("task_id"))
        if task_id is not None:
            key = (run.run_id, event_type, task_id)
            if key in self._subagent_lifecycle_keys:
                return
            self._subagent_lifecycle_keys.add(key)
        self.event_producer.append_api_event(  # type: ignore[attr-defined]
            run=run,
            source=StreamEventSource.SUBAGENT,
            event_type=event_type,
            payload=payload,
            metadata=metadata,
        )

    def append_subagent_lifecycle_events(
        self,
        *,
        run: RunRecord,
        namespace: StreamNamespace,
        data: object,
        metadata: JsonObject,
    ) -> bool:
        """Append lifecycle events derived from documented Deep Agents update chunks."""

        emitted = False
        for payload in self.task_tool_call_payloads(data):
            self.append_task_lifecycle_event(
                run=run,
                event_type=RuntimeApiEventType.SUBAGENT_STARTED,
                payload=payload,
                metadata=metadata,
            )
            emitted = True
        for payload in self.task_tool_result_payloads(data):
            self.append_task_lifecycle_event(
                run=run,
                event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
                payload=payload,
                metadata=metadata,
            )
            emitted = True
        if emitted or not namespace.is_subagent:
            return emitted

        payload = self.safe_activity_payload(data)
        payload.setdefault("task_id", namespace.subagent_task_id)
        payload.setdefault("status", "running")
        self.event_producer.append_api_event(  # type: ignore[attr-defined]
            run=run,
            source=StreamEventSource.SUBAGENT,
            event_type=RuntimeApiEventType.SUBAGENT_PROGRESS,
            payload=payload,
            metadata=metadata,
            parent_task_id=namespace.subagent_task_id,
        )
        return True

    @classmethod
    def task_tool_call_payload(
        cls,
        *,
        call_id: str,
        args_payload: Mapping[str, object],
    ) -> JsonObject:
        subagent_name = (
            cls.text(args_payload.get("subagent_type"))
            or cls.text(args_payload.get("subagent_name"))
            or "subagent"
        )
        summary = cls.text(args_payload.get("description")) or cls.text(
            args_payload.get("task")
        )
        event_payload: JsonObject = {
            "task_id": call_id,
            "subagent_name": subagent_name,
            "status": "queued",
        }
        if summary is not None:
            event_payload["summary"] = summary
        return event_payload

    @classmethod
    def task_tool_result_payload(
        cls,
        payload: Mapping[str, object],
        *,
        subagent_name: str | None = None,
    ) -> JsonObject:
        call_id = cls.text(payload.get("call_id")) or TraceContext.event_id()
        output = payload.get("output")
        output_payload = output if isinstance(output, Mapping) else {}
        summary = (
            cls.content_delta_to_text(output_payload.get("content"))
            or cls.text(output_payload.get("message"))
            or cls.content_delta_to_text(payload.get("content"))
            or cls.text(payload.get("message"))
        )
        event_payload: JsonObject = {
            "task_id": call_id,
            "subagent_name": subagent_name or "subagent",
            "status": "completed",
        }
        if summary is not None:
            event_payload["summary"] = summary
        return event_payload

    @classmethod
    def task_tool_call_payloads(cls, value: object) -> tuple[JsonObject, ...]:
        payloads: list[JsonObject] = []
        for message in cls.update_messages(value):
            for tool_call in cls.tool_call_chunks(message):
                payload = cls.payload_mapping(tool_call)
                tool_name = cls.text(payload.get("name")) or cls.text(
                    payload.get("tool_name")
                )
                if tool_name != "task":
                    continue
                call_id = cls.text(payload.get("id")) or cls.text(
                    payload.get("call_id")
                )
                if call_id is None:
                    continue
                args = payload.get("args")
                args_payload = args if isinstance(args, Mapping) else {}
                payloads.append(
                    cls.task_tool_call_payload(
                        call_id=call_id,
                        args_payload=args_payload,
                    )
                )
        return tuple(payloads)

    @classmethod
    def task_tool_result_payloads(cls, value: object) -> tuple[JsonObject, ...]:
        payloads: list[JsonObject] = []
        for message in cls.update_messages(value):
            if not cls.is_tool_result_message(message):
                continue
            payload = cls.payload_mapping(message)
            tool_name = cls.text(payload.get("name")) or cls.text(
                payload.get("tool_name")
            )
            if tool_name != "task":
                continue
            call_id = (
                cls.text(payload.get("tool_call_id"))
                or cls.text(payload.get("id"))
                or cls.text(payload.get("call_id"))
            )
            if call_id is None:
                continue
            payloads.append(
                cls.task_tool_result_payload({"call_id": call_id, **payload})
            )
        return tuple(payloads)
