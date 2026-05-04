"""Queued run command handling."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
import asyncio
import logging
import time
from datetime import datetime, timezone

from agent_runtime.api.presentation_templates import _ErrorMessage
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
    StreamEventSource,
)
from agent_runtime.execution.tool_outcomes import ToolErrorCode, ToolOutcome
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
from agent_runtime.persistence import with_optimistic_retry
from agent_runtime.pricing import CostCalculator, ModelPricingCatalog
from agent_runtime.settings import RuntimeSettings
from runtime_api.schemas import (
    AgentRunStatus,
    MessageRecord,
    MessageRole,
    RunRecord,
    RuntimeApiEventType,
    RuntimeRunCommand,
)
from runtime_worker.audit import WorkerAuditEmitter
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.run_metrics import AssistantRunMetrics
from runtime_worker.stream_events import StreamOrchestrator
from runtime_worker.stream_messages import StreamTextHelper
from runtime_worker.streaming_executor import StreamingExecutor
from agent_runtime.context.memory.subagent_trace import SubagentArtifactsBackend
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
        self.audit_emitter = WorkerAuditEmitter(persistence=self.persistence)
        self.pricing_catalog = ModelPricingCatalog(self.persistence)

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

        run = await with_optimistic_retry(
            lambda: self.persistence.update_run_status(
                run_id=command.run_id, status=AgentRunStatus.RUNNING
            )
        )
        await self._append_lifecycle(
            run, RuntimeApiEventType.RUN_STARTED, "Run started"
        )
        await self.audit_emitter.emit_run_started(run)
        run_start_perf = time.perf_counter()
        metrics = AssistantRunMetrics.from_run(run)
        self.stream_event_mapper.update_processor.bind_metrics(run.run_id, metrics)

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
            await self._append_model_call_started(run, metrics, messages)
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
                await with_optimistic_retry(
                    lambda: self.persistence.update_run_status(
                        run_id=command.run_id,
                        status=AgentRunStatus.WAITING_FOR_APPROVAL,
                    )
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
            await self._reconcile_inflight_tool_calls(
                run,
                outcome=ToolOutcome.TIMED_OUT,
                error_code=ToolErrorCode.TOOL_RUN_TIMEOUT,
            )
            failed = await with_optimistic_retry(
                lambda: self.persistence.update_run_status(
                    run_id=command.run_id, status=AgentRunStatus.TIMED_OUT
                )
            )
            await self._append_lifecycle(
                failed, RuntimeApiEventType.RUN_FAILED, "Run timed out"
            )
            await self.audit_emitter.emit_run_failed(
                failed,
                status=AgentRunStatus.TIMED_OUT,
                error_class="TimeoutError",
                error_code=ToolErrorCode.TOOL_RUN_TIMEOUT.value,
                duration_ms=int((time.perf_counter() - run_start_perf) * 1000),
            )
            await self._record_run_usage(
                failed,
                metrics=metrics,
                completed_at=failed.completed_at or datetime.now(timezone.utc),
                status=AgentRunStatus.TIMED_OUT.value,
            )
            self.stream_event_mapper.message_processor.discard_ledger(run.run_id)
            self.stream_event_mapper.update_processor.discard_metrics(run.run_id)
            return
        except Exception as exc:
            await self._reconcile_inflight_tool_calls(
                run,
                outcome=ToolOutcome.FAILED,
                error_code=ToolErrorCode.TOOL_EXCEPTION,
            )
            failed = await with_optimistic_retry(
                lambda: self.persistence.update_run_status(
                    run_id=command.run_id, status=AgentRunStatus.FAILED
                )
            )
            await self._append_lifecycle(
                failed, RuntimeApiEventType.RUN_FAILED, "Run failed"
            )
            await self.audit_emitter.emit_run_failed(
                failed,
                status=AgentRunStatus.FAILED,
                error_class=type(exc).__name__,
                error_code=ToolErrorCode.TOOL_EXCEPTION.value,
                duration_ms=int((time.perf_counter() - run_start_perf) * 1000),
            )
            await self._record_run_usage(
                failed,
                metrics=metrics,
                completed_at=failed.completed_at or datetime.now(timezone.utc),
                status=AgentRunStatus.FAILED.value,
            )
            self.stream_event_mapper.message_processor.discard_ledger(run.run_id)
            self.stream_event_mapper.update_processor.discard_metrics(run.run_id)
            raise

        completed = await with_optimistic_retry(
            lambda: self.persistence.update_run_status(
                run_id=command.run_id, status=AgentRunStatus.COMPLETED
            )
        )
        self.stream_event_mapper.message_processor.discard_ledger(run.run_id)
        self.stream_event_mapper.update_processor.discard_metrics(run.run_id)
        completed_at = completed.completed_at or datetime.now(timezone.utc)
        metrics_payload = metrics.to_payload(completed_at=completed_at)
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
        await self.audit_emitter.emit_run_completed(
            completed,
            duration_ms=int((time.perf_counter() - run_start_perf) * 1000),
        )
        await self._record_run_usage(
            completed,
            metrics=metrics,
            completed_at=completed_at,
            status=AgentRunStatus.COMPLETED.value,
        )

    async def _record_run_usage(
        self,
        run: RunRecord,
        *,
        metrics: AssistantRunMetrics,
        completed_at: datetime,
        status: str,
    ) -> None:
        """Best-effort write of the per-run usage row + cost stamp (B1, B3).

        The run-completion event is the source of truth; this denormalized
        row is a derived aggregate that powers fast aggregations (B4) and
        budget enforcement (B7). A failure here must never break the run
        lifecycle, so we swallow exceptions and let observability surface
        them as a metric.

        After the row is written we look up pricing-as-of ``completed_at``
        and stamp the cost. Pricing miss → row stays at ``cost_micro_usd
        IS NULL`` (B3 spec: unknown models are null-safe).
        """

        try:
            usage_record = metrics.to_usage_record(
                run, completed_at=completed_at, status=status
            )
            await self.persistence.record_run_usage(usage_record)
        except Exception:
            logging.getLogger(__name__).warning(
                "runtime_run_usage_write_failed",
                extra={"metadata": {"run_id": run.run_id}},
                exc_info=True,
            )
            return
        await self._record_per_call_usage(run, metrics=metrics)
        try:
            pricing = await self.pricing_catalog.lookup(
                provider=run.model_provider,
                model_name=run.model_name,
                region="global",
                at=completed_at,
            )
            if pricing is None:
                return
            cost_micro_usd = CostCalculator.compute(
                input_tokens=usage_record.input_tokens,
                output_tokens=usage_record.output_tokens,
                cached_input_tokens=usage_record.cached_input_tokens,
                pricing=pricing,
            )
            await self.persistence.update_run_usage_cost(
                run_id=run.run_id,
                cost_micro_usd=cost_micro_usd,
                pricing_id=pricing.id,
                pricing_version=pricing.pricing_version,
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "runtime_run_usage_cost_write_failed",
                extra={"metadata": {"run_id": run.run_id}},
                exc_info=True,
            )

    async def _record_per_call_usage(
        self,
        run: RunRecord,
        *,
        metrics: AssistantRunMetrics,
    ) -> None:
        """Best-effort write of per-LLM-call usage rows (B2).

        Reconciliation invariant: ``sum(model_call_usage rows for run_id)``
        equals ``runtime_run_usage`` for that run. The records are built
        from the same accumulator that produced the run-level row, so the
        invariant holds by construction.

        Cost stamping for per-call rows is best-effort: each row gets
        priced against the same pricing snapshot as the run-level row.
        Failures here never break the run lifecycle.
        """

        try:
            records = metrics.model_call_usage_records(run, trace_id=run.trace_id)
            if not records:
                return
            for record in records:
                await self.persistence.record_model_call_usage(record)
        except Exception:
            logging.getLogger(__name__).warning(
                "runtime_model_call_usage_write_failed",
                extra={"metadata": {"run_id": run.run_id}},
                exc_info=True,
            )
            return
        # Cost stamp per call.
        try:
            pricing = await self.pricing_catalog.lookup(
                provider=run.model_provider,
                model_name=run.model_name,
                region="global",
                at=datetime.now(timezone.utc),
            )
            if pricing is None:
                return
            for record in records:
                cost_micro_usd = CostCalculator.compute(
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    cached_input_tokens=record.cached_input_tokens,
                    pricing=pricing,
                )
                await self.persistence.update_model_call_usage_cost(
                    usage_id=record.id,
                    cost_micro_usd=cost_micro_usd,
                    pricing_id=pricing.id,
                    pricing_version=pricing.pricing_version,
                )
        except Exception:
            logging.getLogger(__name__).warning(
                "runtime_model_call_usage_cost_write_failed",
                extra={"metadata": {"run_id": run.run_id}},
                exc_info=True,
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
        update: dict[str, object] = {
            "subagent_artifacts_backend": SubagentArtifactsBackend(
                event_store=self.event_store,
                persistence=self.persistence,
                org_id=command.org_id,
                conversation_id=command.conversation_id,
                current_run_id=command.run_id,
            ),
        }
        if tool_observation_index.has_observations:
            update["prior_tool_result_loader"] = PriorToolResultLoader(
                tool_observation_index
            )
        return dependencies.model_copy(update=update)

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

    async def _reconcile_inflight_tool_calls(
        self,
        run: RunRecord,
        *,
        outcome: ToolOutcome,
        error_code: ToolErrorCode,
    ) -> None:
        """Settle every in-flight tool call before the run terminates.

        On run-level failure paths (asyncio.timeout, unhandled exception),
        any tool call still in `tool_call_started` without a matching
        `tool_result` would leave a "Running" card stuck on the client.
        We synthesize a terminal `tool_result` + `tool_call_completed`
        event for each, in started-order, BEFORE emitting `run_failed`
        so SSE consumers see lifecycle terminate top-down.

        Failures inside this loop are logged but never raised — the caller
        is already on a failure path and reconciliation is best-effort. A
        partial reconciliation is still strictly better than none.
        """

        ledger = self.stream_event_mapper.message_processor.ledger_for_run(run.run_id)
        unsettled = ledger.unsettled()
        if not unsettled:
            return
        _, error_summary = _ErrorMessage.for_code(error_code.value)
        for entry in unsettled:
            try:
                payload: dict[str, object] = {
                    "tool_name": entry.tool_name,
                    "call_id": entry.call_id,
                    "status": outcome.value,
                    "error_code": error_code.value,
                    "error_message": error_summary,
                }
                await self.event_producer.append_api_event(
                    run=run,
                    source=StreamEventSource.SYSTEM,
                    event_type=RuntimeApiEventType.TOOL_RESULT,
                    payload=payload,
                    parent_task_id=entry.parent_task_id,
                    subagent_id=entry.subagent_id,
                )
                await self.event_producer.append_api_event(
                    run=run,
                    source=StreamEventSource.SYSTEM,
                    event_type=RuntimeApiEventType.TOOL_CALL_COMPLETED,
                    payload={
                        "tool_name": entry.tool_name,
                        "call_id": entry.call_id,
                        "status": outcome.value,
                        "error_code": error_code.value,
                    },
                    parent_task_id=entry.parent_task_id,
                    subagent_id=entry.subagent_id,
                )
                ledger.observed_settled(entry.call_id)
            except Exception:
                logging.getLogger(__name__).warning(
                    "tool_call_reconcile.failed run=%s call_id=%s outcome=%s",
                    run.run_id,
                    entry.call_id,
                    outcome.value,
                    exc_info=True,
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

    async def _append_model_call_started(
        self,
        run: RunRecord,
        metrics: AssistantRunMetrics,
        messages: Sequence[Mapping[str, object]],
    ) -> None:
        """Mark the boundary between local prompt build and the LLM call.

        Splits the previously opaque `run_started → first model_delta` gap into
        prompt-build cost (`prompt_build_ms`) versus LangGraph + network + LLM
        TTFT (which is then `t(model_delta) - t(model_call_started)`).
        """

        now = datetime.now(timezone.utc)
        prompt_build_ms = max(
            0, round((now - metrics.started_at).total_seconds() * 1000)
        )
        prompt_chars = sum(
            len(message.get(self._Fields.CONTENT) or "")
            for message in messages
            if isinstance(message.get(self._Fields.CONTENT), str)
        )
        await self._append_lifecycle(
            run,
            RuntimeApiEventType.MODEL_CALL_STARTED,
            "Model call started",
            payload={
                self._Fields.STATUS: (RuntimeApiEventType.MODEL_CALL_STARTED.value),
                "prompt_build_ms": prompt_build_ms,
                "message_count": len(messages),
                "prompt_chars": prompt_chars,
            },
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
