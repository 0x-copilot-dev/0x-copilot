"""Tool call projection helpers for runtime stream events."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent_runtime.api.constants import Keys, Values
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.capabilities.mcp.connector_resolver import ToolConnectorResolver
from agent_runtime.capabilities.mcp.dispatcher import McpDispatcherUnwrap
from agent_runtime.capabilities.todo_list import TodoListProjector
from agent_runtime.capabilities.workspace.policy_answers import WorkspacePolicyAnswers
from agent_runtime.execution.contracts import (
    ConnectorAccessMode,
    JsonObject,
    StreamEventSource,
)
from agent_runtime.execution.tool_outcomes import ToolInvocationOutcome
from agent_runtime.observability.redactor import ToolArgumentRedactor
from agent_runtime.observability.tracing import TraceContext
from agent_runtime.persistence.records import ToolInvocationRecord
from agent_runtime.persistence.records.common import ToolInvocationStatus
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventVisibility,
)
from runtime_worker.stream_messages import StreamMessageParser, StreamTextHelper
from runtime_worker.stream_parts import StreamNamespace
from runtime_worker.stream_subagents import StreamUpdateProcessor
from runtime_worker.tool_call_ledger import ToolCallLedger
from runtime_worker.tool_result_offload import ToolResultOffloader

logger = logging.getLogger(__name__)


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
    # The same arguments before ``payload_mapping`` flattened them. ``args`` is
    # display-shaped and lossy for any argument holding a list of objects (see
    # ``StreamMessageParser.raw_args``); consumers that must read the structure
    # — the todo-list projector — read this. Absent on the streamed-delta path,
    # where ``args_text`` already holds the untouched JSON.
    raw_args: object | None = None
    summary: str | None = None
    subagent_name: str | None = None
    short_summary: str | None = None
    started_emitted: bool = False
    pending_start: bool = False
    started_at: datetime | None = None
    # The ``running`` ledger row minted at TOOL_CALL_STARTED. Held so the
    # terminal write reuses the SAME ``invocation_id`` and therefore UPDATES
    # that row (both stores upsert on it) instead of appending a second one.
    # Without it there was no way to close the row, and every invocation in
    # the ledger stayed ``running`` with a null ``completed_at`` forever.
    invocation: ToolInvocationRecord | None = None


class StreamMessageProcessor:
    """Process message-type stream events: tool-call start/delta/result and text deltas."""

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
        *,
        tool_result_offloader: ToolResultOffloader | None = None,
    ) -> None:
        """Wire the event producer and update sub-processor; initialise per-run state dicts.

        ``tool_result_offloader`` is supplied only on the desktop file-store
        path; when ``None`` (postgres / in-memory / web) tool results are emitted
        inline exactly as before.
        """
        self.event_producer = event_producer
        self._update_processor = update_processor
        self._tool_result_offloader = tool_result_offloader
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
        # Resolves ``write_todos`` writes into the checklist snapshots the todo
        # panel renders. Held for the worker's lifetime (it keys its own state
        # per run + subagent) and freed per run in ``discard_ledger``.
        self._todo_lists = TodoListProjector()
        # Per-run ``tool_name → server_slug`` snapshots for per-tool MCP
        # registration (P2-6). Keyed by run because one worker serves runs from
        # different users, whose installed connectors differ — a worker-wide map
        # could attribute one run's tool to another run's connector. Empty until
        # the per-tool flip publishes one (P2-8), which is what keeps this seam
        # inert under the legacy ``call_mcp_tool`` gateway.
        self._connector_resolvers: dict[str, ToolConnectorResolver] = {}

    def ledger_for_run(self, run_id: str) -> ToolCallLedger:
        """Return (and lazily create) the per-run tool call ledger."""

        ledger = self._ledgers.get(run_id)
        if ledger is None:
            ledger = ToolCallLedger(run_id=run_id)
            self._ledgers[run_id] = ledger
        return ledger

    def publish_connector_resolver(
        self,
        run_id: str,
        resolver: ToolConnectorResolver,
    ) -> None:
        """Bind ``run_id``'s registration-time ``tool_name → server_slug`` map.

        Called once per run by the per-tool registration path (P2-8), after the
        tool source has published its map. Nothing calls it under the legacy
        gateway, so no run has a resolver and the connector-identity seams keep
        resolving exactly as they did — through ``McpDispatcherUnwrap``.
        """

        self._connector_resolvers[run_id] = resolver

    def discard_ledger(self, run_id: str) -> None:
        """Free per-run ledger state once the run has reached a terminal state."""

        self._ledgers.pop(run_id, None)
        self._reasoning_buffers.pop(run_id, None)
        self._todo_lists.discard_run(run_id)
        self._connector_resolvers.pop(run_id, None)
        # Per-call stream state was never dropped here. It survived every run
        # in a long-lived worker, and each entry now also holds the call's
        # ``ToolInvocationRecord`` (args included), so leaving it would grow the
        # leak in proportion to what the ledger fix added.
        for key in [key for key in self._tool_call_ids if key[0] == run_id]:
            del self._tool_call_ids[key]

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
        """Emit reasoning_summary_delta per chunk and a reasoning_summary cap on span close; skips subagents."""

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
        """Dispatch a message chunk to reasoning, tool-call, and tool-result processors."""
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
            self.add_tool_presentation_fields(
                run=run,
                payload=payload,
                parent_task_id=parent_task_id,
            )
            # Desktop file store only: park an oversized ``output`` in the object
            # store, leaving the event with a bounded preview + ``output_ref``.
            # No-op (offloader is None) on every other backend, and the ``task``
            # tool result — routed below to a SUBAGENT_COMPLETED lifecycle event
            # rather than a TOOL_RESULT — is left untouched.
            if (
                self._tool_result_offloader is not None
                and payload[Keys.Field.TOOL_NAME] != Values.Tool.TASK
            ):
                payload = self._tool_result_offloader.apply(
                    payload,
                    trace_id=run.trace_id or run.run_id,
                    projection_key=run.run_id,
                    projection_content=StreamMessageParser.raw_content(message),
                )
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
                    parent_task_id=parent_task_id,
                )
                return
            duration_ms = self._tool_duration_ms(run.run_id, payload)
            if duration_ms is not None:
                payload[Keys.Field.DURATION_MS] = duration_ms
            await self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_RESULT,
                payload=payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
                subagent_id=subagent_id,
            )
            await self._append_todo_list_event(
                run=run,
                payload=payload,
                metadata=metadata,
                parent_task_id=parent_task_id,
                subagent_id=subagent_id,
            )
            settled_call_id = StreamTextHelper.extract(payload.get(Keys.Field.CALL_ID))
            if settled_call_id is not None:
                self.ledger_for_run(run.run_id).observed_settled(settled_call_id)
                # Close the persisted ledger row in the same beat the in-memory
                # ledger settles. These two had drifted apart: the in-memory
                # one settled here, the durable one never did.
                await self.close_tool_invocation(
                    run=run,
                    call_id=settled_call_id,
                    **ToolInvocationOutcome.from_result_payload(payload),
                )
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
                completed_payload[Keys.Field.DURATION_MS] = duration_ms
            for key in (
                Keys.Field.PROVENANCE,
                Keys.Field.ACCESS_MODE,
                Keys.Field.SUBAGENT_TASK_IDS,
            ):
                if key in payload:
                    completed_payload[key] = payload[key]
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

    async def _append_todo_list_event(
        self,
        *,
        run: RunRecord,
        payload: JsonObject,
        metadata: JsonObject,
        parent_task_id: str | None,
        subagent_id: str | None,
    ) -> None:
        """Publish the agent's checklist when ``payload`` settles a ``write_todos`` call.

        The tool's own frames stay INTERNAL (``internal_tool_names``) — this is
        the public rendering of them, and the only one. Read from the settled
        call's accumulated ARGUMENTS rather than the tool's output: the output is
        a prose echo (``"Updated todo list to [...]"``) while the arguments carry
        the structured list, and by this seam the streamed argument buffer is
        complete where at TOOL_CALL_STARTED it is frequently still partial.
        """

        if payload.get(Keys.Field.TOOL_NAME) != Values.Tool.WRITE_TODOS:
            return
        state = self.tool_call_state_for_payload(run.run_id, payload)
        if state is None:
            return
        snapshot = self._todo_lists.project(
            run_id=run.run_id,
            subagent_id=subagent_id,
            arguments=self._structured_arguments(state),
        )
        # ``None`` means the write carried no usable list, or repeated the
        # current one verbatim; either way there is no state change to publish.
        if snapshot is None:
            return
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.TOOL,
            event_type=RuntimeApiEventType.TODO_LIST_UPDATED,
            payload=snapshot.model_dump(mode="json"),
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
        """Emit TOOL_CALL_STARTED or TOOL_CALL_DELTA for a streaming tool-call chunk, routing task calls separately."""
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
        self.add_tool_presentation_fields(
            run=run,
            payload=payload,
            parent_task_id=parent_task_id,
        )
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
                # The MCP server hosting this call — the dispatcher's nested
                # server name under the legacy gateway, the run's registration
                # map under per-tool — else None (a native tool: a step, not
                # an app).
                connector_slug = self.connector_slug_for_payload(
                    run_id=run.run_id,
                    payload=payload,
                )
                self.ledger_for_run(run.run_id).started(
                    call_id=state.call_id,
                    tool_name=state.tool_name,
                    parent_task_id=parent_task_id,
                    subagent_id=subagent_id,
                    connector_slug=connector_slug,
                )
                # PRD-08 D1b — persist one runtime_tool_invocations row for this
                # tool call (backs Activity's step/app counters). Fire-and-forget:
                # a ledger write must never fail the run.
                await self._record_tool_invocation(
                    run=run,
                    state=state,
                    connector_slug=connector_slug,
                    subagent_id=subagent_id,
                )
        state.started_emitted = True

    async def _record_tool_invocation(
        self,
        *,
        run: RunRecord,
        state: ToolCallStreamState,
        connector_slug: str | None,
        subagent_id: str | None,
    ) -> None:
        """Write one tool-invocation row at the TOOL_CALL_STARTED seam (PRD-08 D1b).

        Best-effort: any persistence error is swallowed with a warning so tool
        execution and the run are never blocked.
        """

        if state.call_id is None or state.tool_name is None:
            return
        record = ToolInvocationRecord(
            run_id=run.run_id,
            org_id=run.org_id,
            task_id=subagent_id,
            tool_name=state.tool_name,
            connector_slug=connector_slug,
            call_id=state.call_id,
            status=ToolInvocationStatus.RUNNING,
            args=ToolArgumentRedactor.redact(self._state_arguments(state)),
            started_at=state.started_at,
        )
        # Held even if the write below fails: the terminal write is what makes
        # the row honest, and it must target the same id either way.
        state.invocation = record
        try:
            await self.event_producer.persistence.record_tool_invocation(record)
        except Exception:  # noqa: BLE001 — ledger write is best-effort.
            logger.warning(
                "tool_invocation_record_failed",
                extra={"run_id": run.run_id, "call_id": state.call_id},
                exc_info=True,
            )

    @staticmethod
    def _state_arguments(state: ToolCallStreamState) -> JsonObject:
        """Best available arguments for a call: the parsed dict, else the buffered JSON text.

        Streamed tool calls accumulate their arguments as text deltas, so at
        TOOL_CALL_STARTED this is often still partial (or empty). The terminal
        write re-reads it once the buffer is complete, which is what makes a
        failing call's payload recoverable from the ledger.
        """

        if state.args:
            return state.args
        return StreamMessageProcessor.parse_args_text(state.args_text)

    @staticmethod
    def _structured_arguments(state: ToolCallStreamState) -> Mapping[str, object]:
        """Arguments for a call with nested structure intact, not display-flattened.

        The sibling of :meth:`_state_arguments`. That one is right for the event
        payload and the ledger row, where a readable string beats a nested
        object; this one is for consumers that must read the shape the model
        actually sent. The two paths a provider can take both survive here: a
        complete argument mapping is held raw on the state, and a streamed one
        is re-parsed from its untouched JSON buffer.
        """

        if isinstance(state.raw_args, Mapping):
            return state.raw_args
        if state.args_text.strip():
            try:
                parsed = json.loads(state.args_text)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, Mapping):
                return parsed
        return {}

    async def close_tool_invocation(
        self,
        *,
        run: RunRecord,
        call_id: str,
        status: ToolInvocationStatus,
        result_summary: JsonObject | None = None,
        safe_error_code: str | None = None,
        safe_error_message: str | None = None,
    ) -> None:
        """Write the terminal ledger row for ``call_id``: status + ``completed_at`` + error.

        THE closing half of the ledger, missing since PRD-08 D1b: only the
        ``running`` row was ever written, so a run could finish with every
        invocation still open and no record of what failed or why. Both stores
        upsert on ``invocation_id`` — and the postgres adapter's
        ``ON CONFLICT DO UPDATE`` already listed exactly the columns written
        here — so re-putting the held record closes the row in place.

        Called from the natural ``tool_result`` seam AND from the run handler's
        terminal reconciliation, so failure and cancellation close too. Like
        the opening write it is best-effort: a ledger write must never fail a
        run, least of all one that is already failing.
        """

        state = self._tool_call_ids.get((run.run_id, call_id))
        # No held record means no row was ever opened for this call, so there is
        # nothing to close — closing would append an orphan rather than settle
        # anything.
        if state is None or state.invocation is None:
            return
        terminal = state.invocation.model_copy(
            update={
                "status": status,
                # Re-read the arguments: by now the streamed buffer is complete,
                # whereas at TOOL_CALL_STARTED it was frequently still empty.
                "args": ToolArgumentRedactor.redact(self._state_arguments(state)),
                "result_summary": result_summary or {},
                "completed_at": datetime.now(timezone.utc),
                "safe_error_code": safe_error_code,
                "safe_error_message": safe_error_message,
            }
        )
        # Replacing the held record makes a second close idempotent rather than
        # re-opening the row: the reconciler can run over a call the natural
        # seam already settled.
        state.invocation = terminal
        try:
            await self.event_producer.persistence.record_tool_invocation(terminal)
        except Exception:  # noqa: BLE001 — ledger write is best-effort.
            logger.warning(
                "tool_invocation_close_failed",
                extra={"run_id": run.run_id, "call_id": call_id},
                exc_info=True,
            )

    async def _append_task_tool_call_event(
        self,
        *,
        run: RunRecord,
        state: ToolCallStreamState,
        metadata: JsonObject,
    ) -> None:
        """Emit a SUBAGENT_STARTED lifecycle event once the task tool call's args are fully buffered."""
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
            parent_task_id=self._update_processor.subagent_call_id_for_subgraph(
                run_id=run.run_id,
                subgraph_task_id=state.namespace.subagent_task_id,
            ),
        )
        state.started_emitted = True

    @classmethod
    def tool_call_payload(cls, tool_call: object) -> JsonObject:
        """Build a normalised tool-call event payload from a raw tool-call chunk."""
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
        """Retrieve or create the incremental state for a streaming tool-call chunk, accumulating args deltas."""
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
                state.raw_args = StreamMessageParser.raw_args(tool_call)
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
        """Derive a stable state-bucket key from chunk index, call_id, or namespace context."""
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
        """Materialise a TOOL_CALL_STARTED or TOOL_CALL_DELTA event payload from accumulated state."""
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
        """Return ``True`` when enough state is buffered to emit a started event (path-classified tools require args)."""
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
        """Parse accumulated JSON args text; returns an empty dict on failure or blank input."""
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
    _TOOL_ERROR_CONTENT_PARSE_CAP = 64 * 1024

    @classmethod
    def _structured_tool_error(
        cls, output: Mapping[str, object]
    ) -> tuple[JsonObject, Mapping[str, object]] | None:
        """Return a normalised typed error that a tool returned as data.

        LangChain marks a ``ToolMessage`` successful when the callable returns
        normally, even if the returned value is our typed ``{"error": ...}``
        envelope. On the common provider path that mapping is JSON-serialised
        into ``content``. Parse only this bounded, explicit error shape; all
        successful JSON output remains byte-for-byte in ``content``.
        """

        direct_error = output.get("error")
        if isinstance(direct_error, Mapping):
            return dict(output), direct_error

        content = output.get(Keys.Field.CONTENT)
        if (
            not isinstance(content, str)
            or not content
            or len(content) > cls._TOOL_ERROR_CONTENT_PARSE_CAP
        ):
            return None
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, Mapping):
            return None
        embedded_error = parsed.get("error")
        if not isinstance(embedded_error, Mapping):
            return None
        return dict(parsed), embedded_error

    @classmethod
    def tool_result_payload(cls, message: object) -> JsonObject:
        """Build a normalised TOOL_RESULT event payload from a tool-result message."""
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
        structured_error = cls._structured_tool_error(output)
        if structured_error is not None:
            output, error = structured_error
            status = Values.Status.FAILED
        # A declared policy answer reaches us wearing a fault's clothes: the Deep
        # Agents backend protocol has one ``error`` channel, so "not available
        # here, do X instead" and "I broke" arrive identically as (text,
        # "error"). Re-establish the distinction before the status is published,
        # or a working policy decision propagates as a run-level failure.
        policy_code = WorkspacePolicyAnswers.code_for(output.get(Keys.Field.CONTENT))
        result: JsonObject = {
            Keys.Field.TOOL_NAME: tool_name,
            Keys.Field.CALL_ID: call_id,
            Keys.Field.STATUS: (
                Values.Status.UNAVAILABLE if policy_code is not None else status
            ),
            Keys.Field.OUTPUT: output or payload,
        }
        if policy_code is not None:
            result["error_code"] = str(policy_code)
            result["safe_message"] = str(output.get(Keys.Field.CONTENT)).strip()
        elif structured_error is not None:
            error_code = StreamTextHelper.extract(error.get("code"))
            safe_message = StreamTextHelper.extract(error.get("safe_message"))
            if error_code is not None:
                result["error_code"] = error_code
            if safe_message is not None:
                result["safe_message"] = safe_message
        # PRD-E3: the v1 ``surface`` / ``surface_uri`` hoist (``_lift_surface_fields``)
        # was retired here — the v1 ``result["surface"]`` appendage no longer exists,
        # so ordinary successful ``tool_result`` payloads carry exactly
        # {tool_name, call_id, status, output}. Typed failures additionally expose
        # bounded error_code/safe_message metadata for truthful presentation.
        # Surface data now flows to clients via the Work Ledger
        # (``surface.created`` / ``view.derived``) + ``payload_ref`` resolution.
        cls.apply_tool_visibility(result)
        return result

    def tool_result_payload_with_state(
        self, run_id: str, payload: JsonObject
    ) -> JsonObject:
        """Enrich a tool-result payload with tool_name and visibility information from accumulated call state."""
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
        """Return the accumulated call state keyed by the payload's call_id, or ``None``."""
        call_id = StreamTextHelper.extract(payload.get(Keys.Field.CALL_ID))
        if call_id is None:
            return None
        return self._tool_call_ids.get((run_id, call_id))

    def _tool_duration_ms(
        self,
        run_id: str,
        payload: Mapping[str, object],
    ) -> int | None:
        """Return elapsed milliseconds since the tool call started, or ``None`` if start time is unknown."""
        state = self.tool_call_state_for_payload(run_id, payload)
        if state is None or state.started_at is None:
            return None
        elapsed = datetime.now(timezone.utc) - state.started_at
        return max(0, round(elapsed.total_seconds() * 1000))

    def connector_slug_for_payload(
        self,
        *,
        run_id: str,
        payload: Mapping[str, object],
    ) -> str | None:
        """Return the MCP connector hosting the call in ``payload``, else ``None``.

        Two registration shapes reach this seam and both must resolve, because
        P2 flips them one deployment at a time:

        * **Legacy gateway** — every MCP call is one ``call_mcp_tool`` dispatch
          whose connector is nested at ``args.server_name``. ``McpDispatcherUnwrap``
          is tried first and answers here, so this method is inert versus the
          previous behaviour for every payload the old code resolved.
        * **Per-tool** — the tool name IS the real MCP tool, there is nothing to
          unwrap, and the unwrap returns ``None``. The connector then comes from
          the run's registration snapshot (:meth:`publish_connector_resolver`),
          a lookup of a name the source itself registered.

        ``None`` means "no connector": a native tool (a step, not an app), an
        unregistered name, or a dispatcher call whose args have not streamed in
        yet. It is never inferred from the tool name's shape — a native
        ``notion_search`` must not acquire MCP identity.
        """

        server_name = McpDispatcherUnwrap.effective_server_name(payload)
        if server_name is not None:
            return server_name
        resolver = self._connector_resolvers.get(run_id)
        if resolver is None:
            return None
        tool_name = StreamTextHelper.extract(payload.get(Keys.Field.TOOL_NAME))
        if tool_name is None:
            return None
        return resolver.server_for(tool_name)

    def add_tool_presentation_fields(
        self,
        *,
        run: RunRecord,
        payload: JsonObject,
        parent_task_id: str | None,
    ) -> None:
        """Attach source-backed optional details to a public tool event.

        MCP provenance is disclosed only when the event resolves to a concrete
        connector — the dispatcher's ``args.server_name`` under the legacy
        gateway, the run's published registration map under per-tool. Neither is
        a guess from the tool name's shape. The frozen access-mode context is
        keyed by server ID, whereas the stream carries a server name; an exact
        key match is the only safe resolution available at this seam. A missing
        or non-matching key remains absent rather than being guessed.
        ``read_act`` is reported strictly as that configured authority mode, not
        as a claim about the side effect of this particular call.
        """

        provenance_payload: Mapping[str, object] = payload
        server_name = self.connector_slug_for_payload(
            run_id=run.run_id,
            payload=provenance_payload,
        )
        if server_name is None:
            state = self.tool_call_state_for_payload(run.run_id, payload)
            if state is not None:
                provenance_payload = {
                    Keys.Field.TOOL_NAME: state.tool_name,
                    Keys.Field.ARGS: state.args
                    or self.parse_args_text(state.args_text),
                }
                server_name = self.connector_slug_for_payload(
                    run_id=run.run_id,
                    payload=provenance_payload,
                )
        if server_name is not None:
            payload[Keys.Field.PROVENANCE] = {
                "source": Values.Provenance.MCP,
                Keys.Field.SERVER_NAME: server_name,
            }
            access_mode = run.runtime_context.connector_access_modes.get(server_name)
            if access_mode is not None:
                access_mode_value = getattr(access_mode, "value", access_mode)
                if isinstance(access_mode_value, str):
                    try:
                        payload[Keys.Field.ACCESS_MODE] = ConnectorAccessMode(
                            access_mode_value
                        ).value
                    except ValueError:
                        # Pydantic validates persisted contexts; retain this
                        # guard for synthetic/replay objects that bypass it.
                        pass
        if parent_task_id is not None:
            payload[Keys.Field.SUBAGENT_TASK_IDS] = [parent_task_id]

    @classmethod
    def apply_tool_visibility(cls, payload: JsonObject) -> None:
        """Stamp ``INTERNAL`` visibility on the payload when the tool name or args path triggers an internal rule."""
        if cls.is_internal_tool_name(
            StreamTextHelper.extract(payload.get(Keys.Field.TOOL_NAME))
        ):
            cls.mark_internal_visibility(payload)
        if cls.is_large_result_artifact_payload(payload):
            cls.mark_internal_visibility(payload)

    @classmethod
    def mark_internal_visibility(cls, payload: JsonObject) -> None:
        """Set the ``visibility`` field to ``INTERNAL`` on ``payload``."""
        payload[Keys.Field.VISIBILITY] = RuntimeEventVisibility.INTERNAL.value

    @classmethod
    def is_internal_tool_name(cls, tool_name: str | None) -> bool:
        """Return ``True`` when ``tool_name`` is in the internal-tools allow-list."""
        return tool_name in cls.internal_tool_names

    @classmethod
    def is_large_result_artifact_state(cls, state: ToolCallStreamState) -> bool:
        """Return ``True`` when the call state represents a large-result artifact tool targeting a virtual path."""
        args = state.args or cls.parse_args_text(state.args_text)
        payload: JsonObject = {
            Keys.Field.TOOL_NAME: state.tool_name,
            Keys.Field.ARGS: args,
        }
        return cls.is_large_result_artifact_payload(payload)

    @classmethod
    def is_large_result_artifact_payload(cls, payload: Mapping[str, object]) -> bool:
        """Return ``True`` when the payload is a large-result artifact tool writing to the virtual path prefix."""
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
        """Return ``True`` when the tool name is in the large-result set or contains ``search``."""
        if tool_name is None:
            return False
        normalized = tool_name.strip().lower()
        return (
            normalized in cls.large_result_artifact_tool_names or "search" in normalized
        )

    @classmethod
    def is_path_classified_artifact_tool_name(cls, tool_name: str | None) -> bool:
        """Return ``True`` when the tool name is in the explicit large-result artifact set (no substring match)."""
        if tool_name is None:
            return False
        return tool_name.strip().lower() in cls.large_result_artifact_tool_names
