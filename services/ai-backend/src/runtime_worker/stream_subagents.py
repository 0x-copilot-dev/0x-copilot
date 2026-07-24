"""Subagent lifecycle projection helpers for runtime stream events."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from uuid import uuid4

from agent_runtime.execution.contracts import JsonObject, StreamEventSource
from agent_runtime.api.constants import Keys, Messages
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.observability.tracing import TraceContext
from runtime_api.schemas import RunRecord, RuntimeApiEventType
from runtime_worker.run_metrics import AssistantRunMetrics
from runtime_worker.stream_messages import StreamMessageParser, StreamTextHelper
from runtime_worker.stream_parts import StreamNamespace


class StreamUpdateProcessor:
    """Process update-type stream chunks into subagent lifecycle and progress events."""

    short_summary_max_chars = 120

    class _Fields:
        TASK_ID = "task_id"
        SUBAGENT_NAME = "subagent_name"
        SUBAGENT_TYPE = "subagent_type"
        STATUS = "status"
        SUMMARY = "summary"
        DESCRIPTION = "description"
        TASK = "task"
        DISPLAY_TITLE = "display_title"
        MESSAGE = "message"
        SUBAGENT_ID = "subagent_id"
        CALL_ID = "call_id"
        CONTENT = "content"
        # When the supervisor dispatches >1 task tool call in a single tick we wrap
        # them in a fleet so the FE renders a single SubagentFleetCard.
        FLEET_ID = "fleet_id"
        PARENT_FLEET_ID = "parent_fleet_id"
        AGENT_IDS = "agent_ids"
        TITLE = "title"
        ELAPSED = "elapsed"
        # Children's task_ids are carried on FLEET_STARTED so the FE reducer can
        # back-stamp `parent_fleet_id` on `run_subagent` parts emitted before this bookend fired.
        TASK_IDS = "task_ids"

    def __init__(self, event_producer: RuntimeEventProducer) -> None:
        """Initialise all per-run bookkeeping dicts and wire the event producer."""
        self.event_producer = event_producer
        self._subagent_lifecycle_keys: set[tuple[str, RuntimeApiEventType, str]] = set()
        # (run_id, supervisor_call_id) -> subagent_name; populated on SUBAGENT_STARTED.
        self._subagent_name_by_call_id: dict[tuple[str, str], str] = {}
        # (run_id, subgraph_task_id) -> supervisor_call_id; linked via FIFO from _unlinked_subagent_call_ids.
        self._subagent_call_id_by_subgraph_id: dict[tuple[str, str], str] = {}
        # FIFO of supervisor call_ids whose subagents have started but whose
        # subgraph task ids are not yet linked. Per-run.
        self._unlinked_subagent_call_ids: dict[str, list[str]] = {}
        # `(run_id, task_id) -> SUBAGENT_STARTED timestamp`. Used to stamp a
        # `duration_ms` field on the matching SUBAGENT_COMPLETED payload so
        # consumers don't have to join started/completed events themselves.
        self._subagent_started_at: dict[tuple[str, str], datetime] = {}
        # Per-run metrics handle, set by the run handler so subagent token
        # rollup can be attached to SUBAGENT_COMPLETED payloads.
        self._metrics_by_run: dict[str, AssistantRunMetrics] = {}
        # Fleet bookend bookkeeping. `_fleet_id_by_task_id` maps a
        # supervisor `task` call_id back to its fleet so SUBAGENT_COMPLETED
        # can stamp `parent_fleet_id` and decrement the remaining set;
        # `_fleet_remaining` tracks the open task_ids per fleet so the
        # processor can emit SUBAGENT_FLEET_FINISHED when the last child
        # closes; `_fleet_started_at` lets the FINISHED payload carry an
        # `elapsed` string without joining events on the consumer side.
        self._fleet_id_by_task_id: dict[tuple[str, str], str] = {}
        self._fleet_remaining: dict[tuple[str, str], set[str]] = {}
        self._fleet_started_at: dict[tuple[str, str], datetime] = {}

    def bind_metrics(self, run_id: str, metrics: AssistantRunMetrics) -> None:
        """Register the per-run metrics object so SUBAGENT_COMPLETED can rollup."""

        self._metrics_by_run[run_id] = metrics

    def discard_metrics(self, run_id: str) -> None:
        """Remove the metrics handle for ``run_id`` when the run is complete."""
        self._metrics_by_run.pop(run_id, None)

    async def process(
        self,
        *,
        run: RunRecord,
        namespace: StreamNamespace,
        data: object,
        metadata: JsonObject,
    ) -> bool:
        """Dispatch an update-stream payload to lifecycle processing; returns ``True`` if events were emitted."""
        if await self.append_subagent_lifecycle_events(
            run=run,
            namespace=namespace,
            data=data,
            metadata=metadata,
        ):
            return True
        return False

    async def append_task_lifecycle_event(
        self,
        *,
        run: RunRecord,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> None:
        """Persist a deduplicated subagent lifecycle event, enriching COMPLETED with duration and usage."""
        task_id = StreamTextHelper.extract(payload.get(self._Fields.TASK_ID))
        if task_id is not None:
            key = (run.run_id, event_type, task_id)
            if key in self._subagent_lifecycle_keys:
                return
            self._subagent_lifecycle_keys.add(key)
        self._track_subagent_lifecycle(
            run_id=run.run_id,
            event_type=event_type,
            payload=payload,
        )
        if event_type is RuntimeApiEventType.SUBAGENT_COMPLETED and task_id is not None:
            duration_ms = self._subagent_duration_ms(run.run_id, task_id)
            if duration_ms is not None:
                payload["duration_ms"] = duration_ms
            metrics = self._metrics_by_run.get(run.run_id)
            if metrics is not None:
                rollup = metrics.per_call.subagent_rollup(task_id)
                if rollup.call_count > 0:
                    payload["usage"] = rollup.model_dump(mode="json")
        subagent_id = StreamTextHelper.extract(payload.get(self._Fields.SUBAGENT_NAME))
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SUBAGENT,
            event_type=event_type,
            payload=payload,
            metadata=metadata,
            subagent_id=subagent_id,
        )

    def _subagent_duration_ms(self, run_id: str, task_id: str) -> int | None:
        """Pop the started-at timestamp and return the elapsed milliseconds, or ``None`` if not recorded."""
        started_at = self._subagent_started_at.pop((run_id, task_id), None)
        if started_at is None:
            return None
        elapsed = datetime.now(timezone.utc) - started_at
        return max(0, round(elapsed.total_seconds() * 1000))

    def _track_subagent_lifecycle(
        self,
        *,
        run_id: str,
        event_type: RuntimeApiEventType,
        payload: JsonObject,
    ) -> None:
        """Maintain the call_id ↔ subagent_name ↔ subgraph_task_id linkage."""

        call_id = StreamTextHelper.extract(payload.get(self._Fields.TASK_ID))
        if call_id is None:
            return
        if event_type is RuntimeApiEventType.SUBAGENT_STARTED:
            subagent_name = StreamTextHelper.extract(
                payload.get(self._Fields.SUBAGENT_NAME)
            )
            if subagent_name is None:
                return
            self._subagent_name_by_call_id[(run_id, call_id)] = subagent_name
            self._subagent_started_at[(run_id, call_id)] = datetime.now(timezone.utc)
            queue = self._unlinked_subagent_call_ids.setdefault(run_id, [])
            if call_id not in queue:
                queue.append(call_id)
            return
        if event_type is RuntimeApiEventType.SUBAGENT_COMPLETED:
            queue = self._unlinked_subagent_call_ids.get(run_id)
            if queue is not None and call_id in queue:
                queue.remove(call_id)

    def cached_subagent_call_id_for_subgraph(
        self,
        *,
        run_id: str,
        subgraph_task_id: str | None,
    ) -> str | None:
        """Return the cached supervisor call_id for a subgraph task id without mutating the FIFO queue."""
        if subgraph_task_id is None:
            return None
        return self._subagent_call_id_by_subgraph_id.get((run_id, subgraph_task_id))

    def register_supervisor_call_id_for_subgraph(
        self,
        *,
        run_id: str,
        subgraph_task_id: str,
        supervisor_call_id: str,
    ) -> None:
        """Idempotently pin the supervisor call_id for ``subgraph_task_id`` and remove it from the FIFO queue."""
        existing = self._subagent_call_id_by_subgraph_id.get((run_id, subgraph_task_id))
        if existing is not None:
            return
        self._subagent_call_id_by_subgraph_id[(run_id, subgraph_task_id)] = (
            supervisor_call_id
        )
        queue = self._unlinked_subagent_call_ids.get(run_id)
        if queue and supervisor_call_id in queue:
            queue.remove(supervisor_call_id)

    def subagent_call_id_for_subgraph(
        self,
        *,
        run_id: str,
        subgraph_task_id: str | None,
    ) -> str | None:
        """Resolve a subgraph task id to the supervisor call_id via cache lookup then single-unlinked FIFO fallback."""

        if subgraph_task_id is None:
            return None
        existing = self._subagent_call_id_by_subgraph_id.get((run_id, subgraph_task_id))
        if existing is not None:
            return existing
        queue = self._unlinked_subagent_call_ids.get(run_id)
        if not queue:
            return None
        if len(queue) != 1:
            return None
        existing = queue.pop(0)
        self._subagent_call_id_by_subgraph_id[(run_id, subgraph_task_id)] = existing
        return existing

    def subagent_id_for_subgraph(
        self,
        *,
        run_id: str,
        subgraph_task_id: str | None,
    ) -> str | None:
        """Resolve a subgraph task id to the active subagent's `subagent_name`."""

        call_id = self.subagent_call_id_for_subgraph(
            run_id=run_id,
            subgraph_task_id=subgraph_task_id,
        )
        if call_id is None:
            return None
        return self._subagent_name_by_call_id.get((run_id, call_id))

    async def append_subagent_lifecycle_events(
        self,
        *,
        run: RunRecord,
        namespace: StreamNamespace,
        data: object,
        metadata: JsonObject,
    ) -> bool:
        """Append lifecycle events derived from documented Deep Agents update chunks."""

        emitted = False
        start_payloads = self.task_tool_call_payloads(data)
        # PR A2 / WS-E — whenever the supervisor dispatches ≥1 task tool call
        # in the same update tick, emit a SUBAGENT_FLEET_STARTED bookend first
        # and stamp `parent_fleet_id` on each child SUBAGENT_STARTED payload so
        # the FE always renders the subagent(s) inline as one fleet card —
        # including a lone subagent (a "fleet of one"), which previously
        # produced no inline representation at all. The `_fleet_title` helper
        # renders a singular label for the one-agent case.
        fleet_id = await self._maybe_emit_fleet_started(
            run=run,
            payloads=start_payloads,
            metadata=metadata,
        )
        for payload in start_payloads:
            if fleet_id is not None:
                payload[self._Fields.PARENT_FLEET_ID] = fleet_id
                task_id = StreamTextHelper.extract(payload.get(self._Fields.TASK_ID))
                if task_id is not None:
                    self._fleet_id_by_task_id[(run.run_id, task_id)] = fleet_id
                    self._fleet_remaining.setdefault((run.run_id, fleet_id), set()).add(
                        task_id
                    )
            await self.append_task_lifecycle_event(
                run=run,
                event_type=RuntimeApiEventType.SUBAGENT_STARTED,
                payload=payload,
                metadata=metadata,
            )
            emitted = True
        for payload in self.task_tool_result_payloads(data):
            task_id = StreamTextHelper.extract(payload.get(self._Fields.TASK_ID))
            child_fleet_id = (
                self._fleet_id_by_task_id.get((run.run_id, task_id))
                if task_id is not None
                else None
            )
            if child_fleet_id is not None:
                payload[self._Fields.PARENT_FLEET_ID] = child_fleet_id
            await self.append_task_lifecycle_event(
                run=run,
                event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
                payload=payload,
                metadata=metadata,
            )
            emitted = True
            if child_fleet_id is not None and task_id is not None:
                await self._maybe_emit_fleet_finished(
                    run=run,
                    fleet_id=child_fleet_id,
                    task_id=task_id,
                    metadata=metadata,
                )
        if emitted or not namespace.is_subagent:
            return emitted

        payload = StreamMessageParser.safe_activity_payload(data)
        if not self.has_user_visible_progress(payload):
            return True
        payload.setdefault(self._Fields.TASK_ID, namespace.subagent_task_id)
        payload.setdefault(self._Fields.STATUS, "running")
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SUBAGENT,
            event_type=RuntimeApiEventType.SUBAGENT_PROGRESS,
            payload=payload,
            metadata=metadata,
            parent_task_id=namespace.subagent_task_id,
        )
        return True

    async def _maybe_emit_fleet_started(
        self,
        *,
        run: RunRecord,
        payloads: tuple[JsonObject, ...],
        metadata: JsonObject,
    ) -> str | None:
        """Emit SUBAGENT_FLEET_STARTED when this tick dispatches ≥1 subagent.

        A lone subagent is wrapped as a fleet-of-one so it still renders
        inline; only an empty tick (no task tool calls) skips the bookend.
        """

        if not payloads:
            return None
        agent_ids: list[str] = []
        task_ids: list[str] = []
        for payload in payloads:
            name = StreamTextHelper.extract(payload.get(self._Fields.SUBAGENT_NAME))
            if name is not None:
                agent_ids.append(name)
            task_id = StreamTextHelper.extract(payload.get(self._Fields.TASK_ID))
            if task_id is not None:
                task_ids.append(task_id)
        fleet_id = uuid4().hex
        title = self._fleet_title(agent_ids)
        fleet_payload: JsonObject = {
            self._Fields.FLEET_ID: fleet_id,
            self._Fields.TITLE: title,
            self._Fields.AGENT_IDS: tuple(agent_ids),
            # Include the explicit child set so the FE can back-stamp
            # `parent_fleet_id` on `run_subagent` parts emitted by the
            # per-tool streaming path before this fleet bookend fires.
            self._Fields.TASK_IDS: tuple(task_ids),
        }
        self._fleet_started_at[(run.run_id, fleet_id)] = datetime.now(timezone.utc)
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SUBAGENT,
            event_type=RuntimeApiEventType.SUBAGENT_FLEET_STARTED,
            payload=fleet_payload,
            metadata=metadata,
        )
        return fleet_id

    async def _maybe_emit_fleet_finished(
        self,
        *,
        run: RunRecord,
        fleet_id: str,
        task_id: str,
        metadata: JsonObject,
    ) -> None:
        """Decrement the fleet's open-child set; emit FINISHED when empty."""

        key = (run.run_id, fleet_id)
        remaining = self._fleet_remaining.get(key)
        if remaining is None:
            return
        remaining.discard(task_id)
        self._fleet_id_by_task_id.pop((run.run_id, task_id), None)
        if remaining:
            return
        self._fleet_remaining.pop(key, None)
        elapsed_str: str | None = None
        started_at = self._fleet_started_at.pop(key, None)
        if started_at is not None:
            elapsed_seconds = max(
                0,
                round((datetime.now(timezone.utc) - started_at).total_seconds()),
            )
            elapsed_str = self._format_elapsed(elapsed_seconds)
        finished_payload: JsonObject = {self._Fields.FLEET_ID: fleet_id}
        if elapsed_str is not None:
            finished_payload[self._Fields.ELAPSED] = elapsed_str
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SUBAGENT,
            event_type=RuntimeApiEventType.SUBAGENT_FLEET_FINISHED,
            payload=finished_payload,
            metadata=metadata,
        )

    @staticmethod
    def _fleet_title(agent_ids: list[str]) -> str:
        """Generate a human-readable fleet title from the list of subagent names."""
        if not agent_ids:
            return "Subagents working in parallel"
        if len(agent_ids) == 1:
            return f"{agent_ids[0]} working"
        return f"{len(agent_ids)} subagents working in parallel"

    @staticmethod
    def _format_elapsed(seconds: int) -> str:
        """Format an elapsed duration in seconds as ``M:SS`` or ``H:MM:SS``."""
        if seconds < 60:
            return f"0:{seconds:02d}"
        minutes, secs = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}:{secs:02d}"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"

    @classmethod
    def task_tool_call_payload(
        cls,
        *,
        call_id: str,
        args_payload: Mapping[str, object],
    ) -> JsonObject:
        """Build a ``SUBAGENT_STARTED`` event payload from a task tool-call id and its argument mapping."""
        subagent_name = (
            StreamTextHelper.extract(args_payload.get(cls._Fields.SUBAGENT_TYPE))
            or StreamTextHelper.extract(args_payload.get(cls._Fields.SUBAGENT_NAME))
            or "subagent"
        )
        summary = StreamTextHelper.extract(
            args_payload.get(cls._Fields.DESCRIPTION)
        ) or StreamTextHelper.extract(args_payload.get(cls._Fields.TASK))
        short_summary = cls.short_task_summary(summary)
        event_payload: JsonObject = {
            cls._Fields.TASK_ID: call_id,
            cls._Fields.SUBAGENT_NAME: subagent_name,
            cls._Fields.STATUS: "queued",
        }
        if summary is not None:
            event_payload[cls._Fields.SUMMARY] = summary
        if short_summary is not None:
            event_payload[Keys.Field.SHORT_SUMMARY] = short_summary
            event_payload[Keys.Field.DISPLAY_TITLE] = short_summary
        return event_payload

    @classmethod
    def task_tool_result_payload(
        cls,
        payload: Mapping[str, object],
        *,
        subagent_name: str | None = None,
        short_summary: str | None = None,
    ) -> JsonObject:
        """Build a ``SUBAGENT_COMPLETED`` event payload from a task tool-result message payload."""
        call_id = (
            StreamTextHelper.extract(payload.get(cls._Fields.CALL_ID))
            or TraceContext.event_id()
        )
        output = payload.get("output")
        output_payload = output if isinstance(output, Mapping) else {}
        summary = (
            StreamMessageParser.content_delta_to_text(
                output_payload.get(cls._Fields.CONTENT)
            )
            or StreamTextHelper.extract(output_payload.get(cls._Fields.MESSAGE))
            or StreamMessageParser.content_delta_to_text(
                payload.get(cls._Fields.CONTENT)
            )
            or StreamTextHelper.extract(payload.get(cls._Fields.MESSAGE))
        )
        event_payload: JsonObject = {
            cls._Fields.TASK_ID: call_id,
            cls._Fields.SUBAGENT_NAME: subagent_name or "subagent",
            cls._Fields.STATUS: "completed",
        }
        if summary is not None:
            event_payload[cls._Fields.SUMMARY] = summary
        if short_summary is not None:
            event_payload[Keys.Field.SHORT_SUMMARY] = short_summary
            event_payload[Keys.Field.DISPLAY_TITLE] = short_summary
        return event_payload

    @classmethod
    def short_task_summary(cls, summary: str | None) -> str | None:
        """Normalise, trim to the first sentence, apply action verb transforms, and truncate a task summary."""
        if summary is None:
            return None
        text = " ".join(summary.strip().split())
        if not text:
            return None
        text = cls.first_task_sentence(text)
        text = cls.actionable_task_summary(text)
        return cls.truncate_task_summary(text)

    @classmethod
    def first_task_sentence(cls, text: str) -> str:
        """Return only the first sentence fragment before trailing instructions or a sentence boundary."""
        text = re.split(
            r"\b(?:Provide|Include|For each claim)\b\s*[:,-]?", text, maxsplit=1
        )[0].strip()
        match = re.search(r"(?<=[.!?])\s+", text)
        if match is None:
            return text
        return text[: match.start()].strip()

    @classmethod
    def actionable_task_summary(cls, text: str) -> str:
        """Rewrite leading imperative verbs to gerund phrases and capitalise the result."""
        replacements = (
            (r"^create\s+(?:a|an|the)?\s*", "Preparing a "),
            (r"^write\s+(?:a|an|the)?\s*", "Writing a "),
            (r"^draft\s+(?:a|an|the)?\s*", "Drafting a "),
            (r"^research\s+", "Researching "),
            (r"^investigate\s+", "Investigating "),
            (r"^analyze\s+", "Analyzing "),
            (r"^review\s+", "Reviewing "),
            (r"^summarize\s+", "Summarizing "),
            (r"^find\s+", "Searching for "),
            (r"^search\s+", "Searching "),
            (r"^implement\s+", "Working on "),
            (r"^build\s+", "Working on "),
        )
        for pattern, replacement in replacements:
            updated = re.sub(pattern, replacement, text, count=1, flags=re.IGNORECASE)
            if updated != text:
                return updated[:1].upper() + updated[1:]
        return text[:1].upper() + text[1:]

    @classmethod
    def truncate_task_summary(cls, text: str) -> str:
        """Truncate ``text`` to ``short_summary_max_chars`` on a word boundary, appending ``...``."""
        if len(text) <= cls.short_summary_max_chars:
            return text
        truncated = text[: cls.short_summary_max_chars - 3].rsplit(" ", 1)[0]
        return f"{truncated or text[: cls.short_summary_max_chars - 3]}..."

    @classmethod
    def task_tool_call_payloads(cls, value: object) -> tuple[JsonObject, ...]:
        """Extract SUBAGENT_STARTED payloads from all ``task`` tool calls in an update-stream value."""
        payloads: list[JsonObject] = []
        for message in StreamMessageParser.update_messages(value):
            for tool_call in StreamMessageParser.tool_call_chunks(message):
                payload = StreamMessageParser.payload_mapping(tool_call)
                tool_name = StreamTextHelper.extract(
                    payload.get("name")
                ) or StreamTextHelper.extract(payload.get("tool_name"))
                if tool_name != "task":
                    continue
                call_id = StreamTextHelper.extract(
                    payload.get("id")
                ) or StreamTextHelper.extract(payload.get("call_id"))
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
        """Extract SUBAGENT_COMPLETED payloads from all ``task`` tool-result messages in an update-stream value."""
        payloads: list[JsonObject] = []
        for message in StreamMessageParser.update_messages(value):
            if not StreamMessageParser.is_tool_result_message(message):
                continue
            payload = StreamMessageParser.payload_mapping(message)
            tool_name = StreamTextHelper.extract(
                payload.get("name")
            ) or StreamTextHelper.extract(payload.get("tool_name"))
            if tool_name != "task":
                continue
            call_id = (
                StreamTextHelper.extract(payload.get("tool_call_id"))
                or StreamTextHelper.extract(payload.get("id"))
                or StreamTextHelper.extract(payload.get("call_id"))
            )
            if call_id is None:
                continue
            payloads.append(
                cls.task_tool_result_payload({"call_id": call_id, **payload})
            )
        return tuple(payloads)

    @staticmethod
    def has_user_visible_progress(payload: Mapping[str, object]) -> bool:
        """Return ``True`` when the payload contains a non-empty visible text field and is not an internal marker."""
        if StreamUpdateProcessor.is_internal_progress_text(payload):
            return False
        return any(
            isinstance(payload.get(key), str) and str(payload[key]).strip()
            for key in (
                "message",
                "summary",
                "display_title",
                "subagent_name",
                "subagent_id",
            )
        )

    @staticmethod
    def is_internal_progress_text(payload: Mapping[str, object]) -> bool:
        """Return ``True`` when the payload's message or summary starts with the internal progress prefix."""
        text = (
            StreamTextHelper.extract(payload.get(Keys.Payload.MESSAGE))
            or StreamTextHelper.extract(payload.get(Keys.Field.SUMMARY))
            or ""
        )
        return text.startswith(Messages.Event.INTERNAL_TODO_PROGRESS_PREFIX)
