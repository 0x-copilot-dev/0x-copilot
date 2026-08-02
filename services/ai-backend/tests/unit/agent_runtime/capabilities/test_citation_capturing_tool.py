"""Unit tests for the local-tool citation capture wrapper."""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.capabilities.citation_capturing_tool import (
    CitationCapturingRegistry,
    CitationCapturingTool,
    CitationHint,
)
from agent_runtime.capabilities.citations import CitationLedger
from agent_runtime.capabilities.conversation_ordinals import (
    ConversationOrdinalAllocator,
)
from agent_runtime.capabilities.mcp.middleware.compose import ToolSchemaIdentity
from agent_runtime.capabilities.retrying_tool import RetryingTool
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
from runtime_api.schemas import (
    RunRecord,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
)


class _RecordingPersistence:
    async def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> None:
        del run_id, latest_sequence_no


class _RecordingEventStore:
    def __init__(self) -> None:
        self.drafts: list[RuntimeEventDraft] = []

    async def append_event(self, event: RuntimeEventDraft) -> RuntimeEventEnvelope:
        self.drafts.append(event)
        return RuntimeEventEnvelope(
            run_id=event.run_id,
            conversation_id=event.conversation_id,
            sequence_no=len(self.drafts),
            source=event.source,
            event_type=event.event_type,
            trace_id=event.trace_id,
            parent_event_id=event.parent_event_id,
            span_id=event.span_id,
            parent_span_id=event.parent_span_id,
            parent_task_id=event.parent_task_id,
            task_id=event.task_id,
            subagent_id=event.subagent_id,
            display_title=event.display_title,
            summary=event.summary,
            status=event.status,
            activity_kind=event.activity_kind
            or RuntimeEventPresentationProjector.activity_kind_for(
                event_type=event.event_type,
                source=event.source,
            ),
            visibility=event.visibility,
            redaction_state=event.redaction_state,
            presentation=event.presentation,
            payload=event.payload,
            metadata=event.metadata,
        )


def _run_record() -> RunRecord:
    return RunRecord(
        run_id="run_capture",
        conversation_id="conv_capture",
        org_id="org_capture",
        user_id="user_capture",
        user_message_id="msg_capture",
        trace_id="trace_capture",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=AgentRuntimeContext(
            user_id="user_capture",
            org_id="org_capture",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128_000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
            run_id="run_capture",
            trace_id="trace_capture",
        ),
    )


class _StubWebSearchTool(BaseTool):
    """Mimics ``DuckDuckGoSearchResults(output_format="list")``."""

    name: str = "web_search"
    description: str = "Stub search tool that returns a list of result dicts."
    payload: list[dict[str, str]] = []

    def _run(self, *_args: Any, **_kwargs: Any) -> Any:
        return self.payload

    async def _arun(self, *_args: Any, **_kwargs: Any) -> Any:
        return self.payload


class _ContentAndArtifactSearchTool(BaseTool):
    """Mimics the built-in ``web_search``: declares ``content_and_artifact``.

    Both halves are lists of dicts, which is what makes this the interesting
    case — the content half carries no string for the citation hint to extend.
    """

    name: str = "web_search"
    description: str = "Stub search tool that declares content_and_artifact."
    response_format: str = "content_and_artifact"
    return_direct: bool = True
    tags: list[str] | None = ["builtin"]
    metadata: dict[str, Any] | None = {"origin": "builtin"}
    extras: dict[str, Any] | None = {"cache_control": {"type": "ephemeral"}}
    results: list[dict[str, str]] = []
    raw_results: list[dict[str, str]] = []

    def _run(self, *_args: Any, **_kwargs: Any) -> tuple[Any, Any]:
        return self.results, self.raw_results

    async def _arun(self, *_args: Any, **_kwargs: Any) -> tuple[Any, Any]:
        return self.results, self.raw_results


class _StubRegistry:
    def __init__(self, tools: tuple[BaseTool, ...]) -> None:
        self._tools = tools

    def list_available_tools(self, _context: object) -> tuple[object, ...]:
        return tuple(self._tools)


class CitationCapturingFixtureMixin:
    def _bind_ledger(
        self,
    ) -> tuple[CitationLedger, _RecordingEventStore, InMemoryCitationStore, object]:
        store = InMemoryCitationStore()
        events = _RecordingEventStore()
        producer = RuntimeEventProducer(
            persistence=_RecordingPersistence(),
            event_store=events,
        )
        ledger = CitationLedger(
            run=_run_record(),
            store=store,
            producer=producer,
            source=StreamEventSource.TOOL,
        )
        token = CitationLedger.bind_for_run(ledger)
        return ledger, events, store, token


