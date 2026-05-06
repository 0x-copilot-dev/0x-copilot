"""Tool call projection helpers for runtime stream events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent_runtime.api.constants import Keys, Values
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import JsonObject, StreamEventSource
from agent_runtime.observability.tracing import TraceContext
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventVisibility,
)
from runtime_worker.stream_messages import StreamMessageParser, StreamTextHelper
from runtime_worker.stream_parts import StreamNamespace
from runtime_worker.stream_subagents import StreamUpdateProcessor
from runtime_worker.tool_call_ledger import ToolCallLedger


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
    started_at: datetime | None = None


class StreamMessageProcessor:
    """Process message-type stream events: tool calls, tool results, text deltas.

    Standalone processor — no inheritance. Uses StreamMessageParser as a utility
    and delegates subagent lifecycle events to a supplied update_processor.
    """

    internal_tool_names = frozenset(
        {
            Values.Tool.WRITE_TODOS,
            # ask_a_question surfaces its own approval_requested card via the
            # native interrupt path; the tool_call_started/result events are
            # noise and would render a duplicate "ask_a_question running" tile.
            Values.Tool.ASK_A_QUESTION,
        }
    )
    large_result_artifact_tool_names = frozenset(
        {
            Values.Tool.READ_FILE,
            Values.Tool.RG,
            Values.Tool.GREP,
            Values.Tool.SEARCH_FILES,
        }
    )

    class _Fields:
        INDEX = "index"

    def __init__(
        self,
        event_producer: RuntimeEventProducer,
        update_processor: StreamUpdateProcessor,
    ) -> None:
        self.event_producer = event_producer
        self._update_processor = update_processor
        self._tool_call_states: dict[
            tuple[str, tuple[str, ...], str], ToolCallStreamState
        ] = {}
        self._tool_call_ids: dict[tuple[str, str], ToolCallStreamState] = {}
        # Per-run lifecycle ledger of in-flight tool calls. Lazily created on
        # first tool_call_started, used by `RuntimeRunHandler` to settle
        # orphaned calls when a run hits a terminal failure path.
        self._ledgers: dict[str, ToolCallLedger] = {}
        # Per-run reasoning span accumulator. Keyed by run_id; value is the
        # text assembled across delta chunks for the currently-open span.
        # Cleared on emission of the final ``reasoning_summary`` cap (or
        # on run discard). Subagent reasoning is intentionally dropped at
        # the extraction site, so this dict only ever sees main-agent runs.
        self._reasoning_buffers: dict[str, str] = {}

    def ledger_for_run(self, run_id: str) -> ToolCallLedger:
        """Return (and lazily create) the per-run tool call ledger."""

        ledger = self._ledgers.get(run_id)
        if ledger is None:
            ledger = ToolCallLedger(run_id=run_id)
            self._ledgers[run_id] = ledger
        return ledger

    def discard_ledger(self, run_id: str) -> None:
        """Free per-run ledger state once the run has reached a terminal state."""

        self._ledgers.pop(run_id, None)
        self._reasoning_buffers.pop(run_id, None)

    async def emit_reasoning_events(
        self,
        *,
        run: RunRecord,
        namespace: StreamNamespace,
        message: object,
        metadata: JsonObject,
        parent_task_id: str | None,
        subagent_id: str | None,
    ) -> None:
        """Emit ``reasoning_summary_delta`` (per chunk) and ``reasoning_summary``
        (cap) events from a parsed message chunk.

        Subagent runs are dropped (matches the text path in
        ``StreamOrchestrator.stream_delta`` and the v1 design decision: the
        subagent fleet card carries its own progress affordance; we do not
        bubble the subagent's thinking up to the parent thread).
        """

        if namespace.is_subagent:
            return
        delta = StreamMessageParser.reasoning_delta(message)
        if delta is not None:
            buffer = self._reasoning_buffers.get(run.run_id, "")
            self._reasoning_buffers[run.run_id] = buffer + delta
            await self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.MODEL,
                event_type=RuntimeApiEventType.REASONING_SUMMARY_DELTA,
                payload={
                    Keys.Payload.DELTA: delta,
                    Keys.Field.SUMMARY: delta,
                },
                summary=delta,
                metadata=metadata,
                parent_task_id=parent_task_id,
                subagent_id=subagent_id,
            )
        if not StreamMessageParser.reasoning_finalised(message):
            return
        assembled = self._reasoning_buffers.pop(run.run_id, "")
        if not assembled:
            return
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.MODEL,
            event_type=RuntimeApiEventType.REASONING_SUMMARY,
            payload={Keys.Field.SUMMARY: assembled},
            summary=assembled,
            metadata=metadata,
            parent_task_id=parent_task_id,
            subagent_id=subagent_id,
        )

    async def process(
        self,
        *,
        run: RunRecord,
        namespace: StreamNamespace,
        message: object,
        delta: str | None,
    ) -> None:
        metadata = namespace.metadata("messages")
        # The LangGraph subgraph task id is an internal UUID; resolve it to the
        # supervisor's `task` tool call_id so child events nest under the
        # subagent_started card via a shared identifier.
        subgraph_task_id = namespace.subagent_task_id
        parent_task_id = self._update_processor.subagent_call_id_for_subgraph(
            run_id=run.run_id,
            subgraph_task_id=subgraph_task_id,
        )
        subagent_id = self._update_processor.subagent_id_for_subgraph(
            run_id=run.run_id,
            subgraph_task_id=subgraph_task_id,
        )

        await self.emit_reasoning_events(
            run=run,
            namespace=namespace,
            message=message,
            metadata=metadata,
            parent_task_id=parent_task_id,
            subagent_id=subagent_id,
        )

        for tool_call in StreamMessageParser.tool_call_chunks(message):
            await self.append_tool_call_chunk_event(
                run=run,
                namespace=namespace,
                tool_call=tool_call,
                metadata=metadata,
                parent_task_id=parent_task_id,
                subagent_id=subagent_id,
            )

        if StreamMessageParser.is_tool_result_message(message):
            payload = self.tool_result_payload(message)
            payload = self.tool_result_payload_with_state(run.run_id, payload)
            if payload[Keys.Field.TOOL_NAME] == Values.Tool.TASK:
                state = self.tool_call_state_for_payload(run.run_id, payload)
                await self._update_processor.append_task_lifecycle_event(
                    run=run,
                    event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
                    payload=self._update_processor.task_tool_result_payload(
                        payload,
                        subagent_name=state.subagent_name
                        if state is not None
                        else None,
                        short_summary=state.short_summary
                        if state is not None
                        else None,
                    ),
                    metadata=metadata,
                )
                return
            duration_ms = self._tool_duration_ms(run.run_id, payload)
            if duration_ms is not None:
                payload["duration_ms"] = duration_ms
            await self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_RESULT,
                payload=payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
                subagent_id=subagent_id,
            )
            settled_call_id = StreamTextHelper.extract(payload.get(Keys.Field.CALL_ID))
            if settled_call_id is not None:
                self.ledger_for_run(run.run_id).observed_settled(settled_call_id)
            completed_payload: JsonObject = {
                Keys.Field.TOOL_NAME: payload[Keys.Field.TOOL_NAME],
                Keys.Field.CALL_ID: payload[Keys.Field.CALL_ID],
                # Mirror the tool_result status so a failed/timed_out result
                # produces a failed/timed_out completed event. Settlement is
                # owned by the tool_result; this mirror keeps presentation
                # downstream consistent.
                Keys.Field.STATUS: payload.get(
                    Keys.Field.STATUS, Values.Status.COMPLETED
                ),
            }
            if duration_ms is not None:
                completed_payload["duration_ms"] = duration_ms
            if (
                StreamTextHelper.extract(payload.get(Keys.Field.VISIBILITY))
                == RuntimeEventVisibility.INTERNAL.value
            ):
                self.mark_internal_visibility(completed_payload)
            else:
                self.apply_tool_visibility(completed_payload)
            await self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_CALL_COMPLETED,
                payload=completed_payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
                subagent_id=subagent_id,
            )

    async def append_tool_call_chunk_event(
        self,
        *,
        run: RunRecord,
        namespace: StreamNamespace,
        tool_call: object,
        metadata: JsonObject,
        parent_task_id: str | None,
        subagent_id: str | None = None,
    ) -> None:
        state = self.tool_call_state(run.run_id, namespace, tool_call)
        if state.tool_name == Values.Tool.TASK:
            await self._append_task_tool_call_event(
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
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.TOOL,
            event_type=event_type,
            payload=payload,
            metadata=metadata,
            parent_task_id=parent_task_id,
            subagent_id=subagent_id,
        )
        if event_type is RuntimeApiEventType.TOOL_CALL_STARTED:
            state.started_at = datetime.now(timezone.utc)
            if state.call_id is not None and state.tool_name is not None:
                self.ledger_for_run(run.run_id).started(
                    call_id=state.call_id,
                    tool_name=state.tool_name,
                    parent_task_id=parent_task_id,
                    subagent_id=subagent_id,
                )
        state.started_emitted = True

    async def _append_task_tool_call_event(
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
        payload = self._update_processor.task_tool_call_payload(
            call_id=state.call_id,
            args_payload=args,
        )
        state.subagent_name = StreamTextHelper.extract(payload.get("subagent_name"))
        state.short_summary = StreamTextHelper.extract(
            payload.get(Keys.Field.SHORT_SUMMARY)
        )
        await self._update_processor.append_task_lifecycle_event(
            run=run,
            event_type=RuntimeApiEventType.SUBAGENT_STARTED,
            payload=payload,
            metadata=metadata,
        )
        state.started_emitted = True

    @classmethod
    def tool_call_payload(cls, tool_call: object) -> JsonObject:
        payload = StreamMessageParser.payload_mapping(tool_call)
        tool_name = (
            StreamTextHelper.extract(payload.get(Keys.Field.NAME))
            or StreamTextHelper.extract(payload.get(Keys.Field.TOOL_NAME))
            or Values.Tool.UNKNOWN_TOOL
        )
        call_id = (
            StreamTextHelper.extract(payload.get(Keys.Field.ID))
            or StreamTextHelper.extract(payload.get(Keys.Field.CALL_ID))
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
        summary = StreamTextHelper.extract(payload.get(Keys.Field.SUMMARY))
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
        payload = StreamMessageParser.payload_mapping(tool_call)
        tool_name = StreamTextHelper.extract(
            payload.get(Keys.Field.NAME)
        ) or StreamTextHelper.extract(payload.get(Keys.Field.TOOL_NAME))
        call_id = StreamTextHelper.extract(
            payload.get(Keys.Field.ID)
        ) or StreamTextHelper.extract(payload.get(Keys.Field.CALL_ID))
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
        summary = StreamTextHelper.extract(payload.get(Keys.Field.SUMMARY))
        if summary is not None:
            state.summary = summary
        if call_id is not None:
            state.call_id = call_id
            self._tool_call_ids[(run_id, call_id)] = state
        args = payload.get(Keys.Field.ARGS, {})
        if isinstance(args, Mapping):
            if Keys.Payload.DELTA in args:
                delta = StreamMessageParser.raw_text(args.get(Keys.Payload.DELTA))
                if delta is not None:
                    state.args_text += delta
                    state.last_delta = delta
            elif args:
                state.args = StreamMessageParser.payload_mapping(args)
                state.last_delta = ""
        else:
            delta = StreamMessageParser.raw_text(args)
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
        index = payload.get(self._Fields.INDEX)
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
        if StreamTextHelper.extract(state.tool_name) != Values.Tool.READ_FILE:
            return True
        return (
            StreamTextHelper.extract(args.get(Keys.Field.FILE_PATH)) is not None
            or StreamTextHelper.extract(args.get(Keys.Field.PATH)) is not None
        )

    @classmethod
    def parse_args_text(cls, value: str) -> JsonObject:
        if not value.strip():
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return StreamMessageParser.payload_mapping(parsed)

    # LangChain `ToolMessage.status` values mapped onto our public
    # `tool_result.status` strings. ``error`` is what the LangGraph tool
    # executor sets when a tool raised — we want to surface that as a typed
    # ``failed`` outcome, not the silent ``completed`` default.
    _TOOL_MESSAGE_STATUS_MAP: dict[str, str] = {
        "error": Values.Status.FAILED,
        "success": Values.Status.COMPLETED,
    }

    @classmethod
    def tool_result_payload(cls, message: object) -> JsonObject:
        payload = StreamMessageParser.payload_mapping(message)
        tool_name = (
            StreamTextHelper.extract(payload.get(Keys.Field.NAME))
            or StreamTextHelper.extract(payload.get(Keys.Field.TOOL_NAME))
            or Values.Tool.UNKNOWN_TOOL
        )
        call_id = (
            StreamTextHelper.extract(payload.get(Keys.Field.TOOL_CALL_ID))
            or StreamTextHelper.extract(payload.get(Keys.Field.ID))
            or StreamTextHelper.extract(payload.get(Keys.Field.CALL_ID))
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
        raw_status = payload.get(Keys.Field.STATUS)
        status: object
        if isinstance(raw_status, str):
            status = cls._TOOL_MESSAGE_STATUS_MAP.get(raw_status.lower(), raw_status)
        else:
            status = Values.Status.COMPLETED
        result: JsonObject = {
            Keys.Field.TOOL_NAME: tool_name,
            Keys.Field.CALL_ID: call_id,
            Keys.Field.STATUS: status,
            Keys.Field.OUTPUT: output or payload,
        }
        cls.apply_tool_visibility(result)
        return result

    def tool_result_payload_with_state(
        self, run_id: str, payload: JsonObject
    ) -> JsonObject:
        call_id = StreamTextHelper.extract(payload.get(Keys.Field.CALL_ID))
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
        call_id = StreamTextHelper.extract(payload.get(Keys.Field.CALL_ID))
        if call_id is None:
            return None
        return self._tool_call_ids.get((run_id, call_id))

    def _tool_duration_ms(
        self,
        run_id: str,
        payload: Mapping[str, object],
    ) -> int | None:
        state = self.tool_call_state_for_payload(run_id, payload)
        if state is None or state.started_at is None:
            return None
        elapsed = datetime.now(timezone.utc) - state.started_at
        return max(0, round(elapsed.total_seconds() * 1000))

    @classmethod
    def apply_tool_visibility(cls, payload: JsonObject) -> None:
        if cls.is_internal_tool_name(
            StreamTextHelper.extract(payload.get(Keys.Field.TOOL_NAME))
        ):
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
            StreamTextHelper.extract(payload.get(Keys.Field.TOOL_NAME))
        ):
            return False
        args = payload.get(Keys.Field.ARGS)
        if not isinstance(args, Mapping):
            return False
        path = StreamTextHelper.extract(
            args.get(Keys.Field.FILE_PATH)
        ) or StreamTextHelper.extract(args.get(Keys.Field.PATH))
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
