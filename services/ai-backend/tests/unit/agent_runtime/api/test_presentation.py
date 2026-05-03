from __future__ import annotations

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
        self.draft: RuntimeEventDraft | None = None

    async def append_event(self, event: RuntimeEventDraft) -> RuntimeEventEnvelope:
        self.draft = event
        return RuntimeEventEnvelope(
            run_id=event.run_id,
            conversation_id=event.conversation_id,
            sequence_no=1,
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
            "confidence": "high",
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
        "confidence": "high",
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
    assert presentation["confidence"] == "high"
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

    assert envelope.presentation is not None
    assert envelope.presentation.title == "Searched the web"
    assert envelope.metadata["presentation"]["summary"] == "Found official sources."
    assert event_store.draft is not None
    assert event_store.draft.presentation is not None
    assert persistence.latest_sequence_no == 1