class TestCitationCapturingTool(CitationCapturingFixtureMixin):
    def test_duckduckgo_list_shape_registers_each_result(self) -> None:
        _, events, store, token = self._bind_ledger()
        try:
            inner = _StubWebSearchTool(
                payload=[
                    {
                        "title": "DeepAgents on PyPI",
                        "link": "https://pypi.org/project/deepagents",
                        "snippet": "An agent harness built on langchain.",
                    },
                    {
                        "title": "LangChain blog",
                        "link": "https://blog.langchain.dev/deep-agents",
                        "snippet": "Introducing Deep Agents.",
                    },
                ],
            )
            wrapped = CitationCapturingTool(
                name=inner.name,
                description=inner.description,
                args_schema=inner.args_schema,
                inner=inner,
            )
            asyncio.run(wrapped._arun())
        finally:
            CitationLedger.unbind(token)

        assert len(store.rows) == 2
        assert [row.source_url for row in store.rows] == [
            "https://pypi.org/project/deepagents",
            "https://blog.langchain.dev/deep-agents",
        ]
        assert [row.source_connector for row in store.rows] == [
            "web_search",
            "web_search",
        ]
        # Projector batches: one sources_ingested event carries both rows.
        assert len(events.drafts) == 1
        assert events.drafts[0].event_type.value == "sources_ingested"

    def test_duplicate_url_within_one_call_dedupes_to_one_event(self) -> None:
        _, events, store, token = self._bind_ledger()
        try:
            inner = _StubWebSearchTool(
                payload=[
                    {
                        "title": "Same page",
                        "link": "https://example.com/x",
                        "snippet": "First mention.",
                    },
                    {
                        "title": "Same page (dup)",
                        "link": "https://example.com/x",
                        "snippet": "Second mention.",
                    },
                ],
            )
            wrapped = CitationCapturingTool(
                name=inner.name,
                description=inner.description,
                args_schema=inner.args_schema,
                inner=inner,
            )
            asyncio.run(wrapped._arun())
        finally:
            CitationLedger.unbind(token)

        assert len(store.rows) == 1
        assert len(events.drafts) == 1

    def test_passes_through_inner_result_unchanged(self) -> None:
        _, _, _, token = self._bind_ledger()
        try:
            inner = _StubWebSearchTool(
                payload=[{"title": "T", "link": "https://example.com/y"}],
            )
            wrapped = CitationCapturingTool(
                name=inner.name,
                description=inner.description,
                args_schema=inner.args_schema,
                inner=inner,
            )
            result = asyncio.run(wrapped._arun())
        finally:
            CitationLedger.unbind(token)

        assert result == [{"title": "T", "link": "https://example.com/y"}]

    def test_no_op_when_no_ledger_bound(self) -> None:
        inner = _StubWebSearchTool(
            payload=[{"title": "T", "link": "https://example.com/z"}],
        )
        wrapped = CitationCapturingTool(
            name=inner.name,
            description=inner.description,
            args_schema=inner.args_schema,
            inner=inner,
        )
        result = asyncio.run(wrapped._arun())
        # No ledger bound → no exception, original result returned unchanged.
        assert result == [{"title": "T", "link": "https://example.com/z"}]

    def test_unrecognized_result_shape_passes_through_silently(self) -> None:
        _, events, store, token = self._bind_ledger()
        try:
            inner = _StubWebSearchTool(payload=[])
            inner.payload = "raw text without URLs"  # type: ignore[assignment]
            wrapped = CitationCapturingTool(
                name=inner.name,
                description=inner.description,
                args_schema=inner.args_schema,
                inner=inner,
            )
            asyncio.run(wrapped._arun())
        finally:
            CitationLedger.unbind(token)

        assert store.rows == ()
        assert events.drafts == []


class TestCitationCapturingRegistry(CitationCapturingFixtureMixin):
    def test_wraps_basetools_passthrough_for_non_basetools(self) -> None:
        sentinel = object()
        inner = _StubWebSearchTool(payload=[])
        registry = CitationCapturingRegistry(
            inner=_StubRegistry(tools=(inner, sentinel))  # type: ignore[arg-type]
        )
        rendered = registry.list_available_tools(context=None)
        assert isinstance(rendered[0], CitationCapturingTool)
        assert rendered[1] is sentinel

    def test_wrap_is_idempotent(self) -> None:
        inner = _StubWebSearchTool(payload=[])
        once = CitationCapturingRegistry(inner=_StubRegistry(tools=(inner,)))
        twice = CitationCapturingRegistry(inner=once)
        rendered = twice.list_available_tools(context=None)
        # Wrapping a wrapped tool should not double-wrap.
        assert isinstance(rendered[0], CitationCapturingTool)
        assert isinstance(rendered[0].inner, _StubWebSearchTool)


