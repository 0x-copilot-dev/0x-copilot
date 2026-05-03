from __future__ import annotations

import asyncio

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.presentation import PresentationGenerator
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentation,
    RuntimeEventPresentationProjector,
)


class RecordingPersistence:
    def __init__(self) -> None:
        self.latest_sequence_no: int | None = None

    async def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> None:
        self.latest_sequence_no = latest_sequence_no


class RecordingEventStore:
    def __init__(self) -> None:
        self.drafts: list[RuntimeEventDraft] = []

    @property
    def draft(self) -> RuntimeEventDraft | None:
        """Return the first appended draft (the original event, not patches)."""
        return self.drafts[0] if self.drafts else None

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


def run_record() -> RunRecord:
    return RunRecord(
        run_id="run_123",
        conversation_id="conversation_123",
        org_id="org_123",
        user_id="user_123",
        user_message_id="message_123",
        trace_id="trace_123",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=AgentRuntimeContext(
            user_id="user_123",
            org_id="org_123",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
            run_id="run_123",
            trace_id="trace_123",
        ),
    )


def test_runtime_event_presentation_sanitizes_text() -> None:
    presentation = RuntimeEventPresentation.model_validate(
        {
            "title": "  <b>Searched docs</b>  ",
            "summary": "  Found   useful sources. ",
            "status_label": "Done",
            "kind": "result",
        }
    )

    assert presentation.title == "bSearched docs/b"
    assert presentation.summary == "Found useful sources."


async def test_presentation_generator_uses_valid_llm_json() -> None:
    generator = PresentationGenerator(
        presenter=lambda _: {
            "title": "Searched the web",
            "summary": "Found official Slack MCP setup sources.",
            "status_label": "Done",
            "kind": "result",
            "result_preview": [
                {
                    "title": "Slack Developer Docs",
                    "subtitle": "Official MCP overview",
                    "url": "https://docs.slack.dev/ai/slack-mcp-server/",
                    "badge": "Official",
                }
            ],
            "debug_label": "Tool details",
        }
    )

    presentation = await generator.presentation_for_event(
        run=run_record(),
        event_type=RuntimeApiEventType.TOOL_RESULT,
        source=StreamEventSource.RUNTIME,
        payload={
            "tool_name": "web_search",
            "call_id": "call_123",
            "status": "completed",
            "output": {"results": ["redacted"]},
        },
        metadata={},
        timeline_fields={"status": "completed", "span_id": "call_123"},
    )

    assert presentation == {
        "title": "Searched the web",
        "summary": "Found official Slack MCP setup sources.",
        "status_label": "Done",
        "kind": "result",
        "result_preview": [
            {
                "title": "Slack Developer Docs",
                "subtitle": "Official MCP overview",
                "url": "https://docs.slack.dev/ai/slack-mcp-server/",
                "badge": "Official",
            }
        ],
        "debug_label": "Tool details",
        "group_key": "call_123",
    }


async def test_approval_requested_uses_deterministic_template_without_calling_llm() -> (
    None
):
    presenter_calls: list[str] = []

    def recording_presenter(prompt: str) -> dict[str, object]:
        presenter_calls.append(prompt)
        return {"title": "should not be used", "status_label": "Done", "kind": "result"}

    generator = PresentationGenerator(presenter=recording_presenter)

    presentation = await generator.presentation_for_event(
        run=run_record(),
        event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
        source=StreamEventSource.RUNTIME,
        payload={
            "approval_id": "approval_123",
            "server_name": "mcp_clickup_com",
            "tool_name": "clickup_resolve_assignees",
            "status": "pending",
        },
        metadata={},
        timeline_fields={"status": "pending", "span_id": "span_123"},
    )

    assert presentation is not None
    assert presentation["status_label"] == "Waiting for permission"
    assert presentation["kind"] == "approval"
    # Tool name humanized in the title, no raw protocol identifiers leaked.
    assert "Clickup Resolve Assignees" in presentation["title"]
    assert "mcp_clickup_com" not in str(presentation)
    assert "clickup_resolve_assignees" not in str(presentation)
    # The LLM presenter must not be consulted for deterministic event types.
    assert presenter_calls == []


async def test_presentation_context_uses_display_facts_not_raw_protocol_names() -> None:
    generator = PresentationGenerator()
    presentation = await generator.presentation_for_event(
        run=run_record(),
        event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
        source=StreamEventSource.RUNTIME,
        payload={
            "approval_id": "approval_123",
            "server_name": "mcp_clickup_com",
            "tool_name": "clickup_resolve_assignees",
            "display_name": "ClickUp",
            "read_only": True,
            "risk_level": "low",
            "status": "pending",
        },
        metadata={},
        timeline_fields={"status": "pending", "span_id": "span_123"},
    )

    assert presentation is not None
    presentation_str = str(presentation)
    assert "mcp_clickup_com" not in presentation_str
    assert "clickup_resolve_assignees" not in presentation_str


