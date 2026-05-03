"""Queued run command handling."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
import asyncio
from datetime import datetime, timezone

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
    StreamEventSource,
)
from agent_runtime.api.async_ports import AsyncEventStorePort, AsyncPersistencePort
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import EventStorePort, PersistencePort
from runtime_adapters.async_wrappers import (
    adapt_event_store_to_async,
    adapt_persistence_to_async,
)
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
from runtime_worker.run_metrics import AssistantRunMetrics
from runtime_worker.stream_events import StreamOrchestrator
from runtime_worker.stream_messages import StreamTextHelper
from runtime_worker.streaming_executor import StreamingExecutor
from runtime_worker.tool_observations import (
    PriorToolResultLoader,
    ToolObservationIndex,
    ToolObservationIndexBuilder,
)

RuntimeDependenciesFactory = Callable[[AgentRuntimeContext], RuntimeDependencies]
AgentFactory = Callable[..., RuntimeHarness]
RuntimeInvoker = Callable[[RuntimeHarness, Sequence[object]], object]
RuntimeStreamer = Callable[[RuntimeHarness, Sequence[object]], AsyncIterator[object]]
MAX_STRUCTURED_CONTEXT_CHARS = 4_000


class RuntimeRunHandler:
    """Execute a queued runtime run command asynchronously."""

    action_interrupt_events = frozenset(
        {
            RuntimeApiEventType.APPROVAL_REQUESTED,
            RuntimeApiEventType.MCP_AUTH_REQUIRED,
        }
    )

    class _Fields:
        ROLE = "role"
        CONTENT = "content"
        FINAL_RESPONSE = "final_response"
        RESPONSE = "response"
        OUTPUT = "output"
        MESSAGES = "messages"
        TEXT = "text"
        FILENAME = "filename"
        NAME = "name"
        ID = "id"
        CONTENT_TYPE = "content_type"
        MIME_TYPE = "mime_type"
        SIZE = "size"
        FILE_ID = "file_id"
        URL = "url"
        TYPE = "type"
        ACTION_REQUIRED = "action_required"
        APPROVAL_REQUESTED = "approval_requested"
        INTERRUPTS = "interrupts"
        STATUS = "status"
        DELTA = "delta"
        MESSAGE = "message"
        BRANCH = "branch"
        REGENERATE_FROM_MESSAGE_ID = "regenerate_from_message_id"
        REPLACE_FROM_MESSAGE_ID = "replace_from_message_id"
        BRANCH_ID = "branch_id"
        SOURCE_MESSAGE_ID = "source_message_id"
        PARENT_MESSAGE_ID = "parent_message_id"

    def __init__(
        self,
        *,
        persistence: PersistencePort | AsyncPersistencePort,
        event_store: EventStorePort | AsyncEventStorePort,
        dependencies_factory: RuntimeDependenciesFactory | None = None,
        settings: RuntimeSettings | None = None,
        agent_factory: AgentFactory = create_agent_runtime,
        runtime_invoker: RuntimeInvoker = ainvoke_runtime,
        runtime_streamer: RuntimeStreamer = astream_runtime,
        on_event_appended: Callable[[str], None] | None = None,
    ) -> None:
        self.persistence: AsyncPersistencePort = adapt_persistence_to_async(persistence)
        self.event_store: AsyncEventStorePort = adapt_event_store_to_async(event_store)
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
            on_event_appended=on_event_appended,
        )
        self.stream_event_mapper = StreamOrchestrator(self.event_producer)
        self._runtime_streamer_explicit = runtime_streamer is not astream_runtime

    async def handle(self, command: RuntimeRunCommand) -> None:
        """Run the agent and persist lifecycle events."""

        run = await self.persistence.get_run(
            org_id=command.org_id, run_id=command.run_id
        )
        if run is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Run command references an unknown run.",
                retryable=False,
                correlation_id=command.trace_id,
            )
        if run.conversation_id != command.conversation_id:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Run command conversation_id does not match persisted run.",
                retryable=False,
                correlation_id=command.trace_id,
            )
        if run.user_id != command.user_id:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Run command user_id does not match persisted run.",
                retryable=False,
                correlation_id=command.trace_id,
            )

        run = await self.persistence.update_run_status(
            run_id=command.run_id, status=AgentRunStatus.RUNNING
        )
        await self._append_lifecycle(
            run, RuntimeApiEventType.RUN_STARTED, "Run started"
        )
        metrics = AssistantRunMetrics.from_run(run)

        try:
            tool_observation_index = await self._tool_observation_index(command, run)
            harness = self.agent_factory(
                context=command.runtime_context,
                dependencies=self._dependencies_for_run(
                    command,
                    tool_observation_index,
                ),
            )
            messages = await self._messages_for_run(
                command,
                run,
                tool_observation_index=tool_observation_index,
            )
            if command.runtime_context.model_profile.supports_streaming and (
                self._runtime_streamer_explicit
                or callable(getattr(harness.agent, "astream", None))
            ):
                result = await self._stream_runtime(
                    command,
                    run,
                    harness,
                    messages,
                    metrics,
                )
            else:
                result = await asyncio.wait_for(
                    self.runtime_invoker(
                        harness,
                        messages,
                    ),
                    timeout=command.runtime_context.model_profile.timeout_seconds,
                )
                metrics.record_usage_from(result)
                if await self.stream_event_mapper.append_native_interrupt_events(
                    run=run,
                    value=result,
                ):
                    result = {self._Fields.ACTION_REQUIRED: True}
            if self._is_action_interrupt(result):
                await self.persistence.update_run_status(
                    run_id=command.run_id,
                    status=AgentRunStatus.WAITING_FOR_APPROVAL,
                )
                return
            final_text = self._extract_final_text(result)
            if final_text is not None:
                metrics_payload = metrics.to_payload(
                    completed_at=datetime.now(timezone.utc)
                )
                usage = metrics_payload.get("usage")
                output_tokens = usage.get("output") if isinstance(usage, dict) else None
                await self.persistence.append_message(
                    MessageRecord(
                        conversation_id=command.conversation_id,
                        org_id=command.org_id,
                        run_id=command.run_id,
                        role=MessageRole.ASSISTANT,
                        content_text=final_text,
                        parent_message_id=run.user_message_id,
                        branch_id=self._trace_text(
                            command.runtime_context, self._Fields.BRANCH_ID
                        ),
                        metadata=AssistantRunMetrics.metadata(metrics_payload),
                        token_count=output_tokens
                        if isinstance(output_tokens, int)
                        else None,
                        trace_id=command.trace_id,
                    )
                )
                await self._append_lifecycle(
                    run,
                    RuntimeApiEventType.FINAL_RESPONSE,
                    final_text,
                    payload=AssistantRunMetrics.with_payload(
                        {self._Fields.MESSAGE: final_text},
                        metrics_payload,
                    ),
                    metadata=AssistantRunMetrics.metadata(metrics_payload),
                )
        except TimeoutError:
            failed = await self.persistence.update_run_status(
                run_id=command.run_id, status=AgentRunStatus.TIMED_OUT
            )
            await self._append_lifecycle(
                failed, RuntimeApiEventType.RUN_FAILED, "Run timed out"
            )
            return
        except Exception:
            failed = await self.persistence.update_run_status(
                run_id=command.run_id, status=AgentRunStatus.FAILED
            )
            await self._append_lifecycle(
                failed, RuntimeApiEventType.RUN_FAILED, "Run failed"
            )
            raise

        completed = await self.persistence.update_run_status(
            run_id=command.run_id, status=AgentRunStatus.COMPLETED
        )
        metrics_payload = metrics.to_payload(
            completed_at=completed.completed_at or datetime.now(timezone.utc)
        )
        await self._append_lifecycle(
            completed,
            RuntimeApiEventType.RUN_COMPLETED,
            "Run completed",
            payload=AssistantRunMetrics.with_payload(
                {self._Fields.STATUS: RuntimeApiEventType.RUN_COMPLETED.value},
                metrics_payload,
            ),
            metadata=AssistantRunMetrics.metadata(metrics_payload),
        )

    async def _messages_for_run(
        self,
        command: RuntimeRunCommand,
        run: RunRecord,
        *,
        tool_observation_index: ToolObservationIndex | None = None,
    ) -> tuple[dict[str, str], ...]:
        records = await self.persistence.list_messages(
            org_id=command.org_id,
            conversation_id=command.conversation_id,
            limit=200,
        )
        selected = self._selected_message_chain(records, run.user_message_id)
        messages = [
            {
                self._Fields.ROLE: message.role.value,
                self._Fields.CONTENT: self._message_content_for_runtime(message),
            }
            for message in selected
            if message.role
            in {MessageRole.USER, MessageRole.ASSISTANT, MessageRole.SYSTEM}
        ]
        observations = (
            tool_observation_index
            or await self._tool_observation_index_from_selected(
                command,
                run,
                selected,
            )
        )
        if observations.prompt_context is not None:
            self._insert_prior_tool_context(messages, observations.prompt_context)
        return tuple(messages)

    def _dependencies_for_run(
        self,
        command: RuntimeRunCommand,
        tool_observation_index: ToolObservationIndex,
    ) -> RuntimeDependencies:
        dependencies = self.dependencies_factory(command.runtime_context)
        if not tool_observation_index.has_observations:
            return dependencies
        return dependencies.model_copy(
            update={
                "prior_tool_result_loader": PriorToolResultLoader(
                    tool_observation_index
                )
            }
        )

    async def _tool_observation_index(
        self,
        command: RuntimeRunCommand,
        run: RunRecord,
    ) -> ToolObservationIndex:
        records = await self.persistence.list_messages(
            org_id=command.org_id,
            conversation_id=command.conversation_id,
            limit=200,
        )
        selected = self._selected_message_chain(records, run.user_message_id)
        return await self._tool_observation_index_from_selected(command, run, selected)

    async def _tool_observation_index_from_selected(
        self,
        command: RuntimeRunCommand,
        run: RunRecord,
        selected: Sequence[MessageRecord],
    ) -> ToolObservationIndex:
        return await ToolObservationIndexBuilder(self.event_store).build(
            org_id=command.org_id,
            conversation_id=command.conversation_id,
            current_run_id=run.run_id,
            selected_messages=selected,
        )

    @classmethod
    def _insert_prior_tool_context(
        cls,
        messages: list[dict[str, str]],
        prompt_context: str,
    ) -> None:
        insert_at = len(messages)
        for index in range(len(messages) - 1, -1, -1):
            if messages[index][cls._Fields.ROLE] == MessageRole.USER.value:
                insert_at = index
                break
        messages.insert(
            insert_at,
            {
                cls._Fields.ROLE: MessageRole.SYSTEM.value,
                cls._Fields.CONTENT: prompt_context,
            },
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
        text = StreamTextHelper.extract(
            quote.get(cls._Fields.TEXT)
        ) or StreamTextHelper.extract(quote.get(cls._Fields.MESSAGE))
        source = StreamTextHelper.extract(
            quote.get("source")
        ) or StreamTextHelper.extract(quote.get("message_id"))
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
            part_type = StreamTextHelper.extract(part.get(cls._Fields.TYPE)) or "part"
            text = cls._content_text(part)
            if part_type == cls._Fields.TEXT:
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
                StreamTextHelper.extract(attachment.get(cls._Fields.NAME))
                or StreamTextHelper.extract(attachment.get(cls._Fields.FILENAME))
                or StreamTextHelper.extract(attachment.get(cls._Fields.ID))
                or "attachment"
            )
            content_type = StreamTextHelper.extract(
                attachment.get(cls._Fields.CONTENT_TYPE)
            ) or StreamTextHelper.extract(attachment.get(cls._Fields.MIME_TYPE))
            text = cls._content_blocks_text(attachment.get(cls._Fields.CONTENT))
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
            cls._Fields.BRANCH_ID: message.branch_id,
            cls._Fields.SOURCE_MESSAGE_ID: message.source_message_id,
        }
        branch = message.metadata.get(cls._Fields.BRANCH)
        if isinstance(branch, Mapping):
            for key in (
                cls._Fields.REGENERATE_FROM_MESSAGE_ID,
                cls._Fields.REPLACE_FROM_MESSAGE_ID,
            ):
                value = StreamTextHelper.extract(branch.get(key))
                if value is not None:
                    fields[key] = value
        regenerate = StreamTextHelper.extract(
            message.metadata.get(cls._Fields.REGENERATE_FROM_MESSAGE_ID)
        )
        if regenerate is not None:
            fields[cls._Fields.REGENERATE_FROM_MESSAGE_ID] = regenerate
        if any(fields.values()) and message.parent_message_id is not None:
            fields[cls._Fields.PARENT_MESSAGE_ID] = message.parent_message_id
        lines = [f"- {key}: {value}" for key, value in fields.items() if value]
        return "\n".join(lines) if lines else None

    @classmethod
    def _part_summary(
        cls,
        part_type: str,
        part: Mapping[str, object],
        text: str | None,
    ) -> str:
        name = StreamTextHelper.extract(
            part.get(cls._Fields.FILENAME)
        ) or StreamTextHelper.extract(part.get(cls._Fields.NAME))
        details = cls._details(
            part, content_type=StreamTextHelper.extract(part.get(cls._Fields.MIME_TYPE))
        )
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
        size = payload.get(cls._Fields.SIZE)
        if isinstance(size, int):
            details.append(f"{size} bytes")
        file_id = StreamTextHelper.extract(payload.get(cls._Fields.FILE_ID))
        if file_id is not None:
            details.append(f"file_id={file_id}")
        url = StreamTextHelper.extract(payload.get(cls._Fields.URL))
        if url is not None:
            details.append(f"url={url}")
        return ", ".join(details)

    @classmethod
    def _content_text(cls, payload: Mapping[str, object]) -> str | None:
        return (
            StreamTextHelper.extract(payload.get(cls._Fields.TEXT))
            or StreamTextHelper.extract(payload.get(cls._Fields.CONTENT))
            or cls._content_blocks_text(payload.get(cls._Fields.CONTENT))
        )

    @classmethod
    def _content_blocks_text(cls, value: object) -> str | None:
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, Mapping):
            return StreamTextHelper.extract(
                value.get(cls._Fields.TEXT)
            ) or StreamTextHelper.extract(value.get(cls._Fields.CONTENT))
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
        metrics: AssistantRunMetrics,
    ) -> object:
        async with asyncio.timeout(
            command.runtime_context.model_profile.timeout_seconds
        ):
            result = await StreamingExecutor.run(
                stream=self.runtime_streamer(harness, messages),
                run=run,
                metrics=metrics,
                event_store=self.event_store,
                event_producer=self.event_producer,
                stream_event_mapper=self.stream_event_mapper,
                track_subagents=True,
            )
        return StreamingExecutor.compose_final(result)

    @classmethod
    def _is_action_interrupt(cls, result: object) -> bool:
        interrupts = getattr(result, cls._Fields.INTERRUPTS, None)
        if interrupts:
            return True
        return isinstance(result, Mapping) and (
            result.get(cls._Fields.ACTION_REQUIRED) is True
            or result.get(cls._Fields.APPROVAL_REQUESTED) is True
            or bool(result.get(cls._Fields.INTERRUPTS))
        )

    async def _append_lifecycle(
        self,
        run: RunRecord,
        event_type: RuntimeApiEventType,
        summary: str,
        *,
        source: StreamEventSource = StreamEventSource.SYSTEM,
        payload: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        await self.event_producer.append_api_event(
            run=run,
            source=source,
            event_type=event_type,
            summary=summary,
            status="completed"
            if event_type == RuntimeApiEventType.FINAL_RESPONSE
            else None,
            payload=payload or {self._Fields.STATUS: event_type.value},
            metadata=metadata,
        )

    @classmethod
    def _extract_final_text(cls, result: object) -> str | None:
        """Extract a best-effort assistant response from common LangChain result shapes."""

        if result is None:
            return None
        if isinstance(result, str):
            return result.strip() or None
        if isinstance(result, dict):
            for key in (
                cls._Fields.FINAL_RESPONSE,
                cls._Fields.RESPONSE,
                cls._Fields.OUTPUT,
                cls._Fields.CONTENT,
            ):
                text = StreamTextHelper.extract(result.get(key))
                if text is not None:
                    return text
            messages = result.get(cls._Fields.MESSAGES)
            if isinstance(messages, Sequence):
                for message in reversed(messages):
                    text = cls._message_content(message)
                    if text is not None:
                        return text
        return cls._message_content(result)

    @classmethod
    def _message_content(cls, message: object) -> str | None:
        if isinstance(message, Mapping):
            return cls._content_to_text(message.get(cls._Fields.CONTENT))
        return cls._content_to_text(getattr(message, cls._Fields.CONTENT, None))

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
                    text = item.get(cls._Fields.TEXT) or item.get(cls._Fields.CONTENT)
                    if isinstance(text, str):
                        parts.append(text)
            text = "".join(parts).strip()
            return text or None
        return None

    @classmethod
    def _trace_text(cls, context: AgentRuntimeContext, key: str) -> str | None:
        value = context.trace_metadata.get(key)
        return value if isinstance(value, str) and value.strip() else None
