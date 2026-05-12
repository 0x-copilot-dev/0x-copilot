from __future__ import annotations

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


def resolve_presentation(
    generator: PresentationGenerator,
    *,
    event_type: RuntimeApiEventType,
    payload: Mapping[str, object],
    metadata: Mapping[str, object],
    timeline_fields: Mapping[str, object],
) -> Mapping[str, object] | None:
    """Single-shot deterministic presentation resolution.

    Polish-removal Phase 4 (docs/refactor/01-presentation-polish-removal.md):
    the producer's old "preliminary then optional async enrichment" split
    is gone — the deterministic chain produces the final envelope
    synchronously. This helper just wraps the single call so existing test
    bodies stay readable.
    """

    return generator.preliminary_presentation_for_event(
        event_type=event_type,
        payload=payload,
        metadata=metadata,
        timeline_fields=timeline_fields,
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


# --- Schema / projector tests --------------------------------------------


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


# --- Deterministic chain --------------------------------------------------


def test_approval_requested_uses_deterministic_template() -> None:
    """``APPROVAL_REQUESTED`` is in ``DeterministicTemplates.HANDLED`` so it
    always renders from the deterministic template — never via tool-template
    lookup or minimal envelope. No raw protocol identifiers leak."""

    generator = PresentationGenerator()

    presentation = resolve_presentation(
        generator,
        event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
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
    assert "Clickup Resolve Assignees" in presentation["title"]
    assert "mcp_clickup_com" not in str(presentation)
    assert "clickup_resolve_assignees" not in str(presentation)


def test_tool_call_completed_returns_no_presentation() -> None:
    """``TOOL_CALL_COMPLETED`` is outside ``_PRESENTATION_TARGET_EVENT_TYPES``
    (the FE renders the prior ``TOOL_CALL`` / ``TOOL_RESULT`` envelope, not
    a separate completed card)."""

    generator = PresentationGenerator()
    presentation = resolve_presentation(
        generator,
        event_type=RuntimeApiEventType.TOOL_CALL_COMPLETED,
        payload={
            "tool_name": "web_search",
            "call_id": "call_123",
            "status": "completed",
        },
        metadata={},
        timeline_fields={"status": "completed", "span_id": "call_123"},
    )

    assert presentation is None


# --- RuntimeEventProducer (deterministic only — no polish) ---------------


async def test_event_producer_attaches_presentation_synchronously() -> None:
    """The producer attaches the deterministic presentation in the same
    event append. There is no follow-up ``PRESENTATION_UPDATED`` envelope
    after Phase 4 — the chain is synchronous."""

    event_store = RecordingEventStore()
    persistence = RecordingPersistence()
    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(),
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
    assert envelope.presentation.status_label == "Done"
    assert envelope.presentation.kind == "result"
    assert envelope.event_type == RuntimeApiEventType.TOOL_RESULT
    # Single envelope written — no async polish patch event follows.
    assert len(event_store.drafts) == 1


async def test_event_producer_uses_tool_template_when_lookup_resolves() -> None:
    """When the tool registry resolves a template (via ``tool_display_lookup``)
    the producer renders it from the payload; the minimal envelope is never
    consulted."""

    from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate

    event_store = RecordingEventStore()
    persistence = RecordingPersistence()
    template = ToolDisplayTemplate(
        title_template="Searching for {query}",
        result_title_template="Found {count} results",
    )
    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(
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

    assert envelope.presentation is not None
    assert envelope.presentation.title == "Found 7 results"
    assert len(event_store.drafts) == 1


async def test_event_producer_resolves_tool_template_via_context_var_when_no_instance_lookup() -> (
    None
):
    """Phase 1 — the per-run handler binds ``ToolDisplayLookupContext`` so
    the producer's default-constructed ``PresentationGenerator`` (no
    instance-level lookup) still resolves registered tool templates."""

    from agent_runtime.api.presentation import ToolDisplayLookupContext
    from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate

    event_store = RecordingEventStore()
    persistence = RecordingPersistence()
    template = ToolDisplayTemplate(
        title_template="Searching for {query}",
        result_title_template="Found {count} results",
    )
    # Default constructor — no instance-level tool_display_lookup. Production
    # constructs the producer this way at handler init time (before any run
    # context exists). The lookup arrives via the ContextVar.
    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(),
    )

    token = ToolDisplayLookupContext.bind_for_run(
        lambda name: template if name == "web_search" else None
    )
    try:
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
    finally:
        ToolDisplayLookupContext.unbind(token)

    assert envelope.presentation is not None
    assert envelope.presentation.title == "Found 7 results"
    assert len(event_store.drafts) == 1


async def test_event_producer_renders_minimal_envelope_when_no_lookup_bound() -> None:
    """Without an instance-level lookup AND no ContextVar binding, the
    minimal-envelope path renders a humanised fallback title. Phase 4
    removed the polish path — the minimal envelope IS the safety net now."""

    from agent_runtime.api.presentation import ToolDisplayLookupContext

    event_store = RecordingEventStore()
    persistence = RecordingPersistence()
    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(),
    )

    # Defensive sanity: nothing should be bound at module import time.
    assert ToolDisplayLookupContext.active() is None

    envelope = await producer.append_api_event(
        run=run_record(),
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.TOOL_RESULT,
        payload={
            "tool_name": "web_search",
            "call_id": "call_77",
            "status": "completed",
        },
    )

    # Minimal envelope rendered — terminal lifecycle, non-empty title.
    # The exact title comes from the timeline projector's ``display_title``
    # hint (which the runtime fills from ``tool_name``); the minimal-envelope
    # path honours the hint when present, falling back to the humanised
    # tool name when absent.
    assert envelope.presentation is not None
    assert envelope.presentation.kind == "result"
    assert envelope.presentation.status_label == "Done"
    assert envelope.presentation.title  # non-empty
    # Single envelope — no PRESENTATION_UPDATED follow-up after Phase 4.
    assert len(event_store.drafts) == 1


async def test_context_var_lookup_unbinds_to_previous_token() -> None:
    """Bind / unbind preserves the prior value (matches the
    ``CitationLedger.bind_for_run`` contract) so nested binds — e.g. an
    in-process worker reusing a ContextVar across two runs — restore
    correctly."""

    from agent_runtime.api.presentation import ToolDisplayLookupContext

    assert ToolDisplayLookupContext.active() is None

    outer_lookup = lambda _name: None  # noqa: E731
    inner_lookup = lambda _name: None  # noqa: E731

    outer_token = ToolDisplayLookupContext.bind_for_run(outer_lookup)
    try:
        assert ToolDisplayLookupContext.active() is outer_lookup
        inner_token = ToolDisplayLookupContext.bind_for_run(inner_lookup)
        try:
            assert ToolDisplayLookupContext.active() is inner_lookup
        finally:
            ToolDisplayLookupContext.unbind(inner_token)
        assert ToolDisplayLookupContext.active() is outer_lookup
    finally:
        ToolDisplayLookupContext.unbind(outer_token)

    assert ToolDisplayLookupContext.active() is None


# --- MCP dispatcher (Phase 2.B) ------------------------------------------


async def test_event_producer_resolves_synthesised_mcp_template_for_dispatcher_event() -> (
    None
):
    """Phase 2.B end-to-end — when ``call_mcp_tool`` dispatches an MCP tool,
    the synthesised template registered by ``BackendMcpClient._tool_descriptor``
    is resolved via ``McpDisplayRegistryContext`` and renders against the
    *promoted* payload (inner ``args.arguments`` keys at the top level)."""

    from agent_runtime.api.presentation import ToolDisplayLookupContext
    from agent_runtime.capabilities.mcp.descriptor_registry import (
        McpDisplayRegistryContext,
    )
    from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate

    event_store = RecordingEventStore()
    persistence = RecordingPersistence()

    synthesised = ToolDisplayTemplate(
        title_template="List Linear issues for {query}",
        result_title_template="Linear results",
        synthetic=True,
    )
    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(),
    )

    mcp_registry: dict[str, ToolDisplayTemplate] = {"list_issues": synthesised}
    mcp_token = McpDisplayRegistryContext.bind_for_run(mcp_registry)
    lookup_token = ToolDisplayLookupContext.bind_for_run(
        lambda name: McpDisplayRegistryContext.get(name)
    )
    try:
        envelope = await producer.append_api_event(
            run=run_record(),
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.TOOL_CALL,
            payload={
                "tool_name": "call_mcp_tool",
                "call_id": "call_mcp_42",
                "args": {
                    "server_name": "linear",
                    "tool_name": "list_issues",
                    "arguments": {"query": "Q1 launch"},
                },
            },
        )
    finally:
        ToolDisplayLookupContext.unbind(lookup_token)
        McpDisplayRegistryContext.unbind(mcp_token)

    assert envelope.presentation is not None
    assert envelope.presentation.title == "List Linear issues for Q1 launch"
    assert len(event_store.drafts) == 1