async def test_tool_call_completed_does_not_generate_weaker_presentation() -> None:
    generator = PresentationGenerator(
        presenter=lambda _: {
            "title": "Weak completion",
            "status_label": "Done",
            "kind": "progress",
        }
    )

    presentation = await generator.presentation_for_event(
        run=run_record(),
        event_type=RuntimeApiEventType.TOOL_CALL_COMPLETED,
        source=StreamEventSource.RUNTIME,
        payload={
            "tool_name": "web_search",
            "call_id": "call_123",
            "status": "completed",
        },
        metadata={},
        timeline_fields={"status": "completed", "span_id": "call_123"},
    )

    assert presentation is None


async def test_tool_result_context_includes_preview_rows() -> None:
    captured_prompt = ""

    def presenter(prompt: str) -> dict[str, object]:
        nonlocal captured_prompt
        captured_prompt = prompt
        return {
            "title": "Searched sources",
            "status_label": "Done",
            "kind": "result",
            "result_preview": [
                {
                    "title": "Slack Developer Docs",
                    "subtitle": "Official docs",
                    "url": "https://docs.slack.dev/ai/slack-mcp-server/",
                    "badge": "Official",
                }
            ],
        }

    generator = PresentationGenerator(presenter=presenter)
    presentation = await generator.presentation_for_event(
        run=run_record(),
        event_type=RuntimeApiEventType.TOOL_RESULT,
        source=StreamEventSource.RUNTIME,
        payload={
            "tool_name": "web_search",
            "call_id": "call_123",
            "status": "completed",
            "output": {
                "results": [
                    {
                        "title": "Slack Developer Docs",
                        "snippet": "Official docs",
                        "link": "https://docs.slack.dev/ai/slack-mcp-server/",
                    }
                ]
            },
        },
        metadata={},
        timeline_fields={"status": "completed", "span_id": "call_123"},
    )

    assert presentation is not None
    assert presentation["result_preview"][0]["title"] == "Slack Developer Docs"
    assert '"result_preview"' in captured_prompt
    assert "Slack Developer Docs" in captured_prompt


async def test_event_producer_attaches_presentation_metadata() -> None:
    event_store = RecordingEventStore()
    persistence = RecordingPersistence()
    generator = PresentationGenerator(
        presenter=lambda _: {
            "title": "Searched the web",
            "summary": "Found official sources.",
            "status_label": "Done",
            "kind": "result",
            "debug_label": "Tool details",
        }
    )
    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=generator,
    )

    envelope = await producer.append_api_event(
        run=run_record(),
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.TOOL_RESULT,
        payload={
            "tool_name": "web_search",
            "call_id": "call_123",
            "status": "completed",
        },
    )

    # Preliminary presentation is attached synchronously so the SSE stream
    # gets a card immediately. The minimal-envelope path always produces
    # title + status + kind from the event lifecycle.
    assert envelope.presentation is not None
    assert envelope.presentation.status_label == "Done"
    assert envelope.presentation.kind == "result"
    assert envelope.event_type == RuntimeApiEventType.TOOL_RESULT
    preliminary_title = envelope.presentation.title

    # Background polish task patches body fields only via PRESENTATION_UPDATED.
    # Title / status_label / kind are owned by the event lifecycle and stay
    # frozen across the patch.
    await producer.flush_pending_enrichment(run_id=envelope.run_id)
    assert len(event_store.drafts) == 2
    patch = event_store.drafts[1]
    assert patch.event_type == RuntimeApiEventType.PRESENTATION_UPDATED
    assert patch.presentation is not None
    assert patch.presentation.title == preliminary_title
    assert patch.presentation.status_label == "Done"
    assert patch.presentation.kind == "result"
    assert patch.presentation.summary == "Found official sources."
    assert patch.payload["call_id"] == "call_123"
    # Patch list names the body fields the polish layer changed.
    assert "summary" in patch.payload["patches"]
    assert persistence.latest_sequence_no == 2


async def test_event_producer_skips_enrichment_for_deterministic_event_types() -> None:
    event_store = RecordingEventStore()
    persistence = RecordingPersistence()
    presenter_calls: list[str] = []

    def recording_presenter(prompt: str) -> dict[str, object]:
        presenter_calls.append(prompt)
        return {"title": "should not be used", "status_label": "Done", "kind": "result"}

    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(presenter=recording_presenter),
    )

    await producer.append_api_event(
        run=run_record(),
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
        payload={
            "approval_id": "approval_123",
            "tool_name": "gmail_send",
            "status": "pending",
        },
    )
    await producer.flush_pending_enrichment()

    assert len(event_store.drafts) == 1
    assert presenter_calls == []  # No LLM call for deterministic types.


