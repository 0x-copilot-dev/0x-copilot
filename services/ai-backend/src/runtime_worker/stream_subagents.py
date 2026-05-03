"""Subagent lifecycle projection helpers for runtime stream events."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone

from agent_runtime.execution.contracts import JsonObject, StreamEventSource
from agent_runtime.api.constants import Keys, Messages
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.observability.tracing import TraceContext
from runtime_api.schemas import RunRecord, RuntimeApiEventType
from runtime_worker.stream_messages import StreamMessageParser, StreamTextHelper
from runtime_worker.stream_parts import StreamNamespace


class StreamUpdateProcessor:
    """Process update-type stream events: subagent lifecycle, progress.

    Standalone processor — no inheritance. Uses StreamMessageParser as a utility.
    """

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

    def __init__(self, event_producer: RuntimeEventProducer) -> None:
        self.event_producer = event_producer
        self._subagent_lifecycle_keys: set[tuple[str, RuntimeApiEventType, str]] = set()
        # `(run_id, supervisor_call_id) -> subagent_name`. Populated on SUBAGENT_STARTED.
        self._subagent_name_by_call_id: dict[tuple[str, str], str] = {}
        # `(run_id, subgraph_task_id) -> supervisor_call_id`. Populated on first child
        # tool event seen for a subgraph; the LangGraph subgraph task id is a UUID
        # that differs from the supervisor's `task` tool call_id, so we link them
        # FIFO from `_unlinked_subagent_call_ids`.
        self._subagent_call_id_by_subgraph_id: dict[tuple[str, str], str] = {}
        # FIFO of supervisor call_ids whose subagents have started but whose
        # subgraph task ids are not yet linked. Per-run.
        self._unlinked_subagent_call_ids: dict[str, list[str]] = {}
        # `(run_id, task_id) -> SUBAGENT_STARTED timestamp`. Used to stamp a
        # `duration_ms` field on the matching SUBAGENT_COMPLETED payload so
        # consumers don't have to join started/completed events themselves.
        self._subagent_started_at: dict[tuple[str, str], datetime] = {}

    async def process(
        self,
        *,
        run: RunRecord,
        namespace: StreamNamespace,
        data: object,
        metadata: JsonObject,
    ) -> bool:
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

    def subagent_call_id_for_subgraph(
        self,
        *,
        run_id: str,
        subgraph_task_id: str | None,
    ) -> str | None:
        """Resolve a LangGraph subagent subgraph task id to the supervisor `task` call_id.

        Linking strategy:

        - Once a subgraph is linked, the same supervisor call_id is reused for
          every subsequent event in that subgraph (cached lookup).
        - For the FIRST event in a new subgraph, we link to a queued
          supervisor call_id ONLY when exactly one subagent is currently
          unlinked. With two or more unlinked subagents a naive FIFO pop is
          racy: when the supervisor dispatches a fast subagent (e.g. one that
          calls no internal tools) and a slow research subagent in parallel,
          the slow subagent's first tool message can arrive at the processor
          before the fast subagent's `SUBAGENT_COMPLETED` removes it from the
          queue, and the slow subagent's tools end up wrongly attributed to
          the fast subagent. Returning None here for ambiguous cases makes
          early tool events orphan rather than mis-attributed; once one
          subagent completes (its `_track_subagent_lifecycle` removes it
          from the queue), the remaining subagent's subsequent tools link
          correctly via this cache.
        """

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
        for payload in self.task_tool_call_payloads(data):
            await self.append_task_lifecycle_event(
                run=run,
                event_type=RuntimeApiEventType.SUBAGENT_STARTED,
                payload=payload,
                metadata=metadata,
            )
            emitted = True
        for payload in self.task_tool_result_payloads(data):
            await self.append_task_lifecycle_event(
                run=run,
                event_type=RuntimeApiEventType.SUBAGENT_COMPLETED,
                payload=payload,
                metadata=metadata,
            )
            emitted = True
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

    @classmethod
    def task_tool_call_payload(
        cls,
        *,
        call_id: str,
        args_payload: Mapping[str, object],
    ) -> JsonObject:
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
        text = re.split(
            r"\b(?:Provide|Include|For each claim)\b\s*[:,-]?", text, maxsplit=1
        )[0].strip()
        match = re.search(r"(?<=[.!?])\s+", text)
        if match is None:
            return text
        return text[: match.start()].strip()

    @classmethod
    def actionable_task_summary(cls, text: str) -> str:
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
        if len(text) <= cls.short_summary_max_chars:
            return text
        truncated = text[: cls.short_summary_max_chars - 3].rsplit(" ", 1)[0]
        return f"{truncated or text[: cls.short_summary_max_chars - 3]}..."

    @classmethod
    def task_tool_call_payloads(cls, value: object) -> tuple[JsonObject, ...]:
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
        text = (
            StreamTextHelper.extract(payload.get(Keys.Payload.MESSAGE))
            or StreamTextHelper.extract(payload.get(Keys.Field.SUMMARY))
            or ""
        )
        return text.startswith(Messages.Event.INTERNAL_TODO_PROGRESS_PREFIX)
