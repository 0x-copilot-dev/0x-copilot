"""Queued run command handling."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
import asyncio

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
    StreamEventSource,
)
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness, create_agent_runtime
from agent_runtime.execution.runtime import ainvoke_runtime, astream_runtime
from agent_runtime.settings import RuntimeSettings
from runtime_api.schemas import (
    AgentRunStatus,
    MessageRecord,
    MessageRole,
    RunRecord,
    RuntimeApiEventType,
    RuntimeRunCommand,
)
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.stream_events import RuntimeStreamPartAdapter

RuntimeDependenciesFactory = Callable[[AgentRuntimeContext], RuntimeDependencies]
AgentFactory = Callable[..., RuntimeHarness]
RuntimeInvoker = Callable[[RuntimeHarness, Sequence[object]], object]
RuntimeStreamer = Callable[[RuntimeHarness, Sequence[object]], AsyncIterator[object]]
MAX_STRUCTURED_CONTEXT_CHARS = 4_000


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
        self.dependencies_factory = (
            dependencies_factory or DefaultRuntimeDependenciesFactory(self.settings)
        )
        self.agent_factory = agent_factory
        self.runtime_invoker = runtime_invoker
        self.runtime_streamer = runtime_streamer
        self.event_producer = RuntimeEventProducer(
            persistence=self.persistence,
            event_store=self.event_store,
        )
        self.stream_event_mapper = RuntimeStreamPartAdapter(self.event_producer)
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

        run = self.persistence.update_run_status(
            run_id=command.run_id, status=AgentRunStatus.RUNNING
        )
        self._append_lifecycle(run, RuntimeApiEventType.RUN_STARTED, "Run started")

        try:
            harness = self.agent_factory(
                context=command.runtime_context,
                dependencies=self.dependencies_factory(command.runtime_context),
            )
            messages = self._messages_for_run(command, run)
            if command.runtime_context.model_profile.supports_streaming and (
                self._runtime_streamer_explicit
                or callable(getattr(harness.agent, "astream", None))
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
                        parent_message_id=run.user_message_id,
                        branch_id=self._trace_text(
                            command.runtime_context, "branch_id"
                        ),
                        trace_id=command.trace_id,
                    )
                )
                self._append_lifecycle(
                    run,
                    RuntimeApiEventType.FINAL_RESPONSE,
                    final_text,
                    payload={"message": final_text},
                )
        except TimeoutError as exc:
            failed = self.persistence.update_run_status(
                run_id=command.run_id, status=AgentRunStatus.TIMED_OUT
            )
            self._append_lifecycle(
                failed, RuntimeApiEventType.RUN_FAILED, "Run timed out"
            )
            raise AgentRuntimeError(
                RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
                "Runtime invocation timed out.",
                retryable=True,
                correlation_id=command.trace_id,
            ) from exc
        except Exception:
            failed = self.persistence.update_run_status(
                run_id=command.run_id, status=AgentRunStatus.FAILED
            )
            self._append_lifecycle(failed, RuntimeApiEventType.RUN_FAILED, "Run failed")
            raise

        completed = self.persistence.update_run_status(
            run_id=command.run_id, status=AgentRunStatus.COMPLETED
        )
        self._append_lifecycle(
            completed, RuntimeApiEventType.RUN_COMPLETED, "Run completed"
        )

    def _messages_for_run(
        self, command: RuntimeRunCommand, run: RunRecord
    ) -> tuple[dict[str, str], ...]:
        records = self.persistence.list_messages(
            org_id=command.org_id,
            conversation_id=command.conversation_id,
            limit=200,
        )
        selected = self._selected_message_chain(records, run.user_message_id)
        return tuple(
            {
                "role": message.role.value,
                "content": self._message_content_for_runtime(message),
            }
            for message in selected
            if message.role
            in {MessageRole.USER, MessageRole.ASSISTANT, MessageRole.SYSTEM}
        )

    @classmethod
    def _message_content_for_runtime(cls, message: MessageRecord) -> str:
        if message.role is not MessageRole.USER:
            return message.content_text

        sections = [message.content_text]
        quote = cls._quote_context(message.quote)
        if quote is not None:
            sections.append(f"Quoted context:\n{quote}")
        content_parts = cls._content_parts_context(
            message.content,
            message.content_text,
        )
        if content_parts is not None:
            sections.append(f"Structured content:\n{content_parts}")
        attachments = cls._attachments_context(message.attachments)
        if attachments is not None:
            sections.append(f"Attachments:\n{attachments}")
        branch = cls._branch_context(message)
        if branch is not None:
            sections.append(f"Branch metadata:\n{branch}")
        return "\n\n".join(sections)

    @classmethod
    def _quote_context(cls, quote: Mapping[str, object] | None) -> str | None:
        if not quote:
            return None
        text = cls._text(quote.get("text")) or cls._text(quote.get("message"))
        source = cls._text(quote.get("source")) or cls._text(quote.get("message_id"))
        parts: list[str] = []
        if text is not None:
            parts.append(cls._truncate(text))
        if source is not None:
            parts.append(f"Source: {source}")
        return "\n".join(parts) if parts else None

    @classmethod
    def _content_parts_context(
        cls,
        parts: Sequence[Mapping[str, object]],
        content_text: str,
    ) -> str | None:
        summaries: list[str] = []
        normalized_content = content_text.strip()
        for part in parts:
            part_type = cls._text(part.get("type")) or "part"
            text = cls._content_text(part)
            if part_type == "text":
                if text is not None and text.strip() != normalized_content:
                    summaries.append(cls._truncate(text))
                continue
            summaries.append(cls._part_summary(part_type, part, text))
        return "\n".join(summary for summary in summaries if summary) or None

    @classmethod
    def _attachments_context(
        cls,
        attachments: Sequence[Mapping[str, object]],
    ) -> str | None:
        summaries: list[str] = []
        for attachment in attachments:
            name = (
                cls._text(attachment.get("name"))
                or cls._text(attachment.get("filename"))
                or cls._text(attachment.get("id"))
                or "attachment"
            )
            content_type = cls._text(attachment.get("content_type")) or cls._text(
                attachment.get("mime_type")
            )
            text = cls._content_blocks_text(attachment.get("content"))
            details = cls._details(attachment, content_type=content_type)
            suffix = f" ({details})" if details else ""
            if text is not None:
                summaries.append(f"- {name}{suffix}: {cls._truncate(text)}")
            else:
                summaries.append(f"- {name}{suffix}")
        return "\n".join(summaries) if summaries else None

    @classmethod
    def _branch_context(cls, message: MessageRecord) -> str | None:
        fields = {
            "branch_id": message.branch_id,
            "source_message_id": message.source_message_id,
        }
        branch = message.metadata.get("branch")
        if isinstance(branch, Mapping):
            for key in (
                "regenerate_from_message_id",
                "replace_from_message_id",
            ):
                value = cls._text(branch.get(key))
                if value is not None:
                    fields[key] = value
        regenerate = cls._text(message.metadata.get("regenerate_from_message_id"))
        if regenerate is not None:
            fields["regenerate_from_message_id"] = regenerate
        if any(fields.values()) and message.parent_message_id is not None:
            fields["parent_message_id"] = message.parent_message_id
        lines = [f"- {key}: {value}" for key, value in fields.items() if value]
        return "\n".join(lines) if lines else None

    @classmethod
    def _part_summary(
        cls,
        part_type: str,
        part: Mapping[str, object],
        text: str | None,
    ) -> str:
        name = cls._text(part.get("filename")) or cls._text(part.get("name"))
        details = cls._details(part, content_type=cls._text(part.get("mime_type")))
        title = f"- {part_type}"
        if name is not None:
            title = f"{title} {name}"
        if details:
            title = f"{title} ({details})"
        if text is not None:
            return f"{title}: {cls._truncate(text)}"
        return title

    @classmethod
    def _details(
        cls,
        payload: Mapping[str, object],
        *,
        content_type: str | None,
    ) -> str:
        details: list[str] = []
        if content_type is not None:
            details.append(content_type)
        size = payload.get("size")
        if isinstance(size, int):
            details.append(f"{size} bytes")
        file_id = cls._text(payload.get("file_id"))
        if file_id is not None:
            details.append(f"file_id={file_id}")
        url = cls._text(payload.get("url"))
        if url is not None:
            details.append(f"url={url}")
        return ", ".join(details)

    @classmethod
    def _content_text(cls, payload: Mapping[str, object]) -> str | None:
        return (
            cls._text(payload.get("text"))
            or cls._text(payload.get("content"))
            or cls._content_blocks_text(payload.get("content"))
        )

    @classmethod
    def _content_blocks_text(cls, value: object) -> str | None:
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, Mapping):
            return cls._text(value.get("text")) or cls._text(value.get("content"))
        if not isinstance(value, Sequence) or isinstance(
            value,
            (str, bytes, bytearray),
        ):
            return None
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, Mapping):
                text = cls._content_text(item)
                if text is not None:
                    parts.append(text)
        text = "\n".join(part.strip() for part in parts if part.strip()).strip()
        return text or None

    @classmethod
    def _text(cls, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text or None

    @classmethod
    def _truncate(cls, value: str) -> str:
        if len(value) <= MAX_STRUCTURED_CONTEXT_CHARS:
            return value
        return f"{value[:MAX_STRUCTURED_CONTEXT_CHARS].rstrip()} [truncated]"

    @classmethod
    def _selected_message_chain(
        cls,
        records: Sequence[MessageRecord],
        user_message_id: str,
    ) -> tuple[MessageRecord, ...]:
        run_user = next(
            (message for message in records if message.message_id == user_message_id),
            None,
        )
        if run_user is None:
            return tuple(records)
        by_id = {message.message_id: message for message in records}
        selected_ids: set[str] = set()
        current: MessageRecord | None = run_user
        while current is not None:
            selected_ids.add(current.message_id)
            parent_id = current.parent_message_id
            current = by_id.get(parent_id) if parent_id is not None else None
        if run_user.parent_message_id is None:
            return tuple(
                message
                for message in records
                if message.created_at <= run_user.created_at
            )
        return tuple(
            message for message in records if message.message_id in selected_ids
        )

    async def _stream_runtime(
        self,
        command: RuntimeRunCommand,
        run: RunRecord,
        harness: RuntimeHarness,
        messages: Sequence[object],
    ) -> object:
        final_result: object | None = None
        response_deltas: list[str] = []
        subagent_summaries: list[str] = []
        active_subagent_tasks: set[str] = set()
        completed_subagent_tasks: set[str] = set()
        saw_task_subagent = False
        async with asyncio.timeout(
            command.runtime_context.model_profile.timeout_seconds
        ):
            async for chunk in self.runtime_streamer(harness, messages):
                latest_before = self.event_store.get_latest_sequence(run_id=run.run_id)
                candidate = self.stream_event_mapper.stream_result_candidate(chunk)
                if candidate is not None and not active_subagent_tasks:
                    final_result = candidate
                delta = self.stream_event_mapper.stream_delta(chunk)
                self.stream_event_mapper.append_activity_events(
                    run=run, chunk=chunk, delta=delta
                )
                new_events = self.event_store.list_events_after(
                    org_id=command.org_id,
                    run_id=run.run_id,
                    after_sequence=latest_before,
                )
                for event in new_events:
                    if (
                        event.event_type == RuntimeApiEventType.SUBAGENT_STARTED
                        and event.task_id is not None
                    ):
                        active_subagent_tasks.add(event.task_id)
                        saw_task_subagent = True
                    if (
                        event.event_type == RuntimeApiEventType.SUBAGENT_COMPLETED
                        and event.task_id is not None
                    ):
                        active_subagent_tasks.discard(event.task_id)
                        if event.task_id not in completed_subagent_tasks:
                            completed_subagent_tasks.add(event.task_id)
                            if event.summary:
                                subagent_summaries.append(event.summary)
                if delta is None:
                    continue
                if not active_subagent_tasks:
                    response_deltas.append(delta)
                self._append_lifecycle(
                    run,
                    RuntimeApiEventType.MODEL_DELTA,
                    delta,
                    source=StreamEventSource.MODEL,
                    payload={"delta": delta, "message": delta},
                )
        if final_result is not None:
            return final_result
        if response_deltas:
            return {"content": "".join(response_deltas)}
        if saw_task_subagent and subagent_summaries:
            return {"content": "\n\n".join(subagent_summaries)}
        return None

    def _append_lifecycle(
        self,
        run: RunRecord,
        event_type: RuntimeApiEventType,
        summary: str,
        *,
        source: StreamEventSource = StreamEventSource.SYSTEM,
        payload: dict[str, object] | None = None,
    ) -> None:
        self.event_producer.append_api_event(
            run=run,
            source=source,
            event_type=event_type,
            summary=summary,
            status="completed"
            if event_type == RuntimeApiEventType.FINAL_RESPONSE
            else None,
            payload=payload or {"status": event_type.value},
        )

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
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
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

    @classmethod
    def _trace_text(cls, context: AgentRuntimeContext, key: str) -> str | None:
        value = context.trace_metadata.get(key)
        return value if isinstance(value, str) and value.strip() else None
