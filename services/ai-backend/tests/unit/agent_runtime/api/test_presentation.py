from __future__ import annotations

import asyncio
from collections.abc import Mapping

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


async def resolve_presentation(
    generator: PresentationGenerator,
    *,
    run: RunRecord,
    event_type: RuntimeApiEventType,
    source: StreamEventSource,
    payload: Mapping[str, object],
    metadata: Mapping[str, object],
    timeline_fields: Mapping[str, object],
) -> Mapping[str, object] | None:
    """Mirror the producer's split: preliminary, then conditional enrichment.

    Production splits the call so the SSE stream gets a card immediately
    (preliminary, sync) and the LLM polish lands as a follow-up
    ``presentation_updated`` event (enrich, async, off the hot path).
    Tests exercise the same composition through this helper instead of a
    single combined wrapper.
    """

    preliminary = generator.preliminary_presentation_for_event(
        event_type=event_type,
        payload=payload,
        metadata=metadata,
        timeline_fields=timeline_fields,
    )
    if not generator.event_eligible_for_enrichment(event_type, payload, metadata):
        return preliminary
    enriched = await generator.enrich_presentation_for_event(
        run=run,
        event_type=event_type,
        source=source,
        payload=payload,
        metadata=metadata,
        timeline_fields=timeline_fields,
    )
    return enriched or preliminary


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


def test_approval_requested_payload_strips_unknown_fields_for_generic_approvals() -> (
    None
):
    """Genuine approval payloads keep their narrow allow-list — question-shaped
    fields would only be noise for an MCP-tool approval."""

    projected = RuntimeEventPresentationProjector.payload_for_event(
        event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
        payload={
            "approval_id": "approval_1",
            "approval_kind": "mcp_tool",
            "tool_name": "list_tasks",
            "message": "Allow ClickUp search?",
            "status": "pending",
            "question": "leaked",
            "options": ["leaked"],
        },
    )

    assert "question" not in projected
    assert "options" not in projected
    assert projected["approval_kind"] == "mcp_tool"
    assert projected["message"] == "Allow ClickUp search?"