async def test_event_producer_skips_enrichment_when_tool_template_renders() -> None:
    from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate

    event_store = RecordingEventStore()
    persistence = RecordingPersistence()
    presenter_calls: list[str] = []

    def recording_presenter(prompt: str) -> dict[str, object]:
        presenter_calls.append(prompt)
        return {"title": "should not be used", "status_label": "Done", "kind": "result"}

    template = ToolDisplayTemplate(
        title_template="Searching for {query}",
        result_title_template="Found {count} results",
    )
    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(
            presenter=recording_presenter,
            tool_display_lookup=lambda name: template if name == "web_search" else None,
        ),
    )

    envelope = await producer.append_api_event(
        run=run_record(),
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.TOOL_RESULT,
        payload={
            "tool_name": "web_search",
            "call_id": "call_42",
            "status": "completed",
            "count": 7,
        },
    )
    await producer.flush_pending_enrichment()

    assert envelope.presentation is not None
    assert envelope.presentation.title == "Found 7 results"
    assert len(event_store.drafts) == 1
    assert presenter_calls == []


async def test_event_producer_cancels_stale_enrichment_on_newer_event_for_same_call_id() -> (
    None
):
    event_store = RecordingEventStore()
    persistence = RecordingPersistence()
    presenter_calls: list[str] = []

    async def slow_presenter(prompt: str) -> dict[str, object]:
        presenter_calls.append(prompt)
        # The STARTED enrichment sleeps long enough that the RESULT event
        # arrives and cancels it before it can patch the card. The RESULT
        # enrichment returns immediately.
        if "tool_call_started" in prompt:
            await asyncio.sleep(2.0)
            return {
                "title": "Stale STARTED",
                "summary": "Stale running summary.",
                "status_label": "Running",
                "kind": "progress",
            }
        return {
            "title": "Fresh RESULT",
            "summary": "Fresh polished result summary.",
            "status_label": "Done",
            "kind": "result",
        }

    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(presenter=slow_presenter),
    )

    await producer.append_api_event(
        run=run_record(),
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.TOOL_CALL_STARTED,
        payload={"tool_name": "web_search", "call_id": "call_77"},
    )
    # Yield control so the STARTED enrichment task starts running and enters
    # the asyncio.sleep above.
    await asyncio.sleep(0)

    await producer.append_api_event(
        run=run_record(),
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.TOOL_RESULT,
        payload={
            "tool_name": "web_search",
            "call_id": "call_77",
            "status": "completed",
        },
    )
    await producer.flush_pending_enrichment()

    presentation_updates = [
        draft
        for draft in event_store.drafts
        if draft.event_type == RuntimeApiEventType.PRESENTATION_UPDATED
    ]
    # Exactly one PRESENTATION_UPDATED — the RESULT polish. The STARTED
    # polish was cancelled before it could append a stale patch.
    assert len(presentation_updates) == 1
    assert presentation_updates[0].presentation is not None
    # Patch carries the polished body but keeps the preliminary's terminal
    # lifecycle (kind=result, status=Done) — the LLM never owns those.
    assert presentation_updates[0].presentation.kind == "result"
    assert presentation_updates[0].presentation.status_label == "Done"
    assert (
        presentation_updates[0].presentation.summary == "Fresh polished result summary."
    )


async def test_event_producer_forwards_agent_intent_hint_into_presentation_prompt() -> (
    None
):
    event_store = RecordingEventStore()
    persistence = RecordingPersistence()
    captured_prompts: list[str] = []

    def recording_presenter(prompt: str) -> dict[str, object]:
        captured_prompts.append(prompt)
        return {
            "title": "Looking up Acme invoice",
            "summary": "Searching Gmail for the Q3 Acme invoice.",
            "status_label": "Done",
            "kind": "result",
        }

    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(presenter=recording_presenter),
    )

    # First, the agent emits a model_delta that captures intent.
    await producer.append_api_event(
        run=run_record(),
        source=StreamEventSource.MODEL,
        event_type=RuntimeApiEventType.MODEL_DELTA,
        payload={"delta": "I'll search Gmail for the Q3 Acme invoice."},
    )

    # Then a tool result arrives — the LLM prompt should include intent_hint.
    await producer.append_api_event(
        run=run_record(),
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.TOOL_RESULT,
        payload={
            "tool_name": "gmail_search",
            "call_id": "call_99",
            "status": "completed",
        },
    )
    await producer.flush_pending_enrichment()

    assert any(
        "I'll search Gmail" in prompt and "agent_intent_hint" in prompt
        for prompt in captured_prompts
    )