async def test_event_producer_dispatcher_event_renders_minimal_when_inner_tool_unknown() -> (
    None
):
    """If the agent dispatches a tool name we never registered, the
    extraction succeeds but the lookup returns None and the minimal-envelope
    path renders a humanised dispatcher fallback. Phase 4: no polish to
    fall through to."""

    from agent_runtime.api.presentation import ToolDisplayLookupContext
    from agent_runtime.capabilities.mcp.descriptor_registry import (
        McpDisplayRegistryContext,
    )
    from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate

    event_store = RecordingEventStore()
    persistence = RecordingPersistence()

    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(),
    )

    mcp_registry: dict[str, ToolDisplayTemplate] = {}  # empty
    mcp_token = McpDisplayRegistryContext.bind_for_run(mcp_registry)
    lookup_token = ToolDisplayLookupContext.bind_for_run(
        lambda name: McpDisplayRegistryContext.get(name)
    )
    try:
        envelope = await producer.append_api_event(
            run=run_record(),
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.TOOL_CALL,
            payload={
                "tool_name": "call_mcp_tool",
                "call_id": "call_mcp_99",
                "args": {
                    "server_name": "newserver",
                    "tool_name": "unregistered_tool",
                    "arguments": {"q": "x"},
                },
            },
        )
    finally:
        ToolDisplayLookupContext.unbind(lookup_token)
        McpDisplayRegistryContext.unbind(mcp_token)

    # Minimal envelope from the dispatcher tool name — never crashes.
    assert envelope.presentation is not None
    assert envelope.presentation.kind == "progress"