class ContentAndArtifactFixtureMixin:
    """Builders and constants for the ``content_and_artifact`` regression."""

    RESULTS: list[dict[str, str]] = [
        {
            "title": "Deep Agents",
            "link": "https://example.test/a",
            "snippet": "Model-visible summary.",
        }
    ]
    RAW_RESULTS: list[dict[str, str]] = [
        {
            "href": "https://example.test/a",
            "body": "Model-visible summary.",
            "extra": "artifact-only-field",
        }
    ]
    TOOL_CALL_ID = "call_content_and_artifact"

    def _content_and_artifact_inner(self) -> _ContentAndArtifactSearchTool:
        return _ContentAndArtifactSearchTool(
            results=self.RESULTS,
            raw_results=self.RAW_RESULTS,
        )

    def _wrap_through_registry(self, tool: BaseTool) -> BaseTool:
        registry = CitationCapturingRegistry(inner=_StubRegistry(tools=(tool,)))
        return registry.list_available_tools(context=None)[0]  # type: ignore[return-value]

    def _bind_allocator(self) -> tuple[ConversationOrdinalAllocator, object]:
        allocator = ConversationOrdinalAllocator(
            org_id="org_capture",
            conversation_id="conv_capture",
            run_id="run_capture",
        )
        return allocator, ConversationOrdinalAllocator.bind_for_run(allocator)

    def _tool_call(self) -> dict[str, Any]:
        return {
            "name": "web_search",
            "args": {},
            "id": self.TOOL_CALL_ID,
            "type": "tool_call",
        }


class TestCitationHintWithinResponseFormat(ContentAndArtifactFixtureMixin):
    def test_content_and_artifact_pair_stays_a_pair(self) -> None:
        # ToolResultNote finds no string to extend in (list, list) and would
        # insert the note as a third element; BaseTool.arun then refuses to
        # unpack the result at all.
        annotated = CitationHint.append_within_response_format(
            (self.RESULTS, self.RAW_RESULTS),
            ordinal=1,
            tool_name="web_search",
            response_format="content_and_artifact",
        )

        assert isinstance(annotated, tuple)
        assert len(annotated) == 2
        assert annotated[1] == self.RAW_RESULTS

    def test_hint_lands_on_the_content_half(self) -> None:
        annotated = CitationHint.append_within_response_format(
            ("readable text", self.RAW_RESULTS),
            ordinal=2,
            tool_name="web_search",
            response_format="content_and_artifact",
        )

        assert annotated == (
            "readable text\n\n"
            + CitationHint.render(ordinal=2, tool_name="web_search"),
            self.RAW_RESULTS,
        )

    def test_plain_content_format_keeps_the_original_shape_walk(self) -> None:
        annotated = CitationHint.append_within_response_format(
            ("readable text", self.RAW_RESULTS),
            ordinal=3,
            tool_name="web_search",
            response_format="content",
        )

        assert annotated == CitationHint.append_to(
            ("readable text", self.RAW_RESULTS), ordinal=3, tool_name="web_search"
        )

    def test_declared_pair_that_is_not_a_pair_falls_back_unchanged(self) -> None:
        # The inner is already breaking its own contract; this method must not
        # invent behaviour for it.
        annotated = CitationHint.append_within_response_format(
            "not a pair",
            ordinal=4,
            tool_name="web_search",
            response_format="content_and_artifact",
        )

        assert annotated == CitationHint.append_to(
            "not a pair", ordinal=4, tool_name="web_search"
        )


class TestContentAndArtifactSurvivesBothWrappers(
    CitationCapturingFixtureMixin, ContentAndArtifactFixtureMixin
):
    def test_registry_propagates_the_dispatch_surface(self) -> None:
        inner = self._content_and_artifact_inner()

        wrapped = self._wrap_through_registry(inner)

        for field in ToolSchemaIdentity.DISPATCH_SURFACE:
            assert getattr(wrapped, field) == getattr(inner, field), field
        assert wrapped.extras == inner.extras

    def test_registry_still_augments_args_schema_with_tool_call_id(self) -> None:
        # args_schema is the one field the citation wrapper must NOT propagate
        # verbatim — the injected tool_call_id is what binds a result to its call.
        wrapped = self._wrap_through_registry(self._content_and_artifact_inner())

        assert "tool_call_id" in wrapped.args_schema.model_fields  # type: ignore[union-attr]

    def test_artifact_survives_retry_and_citation_wrappers(self) -> None:
        _, _, store, ledger_token = self._bind_ledger()
        _, allocator_token = self._bind_allocator()
        try:
            inner = self._content_and_artifact_inner()
            retried = RetryingTool.wrapping(
                inner,
                max_attempts=3,
                initial_backoff_seconds=0.0,
                max_backoff_seconds=0.0,
            )
            tool = self._wrap_through_registry(retried)
            message = asyncio.run(tool.ainvoke(self._tool_call()))
        finally:
            ConversationOrdinalAllocator.unbind(allocator_token)
            CitationLedger.unbind(ledger_token)

        assert isinstance(message, ToolMessage)
        # The artifact reaches the ToolMessage instead of being stringified
        # into the model-visible content alongside it.
        assert message.artifact == self.RAW_RESULTS
        assert "artifact-only-field" not in message.content
        # The citation hint still reaches the model, on the content half only.
        assert CitationHint.NOTE_PREFIX in message.content
        # And the ledger still saw the sources.
        assert [row.source_url for row in store.rows] == ["https://example.test/a"]
