"""Tool call projection helpers for runtime stream events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from agent_runtime.api.constants import Keys, Values
from agent_runtime.api.events import RuntimeEventProducer
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
    short_summary: str | None = None
    started_emitted: bool = False
    pending_start: bool = False


class ToolEventProjector(StreamMessageParser):
    """Project provider tool-call chunks and tool-result messages."""

    event_producer: RuntimeEventProducer

    internal_tool_names = frozenset({Values.Tool.WRITE_TODOS})
    large_result_artifact_tool_names = frozenset(
        {
            Values.Tool.READ_FILE,
            Values.Tool.RG,
            Values.Tool.GREP,
            Values.Tool.SEARCH_FILES,
        }
    )

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
        if state.tool_name == Values.Tool.TASK:
            self.append_task_tool_call_event(
                run=run,
                state=state,
                metadata=metadata,
            )
            return
        if state.tool_name is None or state.call_id is None:
            return
        if not state.started_emitted and not self.tool_call_state_ready_to_emit(state):
            state.pending_start = True
            return
        payload = self.tool_call_payload_from_state(state)
        event_type = (
            RuntimeApiEventType.TOOL_CALL_STARTED
            if not state.started_emitted
            else RuntimeApiEventType.TOOL_CALL_DELTA
        )
        if event_type is RuntimeApiEventType.TOOL_CALL_DELTA:
            payload[Keys.Field.STATUS] = Values.Status.RUNNING
        elif state.pending_start:
            state.pending_start = False
        self.event_producer.append_api_event(
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
            cls.text(payload.get(Keys.Field.NAME))
            or cls.text(payload.get(Keys.Field.TOOL_NAME))
            or Values.Tool.UNKNOWN_TOOL
        )
        call_id = (
            cls.text(payload.get(Keys.Field.ID))
            or cls.text(payload.get(Keys.Field.CALL_ID))
            or TraceContext.event_id()
        )
        args = payload.get(Keys.Field.ARGS, {})
        result: JsonObject = {
            Keys.Field.TOOL_NAME: tool_name,
            Keys.Field.CALL_ID: call_id,
            Keys.Field.ARGS: args
            if isinstance(args, Mapping)
            else {Keys.Payload.DELTA: str(args)},
            Keys.Payload.DELTA: str(args)
            if args and not isinstance(args, Mapping)
            else "",
            Keys.Field.STATUS: payload.get(Keys.Field.STATUS, Values.Status.STARTED),
        }
        summary = cls.text(payload.get(Keys.Field.SUMMARY))
        if summary is not None:
            result[Keys.Field.SUMMARY] = summary
        cls.apply_tool_visibility(result)
        return result

    def tool_call_state(
        self,
        run_id: str,
        namespace: StreamNamespace,
        tool_call: object,
    ) -> ToolCallStreamState:
        payload = self.payload_mapping(tool_call)
        tool_name = self.text(payload.get(Keys.Field.NAME)) or self.text(
            payload.get(Keys.Field.TOOL_NAME)
        )
        call_id = self.text(payload.get(Keys.Field.ID)) or self.text(
            payload.get(Keys.Field.CALL_ID)
        )
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
        summary = self.text(payload.get(Keys.Field.SUMMARY))
        if summary is not None:
            state.summary = summary
        if call_id is not None:
            state.call_id = call_id
            self._tool_call_ids[(run_id, call_id)] = state
        args = payload.get(Keys.Field.ARGS, {})
        if isinstance(args, Mapping):
            if Keys.Payload.DELTA in args:
                delta = self.raw_text(args.get(Keys.Payload.DELTA))
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
            Keys.Field.TOOL_NAME: state.tool_name or Values.Tool.UNKNOWN_TOOL,
            Keys.Field.CALL_ID: state.call_id or TraceContext.event_id(),
            Keys.Field.ARGS: args
            or ({Keys.Payload.DELTA: state.args_text} if state.args_text else {}),
            Keys.Payload.DELTA: state.last_delta if state.started_emitted else "",
            Keys.Field.STATUS: Values.Status.STARTED,
        }
        if state.summary is not None:
            payload[Keys.Field.SUMMARY] = state.summary
        cls.apply_tool_visibility(payload)
        return payload

    @classmethod
    def tool_call_state_ready_to_emit(cls, state: ToolCallStreamState) -> bool:
        if not cls.is_path_classified_artifact_tool_name(state.tool_name):
            return True
        args = state.args or cls.parse_args_text(state.args_text)
        if not args:
            return False
        if cls.text(state.tool_name) != Values.Tool.READ_FILE:
            return True
        return (
            cls.text(args.get(Keys.Field.FILE_PATH)) is not None
            or cls.text(args.get(Keys.Field.PATH)) is not None
        )

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
            cls.text(payload.get(Keys.Field.NAME))
            or cls.text(payload.get(Keys.Field.TOOL_NAME))
            or Values.Tool.UNKNOWN_TOOL
        )
        call_id = (
            cls.text(payload.get(Keys.Field.TOOL_CALL_ID))
            or cls.text(payload.get(Keys.Field.ID))
            or cls.text(payload.get(Keys.Field.CALL_ID))
            or TraceContext.event_id()
        )
        excluded = {
            Keys.Field.TYPE,
            Keys.Field.NAME,
            Keys.Field.ID,
            Keys.Field.TOOL_CALL_ID,
            Keys.Field.CALL_ID,
            Keys.Field.TOOL_NAME,
            Keys.Field.STATUS,
        }
        output = {key: value for key, value in payload.items() if key not in excluded}
        result: JsonObject = {
            Keys.Field.TOOL_NAME: tool_name,
            Keys.Field.CALL_ID: call_id,
            Keys.Field.STATUS: payload.get(Keys.Field.STATUS, Values.Status.COMPLETED),
            Keys.Field.OUTPUT: output or payload,
        }
        cls.apply_tool_visibility(result)
        return result

    def tool_result_payload_with_state(
        self, run_id: str, payload: JsonObject
    ) -> JsonObject:
        call_id = self.text(payload.get(Keys.Field.CALL_ID))
        if call_id is None:
            return payload
        state = self._tool_call_ids.get((run_id, call_id))
        if state is None:
            return payload
        if (
            payload.get(Keys.Field.TOOL_NAME) == Values.Tool.UNKNOWN_TOOL
            and state.tool_name is not None
        ):
            payload = {**payload, Keys.Field.TOOL_NAME: state.tool_name}
        if self.is_large_result_artifact_state(state):
            self.mark_internal_visibility(payload)
        self.apply_tool_visibility(payload)
        return payload

    def tool_call_state_for_payload(
        self,
        run_id: str,
        payload: Mapping[str, object],
    ) -> ToolCallStreamState | None:
        call_id = self.text(payload.get(Keys.Field.CALL_ID))
        if call_id is None:
            return None
        return self._tool_call_ids.get((run_id, call_id))

    @classmethod
    def apply_tool_visibility(cls, payload: JsonObject) -> None:
        if cls.is_internal_tool_name(cls.text(payload.get(Keys.Field.TOOL_NAME))):
            cls.mark_internal_visibility(payload)
        if cls.is_large_result_artifact_payload(payload):
            cls.mark_internal_visibility(payload)

    @classmethod
    def mark_internal_visibility(cls, payload: JsonObject) -> None:
        payload[Keys.Field.VISIBILITY] = RuntimeEventVisibility.INTERNAL.value

    @classmethod
    def is_internal_tool_name(cls, tool_name: str | None) -> bool:
        return tool_name in cls.internal_tool_names

    @classmethod
    def is_large_result_artifact_state(cls, state: ToolCallStreamState) -> bool:
        args = state.args or cls.parse_args_text(state.args_text)
        payload: JsonObject = {
            Keys.Field.TOOL_NAME: state.tool_name,
            Keys.Field.ARGS: args,
        }
        return cls.is_large_result_artifact_payload(payload)

    @classmethod
    def is_large_result_artifact_payload(cls, payload: Mapping[str, object]) -> bool:
        if not cls.is_large_result_artifact_tool_name(
            cls.text(payload.get(Keys.Field.TOOL_NAME))
        ):
            return False
        args = payload.get(Keys.Field.ARGS)
        if not isinstance(args, Mapping):
            return False
        path = cls.text(args.get(Keys.Field.FILE_PATH)) or cls.text(
            args.get(Keys.Field.PATH)
        )
        return bool(
            path and path.startswith(Values.VirtualPath.LARGE_TOOL_RESULTS_PREFIX)
        )

    @classmethod
    def is_large_result_artifact_tool_name(cls, tool_name: str | None) -> bool:
        if tool_name is None:
            return False
        normalized = tool_name.strip().lower()
        return (
            normalized in cls.large_result_artifact_tool_names or "search" in normalized
        )

    @classmethod
    def is_path_classified_artifact_tool_name(cls, tool_name: str | None) -> bool:
        if tool_name is None:
            return False
        return tool_name.strip().lower() in cls.large_result_artifact_tool_names