async def test_event_producer_dispatcher_event_with_no_args_uses_dispatcher_name() -> (
    None
):
    """Defensive: a malformed dispatcher event without ``args`` falls back
    to looking up the dispatcher's own name (which won't be registered)
    instead of crashing."""

    from agent_runtime.api.presentation import ToolDisplayLookupContext

    event_store = RecordingEventStore()
    persistence = RecordingPersistence()

    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(),
    )

    lookup_calls: list[str] = []

    def recording_lookup(name: str) -> object:
        lookup_calls.append(name)
        return None

    token = ToolDisplayLookupContext.bind_for_run(recording_lookup)
    try:
        await producer.append_api_event(
            run=run_record(),
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.TOOL_CALL,
            payload={
                "tool_name": "call_mcp_tool",
                "call_id": "call_mcp_no_args",
                # No "args" key.
            },
        )
    finally:
        ToolDisplayLookupContext.unbind(token)

    # Lookup was called — and with the dispatcher name (the fallback when
    # no inner tool name was extractable). Crucially: no exception.
    assert "call_mcp_tool" in lookup_calls


# --- Tier-3 agent-supplied display (Phase 3.A) ---------------------------


async def test_event_producer_tier3_overrides_synthetic_template_title() -> None:
    """Phase 3.A — when the matched template is ``synthetic=True`` and the
    agent supplied ``_display_title`` in the tool args, Tier-3 overrides
    the rendered title."""

    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_TITLE_KEY,
    )
    from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate

    event_store = RecordingEventStore()
    persistence = RecordingPersistence()

    synthesised = ToolDisplayTemplate(
        title_template="Run Workflow",  # generic; agent will override
        synthetic=True,
    )
    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(
            tool_display_lookup=lambda name: (
                synthesised if name == "run_workflow" else None
            ),
        ),
    )

    envelope = await producer.append_api_event(
        run=run_record(),
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.TOOL_CALL,
        payload={
            "tool_name": "run_workflow",
            "call_id": "call_workflow_42",
            "args": {
                "workflow_id": "wf_q1_launch",
                DISPLAY_TITLE_KEY: "Approving Q1 budget",
            },
        },
    )

    # Tier-3 wins over the synthesised template's generic title.
    assert envelope.presentation is not None
    assert envelope.presentation.title == "Approving Q1 budget"


