"""Tool call projection helpers for runtime stream events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from agent_runtime.api.constants import Keys, Values
from agent_runtime.execution.contracts import JsonObject, StreamEventSource
from agent_runtime.observability.tracing import TraceContext
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventVisibility,
)
from runtime_worker.stream_messages import StreamMessageParser
from runtime_worker.stream_parts import StreamNamespace


@dataclass
class ToolCallStreamState:
    """Incremental tool-call state for provider chunks that omit name/id fields."""

    namespace: StreamNamespace
    key: str
    tool_name: str | None = None
    call_id: str | None = None
    args_text: str = ""
    last_delta: str = ""
    args: JsonObject = field(default_factory=dict)
    summary: str | None = None
    subagent_name: str | None = None
    started_emitted: bool = False


class ToolEventProjector(StreamMessageParser):
    """Project provider tool-call chunks and tool-result messages."""

    internal_tool_names = frozenset({Values.Tool.WRITE_TODOS})

    _tool_call_states: dict[tuple[str, tuple[str, ...], str], ToolCallStreamState]
    _tool_call_ids: dict[tuple[str, str], ToolCallStreamState]

    def append_tool_call_chunk_event(
        self,
        *,
        run: RunRecord,
        namespace: StreamNamespace,
        tool_call: object,
        metadata: JsonObject,
        parent_task_id: str | None,
    ) -> None:
        state = self.tool_call_state(run.run_id, namespace, tool_call)
        if state.tool_name == "task":
            self.append_task_tool_call_event(  # type: ignore[attr-defined]
                run=run,
                state=state,
                metadata=metadata,
            )
            return
        if state.tool_name is None or state.call_id is None:
            return
        payload = self.tool_call_payload_from_state(state)
        event_type = (
            RuntimeApiEventType.TOOL_CALL_STARTED
            if not state.started_emitted
            else RuntimeApiEventType.TOOL_CALL_DELTA
        )
        if event_type is RuntimeApiEventType.TOOL_CALL_DELTA:
            payload["status"] = "running"
        self.event_producer.append_api_event(  # type: ignore[attr-defined]
            run=run,
            source=StreamEventSource.TOOL,
            event_type=event_type,
            payload=payload,
            metadata=metadata,
            parent_task_id=parent_task_id,
        )
        state.started_emitted = True

    @classmethod
    def tool_call_payload(cls, tool_call: object) -> JsonObject:
        payload = cls.payload_mapping(tool_call)
        tool_name = (
            cls.text(payload.get("name"))
            or cls.text(payload.get("tool_name"))
            or "unknown_tool"
        )
        call_id = (
            cls.text(payload.get("id"))
            or cls.text(payload.get("call_id"))
            or TraceContext.event_id()
        )
        args = payload.get("args", {})
        result: JsonObject = {
            "tool_name": tool_name,
            "call_id": call_id,
            "args": args if isinstance(args, Mapping) else {"delta": str(args)},
            "delta": str(args) if args and not isinstance(args, Mapping) else "",
            "status": payload.get("status", "started"),
        }
        summary = cls.text(payload.get("summary"))
        if summary is not None:
            result["summary"] = summary
        cls.apply_tool_visibility(result)
        return result

    def tool_call_state(
        self,
        run_id: str,
        namespace: StreamNamespace,
        tool_call: object,
    ) -> ToolCallStreamState:
        payload = self.payload_mapping(tool_call)
        tool_name = self.text(payload.get("name")) or self.text(
            payload.get("tool_name")
        )
        call_id = self.text(payload.get("id")) or self.text(payload.get("call_id"))
        key = self.tool_call_state_key(run_id, namespace, payload, call_id)
        state_key = (run_id, namespace.parts, key)
        state = self._tool_call_states.get(state_key)
        if state is None:
            state = ToolCallStreamState(namespace=namespace, key=key)
            self._tool_call_states[state_key] = state
        elif call_id is not None and state.call_id not in {None, call_id}:
            state = ToolCallStreamState(namespace=namespace, key=key)
            self._tool_call_states[state_key] = state
        if tool_name is not None:
            state.tool_name = tool_name
        summary = self.text(payload.get("summary"))
        if summary is not None:
            state.summary = summary
        if call_id is not None:
            state.call_id = call_id
            self._tool_call_ids[(run_id, call_id)] = state
        args = payload.get("args", {})
        if isinstance(args, Mapping):
            if "delta" in args:
                delta = self.raw_text(args.get("delta"))
                if delta is not None:
                    state.args_text += delta
                    state.last_delta = delta
            elif args:
                state.args = self.payload_mapping(args)
                state.last_delta = ""
        else:
            delta = self.raw_text(args)
            if delta is not None:
                state.args_text += delta
                state.last_delta = delta
        return state

    def tool_call_state_key(
        self,
        run_id: str,
        namespace: StreamNamespace,
        payload: Mapping[str, object],
        call_id: str | None,
    ) -> str:
        index = payload.get("index")
        if isinstance(index, int | str):
            normalized = str(index).strip()
            if normalized:
                return f"index:{normalized}"
        if call_id is not None:
            return f"call:{call_id}"
        namespace_states = [
            state
            for (state_run_id, parts, _), state in self._tool_call_states.items()
            if state_run_id == run_id and parts == namespace.parts
        ]
        if len(namespace_states) == 1:
            return namespace_states[0].key
        return "__current__"

    @classmethod
    def tool_call_payload_from_state(cls, state: ToolCallStreamState) -> JsonObject:
        args = state.args or cls.parse_args_text(state.args_text)
        payload: JsonObject = {
            "tool_name": state.tool_name or "unknown_tool",
            "call_id": state.call_id or TraceContext.event_id(),
            "args": args or ({"delta": state.args_text} if state.args_text else {}),
            "delta": state.last_delta if state.started_emitted else "",
            "status": "started",
        }
        if state.summary is not None:
            payload["summary"] = state.summary
        cls.apply_tool_visibility(payload)
        return payload

    @classmethod
    def parse_args_text(cls, value: str) -> JsonObject:
        if not value.strip():
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return cls.payload_mapping(parsed)

    @classmethod
    def tool_result_payload(cls, message: object) -> JsonObject:
        payload = cls.payload_mapping(message)
        tool_name = (
            cls.text(payload.get("name"))
            or cls.text(payload.get("tool_name"))
            or "unknown_tool"
        )
        call_id = (
            cls.text(payload.get("tool_call_id"))
            or cls.text(payload.get("id"))
            or cls.text(payload.get("call_id"))
            or TraceContext.event_id()
        )
        excluded = {
            "type",
            "name",
            "id",
            "tool_call_id",
            "call_id",
            "tool_name",
            "status",
        }
        output = {key: value for key, value in payload.items() if key not in excluded}
        result: JsonObject = {
            "tool_name": tool_name,
            "call_id": call_id,
            "status": payload.get("status", "completed"),
            "output": output or payload,
        }
        cls.apply_tool_visibility(result)
        return result

    def tool_result_payload_with_state(
        self, run_id: str, payload: JsonObject
    ) -> JsonObject:
        call_id = self.text(payload.get("call_id"))
        if call_id is None:
            return payload
        state = self._tool_call_ids.get((run_id, call_id))
        if state is None:
            return payload
        if payload.get("tool_name") == "unknown_tool" and state.tool_name is not None:
            payload = {**payload, "tool_name": state.tool_name}
        self.apply_tool_visibility(payload)
        return payload

    def tool_call_state_for_payload(
        self,
        run_id: str,
        payload: Mapping[str, object],
    ) -> ToolCallStreamState | None:
        call_id = self.text(payload.get("call_id"))
        if call_id is None:
            return None
        return self._tool_call_ids.get((run_id, call_id))

    @classmethod
    def apply_tool_visibility(cls, payload: JsonObject) -> None:
        if cls.is_internal_tool_name(cls.text(payload.get("tool_name"))):
            payload[Keys.Field.VISIBILITY] = RuntimeEventVisibility.INTERNAL.value

    @classmethod
    def is_internal_tool_name(cls, tool_name: str | None) -> bool:
        return tool_name in cls.internal_tool_names