def test_ask_a_question_approval_payload_preserves_question_fields() -> None:
    """ask_a_question payloads carry user-visible content (question, hint,
    options, multi_select, allow_free_text). The projector must keep these
    intact so the chat UI can render the dedicated question card."""

    projected = RuntimeEventPresentationProjector.payload_for_event(
        event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
        payload={
            "approval_id": "ask_a_question:run_1:trace_1",
            "approval_kind": "ask_a_question",
            "header": "Pick a powertrain",
            "question": "Petrol or Diesel?",
            "hint": "Diesel for >15k km/yr",
            "options": [
                {
                    "label": "Petrol + Automatic",
                    "description": "Smoother in city traffic.",
                    "recommended": True,
                },
                "Diesel + Manual",
            ],
            "multi_select": False,
            "allow_free_text": True,
            "status": "pending",
        },
    )

    assert projected["approval_kind"] == "ask_a_question"
    assert projected["header"] == "Pick a powertrain"
    assert projected["question"] == "Petrol or Diesel?"
    assert projected["hint"] == "Diesel for >15k km/yr"
    assert projected["multi_select"] is False
    assert projected["allow_free_text"] is True
    assert projected["options"] == [
        {
            "label": "Petrol + Automatic",
            "description": "Smoother in city traffic.",
            "recommended": True,
        },
        {"label": "Diesel + Manual"},
    ]


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

    presentation = await resolve_presentation(
        generator,
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

    presentation = await resolve_presentation(
        generator,
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
    presentation = await resolve_presentation(
        generator,
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

    presentation = await resolve_presentation(
        generator,
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
    presentation = await resolve_presentation(
        generator,
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


def test_failed_tool_result_renders_error_kind_card() -> None:
    """A `tool_result` with `status='failed'` must render kind='error' /
    status_label='Failed', not the default 'Done' / kind='result'. The summary
    falls back to the typed error code's static copy when no error_message is
    on the payload."""

    generator = PresentationGenerator()
    presentation = generator.preliminary_presentation_for_event(
        event_type=RuntimeApiEventType.TOOL_RESULT,
        payload={
            "tool_name": "web_search",
            "call_id": "call_err",
            "status": "failed",
            "error_code": "tool_exception",
        },
        metadata={},
        timeline_fields={"status": "failed", "span_id": "call_err"},
    )

    assert presentation is not None
    assert presentation["kind"] == "error"
    assert presentation["status_label"] == "Failed"
    # Title comes from _ErrorMessage.for_code('tool_exception').
    assert presentation["title"] == "Step failed"
    # Summary comes from the typed code's copy when no error_message is present.
    assert "tool reported an error" in presentation["summary"].lower()


def test_timed_out_tool_result_uses_typed_error_code_summary() -> None:
    """`status='timed_out'` + `error_code='tool_timeout'` should render with
    the timeout-specific copy from `_ErrorMessage`, not the generic default."""

    generator = PresentationGenerator()
    presentation = generator.preliminary_presentation_for_event(
        event_type=RuntimeApiEventType.TOOL_RESULT,
        payload={
            "tool_name": "slow_tool",
            "call_id": "call_slow",
            "status": "timed_out",
            "error_code": "tool_timeout",
        },
        metadata={},
        timeline_fields={"status": "timed_out", "span_id": "call_slow"},
    )

    assert presentation is not None
    assert presentation["kind"] == "error"
    assert presentation["status_label"] == "Failed"
    assert presentation["title"] == "Step timed out"
    assert "took too long" in presentation["summary"].lower()


def test_failed_tool_result_with_explicit_error_message_wins_over_typed_copy() -> None:
    """When a tool surfaces a typed `error_message` on the payload, that wins
    over `_ErrorMessage.for_code(...)`'s static fallback summary."""

    generator = PresentationGenerator()
    presentation = generator.preliminary_presentation_for_event(
        event_type=RuntimeApiEventType.TOOL_RESULT,
        payload={
            "tool_name": "web_search",
            "call_id": "call_err",
            "status": "failed",
            "error_code": "tool_exception",
            "error_message": "Upstream API rejected the request: 503 Service Unavailable",
        },
        metadata={},
        timeline_fields={"status": "failed", "span_id": "call_err"},
    )

    assert presentation is not None
    assert presentation["summary"].startswith("Upstream API rejected the request")


def test_failed_tool_result_skips_payload_projector() -> None:
    """The projector heuristics could surface noise from an error payload's
    `error.context` etc. Result_preview must not appear on a failed card."""

    generator = PresentationGenerator()
    presentation = generator.preliminary_presentation_for_event(
        event_type=RuntimeApiEventType.TOOL_RESULT,
        payload={
            "tool_name": "web_search",
            "call_id": "call_err",
            "status": "failed",
            "error_code": "tool_exception",
            # A list-shaped output that the projector would happily render
            # if we let it run; we should suppress it on failures.
            "output": {
                "results": [
                    {"title": "row 1", "url": "https://example.com/1"},
                    {"title": "row 2", "url": "https://example.com/2"},
                ]
            },
        },
        metadata={},
        timeline_fields={"status": "failed", "span_id": "call_err"},
    )

    assert presentation is not None
    assert presentation["kind"] == "error"
    # The projector populates result_preview only on success paths; on a
    # failed card the field is absent or empty, never the noisy heuristic rows.
    assert not presentation.get("result_preview")


def test_successful_tool_result_still_renders_done_with_projector_rows() -> None:
    """Regression guard: my error-kind branch must not regress the happy path —
    a successful tool_result still goes through the projector and renders kind='result'."""

    generator = PresentationGenerator()
    presentation = generator.preliminary_presentation_for_event(
        event_type=RuntimeApiEventType.TOOL_RESULT,
        payload={
            "tool_name": "web_search",
            "call_id": "call_ok",
            "status": "completed",
            "output": {
                "results": [
                    {
                        "title": "Slack docs",
                        "snippet": "Setup",
                        "link": "https://slack.dev",
                    }
                ]
            },
        },
        metadata={},
        timeline_fields={"status": "completed", "span_id": "call_ok"},
    )

    assert presentation is not None
    assert presentation["kind"] == "result"
    assert presentation["status_label"] == "Done"
    assert presentation["result_preview"][0]["title"] == "Slack docs"


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