async def test_event_producer_tier3_does_not_override_author_template() -> None:
    """Phase 3.A invariant — when the matched template is author-written
    (``synthetic=False``), the agent's ``_display_*`` is ignored."""

    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_TITLE_KEY,
    )
    from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate

    event_store = RecordingEventStore()
    persistence = RecordingPersistence()

    # Author-written template — synthetic defaults to False.
    authored = ToolDisplayTemplate(
        title_template="Searching for {query}",
        result_title_template="Found {count} results",
    )
    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(
            tool_display_lookup=lambda name: (
                authored if name == "search_docs" else None
            ),
        ),
    )

    envelope = await producer.append_api_event(
        run=run_record(),
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.TOOL_CALL,
        payload={
            "tool_name": "search_docs",
            "call_id": "call_search_42",
            "query": "Q1",  # template placeholder lives at top level today
            "args": {
                "query": "Q1",
                # Agent tries to override an authored template — should be ignored.
                DISPLAY_TITLE_KEY: "DO NOT USE THIS TITLE",
            },
        },
    )

    assert envelope.presentation is not None
    assert envelope.presentation.title == "Searching for Q1"


async def test_event_producer_tier3_overrides_minimal_envelope_when_no_template() -> (
    None
):
    """Phase 3.A — when no template is registered for the tool, the agent's
    ``_display_*`` overrides the minimal-envelope default."""

    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_SUMMARY_KEY,
        DISPLAY_TITLE_KEY,
    )

    event_store = RecordingEventStore()
    persistence = RecordingPersistence()

    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(),
    )

    envelope = await producer.append_api_event(
        run=run_record(),
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.TOOL_CALL,
        payload={
            "tool_name": "obscure_tool",
            "call_id": "call_obscure_42",
            "args": {
                "param": "x",
                DISPLAY_TITLE_KEY: "Cataloguing Q1 risks",
                DISPLAY_SUMMARY_KEY: "Building risk register from Slack threads",
            },
        },
    )

    assert envelope.presentation is not None
    assert envelope.presentation.title == "Cataloguing Q1 risks"
    assert envelope.presentation.summary == "Building risk register from Slack threads"


async def test_event_producer_tier3_summary_only_override() -> None:
    """Agent supplies summary but not title — title stays from the
    template / minimal envelope, only summary overrides."""

    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_SUMMARY_KEY,
    )
    from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate

    event_store = RecordingEventStore()
    persistence = RecordingPersistence()

    synthesised = ToolDisplayTemplate(
        title_template="List Linear issues",
        synthetic=True,
    )
    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(
            tool_display_lookup=lambda name: (
                synthesised if name == "list_issues" else None
            ),
        ),
    )

    envelope = await producer.append_api_event(
        run=run_record(),
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.TOOL_CALL,
        payload={
            "tool_name": "list_issues",
            "call_id": "call_xyz",
            "args": {DISPLAY_SUMMARY_KEY: "Risk-tagged tickets opened in Q1"},
        },
    )

    assert envelope.presentation is not None
    assert envelope.presentation.title == "List Linear issues"  # template kept
    assert envelope.presentation.summary == "Risk-tagged tickets opened in Q1"


