"""Subagent lifecycle projection helpers for runtime stream events."""

from __future__ import annotations

import re
from collections.abc import Mapping

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
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SUBAGENT,
            event_type=event_type,
            payload=payload,
            metadata=metadata,
        )

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