async def test_event_producer_tier3_for_dispatcher_event_uses_top_level_args() -> None:
    """Phase 3.A end-to-end — for a ``call_mcp_tool`` dispatcher event the
    agent puts ``_display_*`` at the top of ``args``, not inside
    ``args.arguments``. Tier-3 reads from there and combines with Phase
    2.B's MCP template lookup + payload promotion."""

    from agent_runtime.api.presentation import ToolDisplayLookupContext
    from agent_runtime.capabilities.mcp.descriptor_registry import (
        McpDisplayRegistryContext,
    )
    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_TITLE_KEY,
    )
    from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate

    event_store = RecordingEventStore()
    persistence = RecordingPersistence()

    synthesised = ToolDisplayTemplate(
        title_template="List Linear issues for {query}",
        synthetic=True,
    )
    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(),
    )

    mcp_registry: dict[str, ToolDisplayTemplate] = {"list_issues": synthesised}
    mcp_token = McpDisplayRegistryContext.bind_for_run(mcp_registry)
    lookup_token = ToolDisplayLookupContext.bind_for_run(
        lambda name: McpDisplayRegistryContext.get(name)
    )
    try:
        envelope = await producer.append_api_event(
            run=run_record(),
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.TOOL_CALL,
            payload={
                "tool_name": "call_mcp_tool",
                "call_id": "call_mcp_xyz",
                "args": {
                    "server_name": "linear",
                    "tool_name": "list_issues",
                    "arguments": {"query": "Q1 launch"},
                    DISPLAY_TITLE_KEY: "Looking up Q1 launch tickets in Linear",
                },
            },
        )
    finally:
        ToolDisplayLookupContext.unbind(lookup_token)
        McpDisplayRegistryContext.unbind(mcp_token)

    assert envelope.presentation is not None
    assert envelope.presentation.title == "Looking up Q1 launch tickets in Linear"


# --- Minimal envelope failure / success paths ----------------------------


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
    assert not presentation.get("result_preview")


def test_successful_tool_result_still_renders_done_with_projector_rows() -> None:
    """Regression guard: a successful tool_result still goes through the
    projector and renders kind='result'."""

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


# --- Phase 4 sentinel: polish path is gone -------------------------------


def test_polish_apis_are_removed() -> None:
    """Pin the Phase 4 deletion: the polish path is gone for good.

    Future regressions that try to re-introduce polish (re-add ``presenter``
    / ``presentation_settings`` / ``cache`` to the generator, or
    ``flush_pending_enrichment`` / ``_enrich_and_patch`` /
    ``_intent_buffer`` to the producer) will fail this test before they
    can ship. Also pins that no LLM client is constructed when the
    deterministic chain runs.

    See ``docs/refactor/01-presentation-polish-removal.md`` §4 Phase 4.
    """

    # PresentationGenerator surface — every polish-only field / method
    # must be gone.
    generator = PresentationGenerator()
    for removed_attr in (
        "presenter",
        "presentation_settings",
        "llm_factory",
        "cache",
        "_cached_model",
        "llm_eligible_event_types",
        "event_eligible_for_enrichment",
        "enrich_presentation_for_event",
        "_generate",
        "_structured_model",
        "_prompt",
        "_context",
        "_safe_json",
        "_display_facts",
        "_with_deterministic_fields",
        "_deterministic_card_fields",
    ):
        assert not hasattr(generator, removed_attr), (
            f"PresentationGenerator still exposes polish-only attribute "
            f"{removed_attr!r}; Phase 4 removed the polish path"
        )

    # RuntimeEventProducer surface — same.
    producer = RuntimeEventProducer(
        persistence=RecordingPersistence(),
        event_store=RecordingEventStore(),
    )
    for removed_attr in (
        "flush_pending_enrichment",
        "_pending_enrichment",
        "_intent_buffer",
        "_track_intent",
        "_inject_intent_hint",
        "_spawn_enrichment",
        "_enrich_and_patch",
        "_merge_polish",
        "_POLISH_BODY_FIELDS",
    ):
        assert not hasattr(producer, removed_attr), (
            f"RuntimeEventProducer still exposes polish-only attribute "
            f"{removed_attr!r}; Phase 4 removed the polish path"
        )

    # PresentationOutput / PresentationPreviewRowOutput were the LLM's
    # structured-output schemas; they must be unimportable.
    import agent_runtime.api.presentation_templates as templates_module

    assert not hasattr(templates_module, "PresentationOutput")
    assert not hasattr(templates_module, "PresentationPreviewRowOutput")

    # RuntimePresentationSettings + the polish env keys must be gone too.
    import agent_runtime.settings as settings_module

    assert not hasattr(settings_module, "RuntimePresentationSettings")
    assert not hasattr(settings_module._EnvFields, "PRESENTATION_MODEL")
    assert not hasattr(settings_module._EnvFields, "PRESENTATION_TIMEOUT_SECONDS")


async def test_deterministic_chain_runs_without_any_llm_client() -> None:
    """The deterministic chain must produce a complete envelope without
    instantiating any LLM client. Pins the Phase 4 invariant that the
    presentation path is provider-agnostic and offline-safe.

    Verified by running the full chain and asserting no ``langchain_core``
    chat model module is touched at runtime.
    """

    import sys

    # Snapshot which langchain modules are loaded BEFORE the chain runs.
    before = {name for name in sys.modules if name.startswith("langchain")}

    event_store = RecordingEventStore()
    persistence = RecordingPersistence()
    producer = RuntimeEventProducer(
        persistence=persistence,
        event_store=event_store,
        presentation_generator=PresentationGenerator(),
    )

    # Drive every event-type branch the deterministic chain handles.
    for event_type, payload in (
        (
            RuntimeApiEventType.APPROVAL_REQUESTED,
            {
                "approval_id": "a1",
                "tool_name": "x",
                "status": "pending",
            },
        ),
        (
            RuntimeApiEventType.TOOL_CALL,
            {"tool_name": "search_docs", "call_id": "c1", "args": {"query": "Q1"}},
        ),
        (
            RuntimeApiEventType.TOOL_RESULT,
            {
                "tool_name": "search_docs",
                "call_id": "c1",
                "status": "completed",
                "output": {"results": [{"title": "row", "url": "https://x"}]},
            },
        ),
        (
            RuntimeApiEventType.TOOL_RESULT,
            {
                "tool_name": "search_docs",
                "call_id": "c2",
                "status": "failed",
                "error_code": "tool_exception",
            },
        ),
        (
            RuntimeApiEventType.MCP_AUTH_REQUIRED,
            {
                "approval_id": "a2",
                "server_name": "linear",
                "display_name": "Linear",
                "status": "pending",
            },
        ),
    ):
        envelope = await producer.append_api_event(
            run=run_record(),
            source=StreamEventSource.RUNTIME,
            event_type=event_type,
            payload=payload,
        )
        # Every appended event has a complete presentation envelope.
        assert envelope.presentation is not None

    # No LLM-client modules were imported by the chain. Anything in
    # ``before`` is whatever the test harness already loaded; the diff
    # must be empty.
    after = {name for name in sys.modules if name.startswith("langchain")}
    new_modules = after - before
    # We tolerate ``langchain_core`` already being present (other tests
    # in the suite may have pulled it in). The invariant is no NEW
    # ``langchain_*_models`` provider modules get loaded.
    new_provider_modules = {
        name
        for name in new_modules
        if "chat_models" in name or "language_models" in name
    }
    assert new_provider_modules == set(), (
        f"Deterministic presentation chain pulled in LLM client modules: "
        f"{new_provider_modules}"
    )
