"""Unit tests for `DeterministicTemplates` and `ToolTemplateRenderer`.

These verify that every event type listed in `DeterministicTemplates.HANDLED`
produces a presentation that satisfies `RuntimeEventPresentation` end-to-end,
and that `ToolTemplateRenderer` honors / safely fails on a tool author's
`ToolDisplayTemplate`.
"""

from __future__ import annotations

from agent_runtime.api.presentation_templates import (
    DeterministicTemplates,
    ToolTemplateRenderer,
)
from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate
from runtime_api.schemas import RuntimeApiEventType, RuntimeEventPresentation


def _validate(presentation: dict[str, object] | None) -> RuntimeEventPresentation:
    assert presentation is not None
    return RuntimeEventPresentation.model_validate(presentation)


class TestDeterministicTemplates:
    def test_approval_resolved_approved_renders_granted_card(self) -> None:
        rendered = DeterministicTemplates.render(
            event_type=RuntimeApiEventType.APPROVAL_RESOLVED,
            payload={"status": "approved", "tool_name": "gmail_send"},
            timeline_fields={},
            group_key="approval_1",
        )
        validated = _validate(rendered)
        assert validated.title == "Permission granted"
        assert validated.status_label == "Done"
        assert validated.kind == "approval"
        assert validated.confidence == "high"
        assert "Gmail Send" in (validated.summary or "")

    def test_approval_resolved_denied_renders_blocked_card(self) -> None:
        rendered = DeterministicTemplates.render(
            event_type=RuntimeApiEventType.APPROVAL_RESOLVED,
            payload={"status": "rejected", "tool_name": "gmail_send"},
            timeline_fields={},
            group_key="approval_1",
        )
        validated = _validate(rendered)
        assert validated.title == "Permission denied"
        assert validated.status_label == "Failed"

    def test_approval_requested_uses_humanized_tool_and_entity(self) -> None:
        rendered = DeterministicTemplates.render(
            event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
            payload={
                "tool_name": "clickup_resolve_assignees",
                "server_name": "mcp_clickup_com",
                "display_name": "ClickUp",
                "status": "pending",
            },
            timeline_fields={},
            group_key="approval_2",
        )
        validated = _validate(rendered)
        assert validated.title.startswith("Allow Clickup Resolve Assignees")
        assert validated.status_label == "Waiting for permission"
        assert validated.kind == "approval"
        assert validated.primary_entity == "Clickup"
        # Raw protocol identifiers must not leak.
        assert "mcp_clickup_com" not in str(rendered)
        assert "clickup_resolve_assignees" not in str(rendered)

    def test_mcp_auth_required_renders_connect_card(self) -> None:
        rendered = DeterministicTemplates.render(
            event_type=RuntimeApiEventType.MCP_AUTH_REQUIRED,
            payload={
                "server_name": "mcp_gmail_com",
                "display_name": "Gmail",
                "auth_url": "https://auth/authorize",
            },
            timeline_fields={},
            group_key="auth_1",
        )
        validated = _validate(rendered)
        assert validated.title == "Connect Gmail"
        assert validated.kind == "auth"
        assert validated.status_label == "Waiting for permission"
        assert validated.primary_entity == "Gmail"

    def test_mcp_auth_required_falls_back_to_generic_entity(self) -> None:
        rendered = DeterministicTemplates.render(
            event_type=RuntimeApiEventType.MCP_AUTH_REQUIRED,
            payload={},
            timeline_fields={},
            group_key=None,
        )
        validated = _validate(rendered)
        assert validated.title == "Connect this app"
        assert validated.primary_entity is None

    def test_run_failed_maps_known_error_codes(self) -> None:
        rendered = DeterministicTemplates.render(
            event_type=RuntimeApiEventType.RUN_FAILED,
            payload={"error_code": "TIMEOUT"},
            timeline_fields={},
            group_key=None,
        )
        validated = _validate(rendered)
        assert validated.title == "Step timed out"
        assert validated.kind == "error"
        assert validated.status_label == "Failed"

    def test_run_failed_unknown_code_uses_default_message(self) -> None:
        rendered = DeterministicTemplates.render(
            event_type=RuntimeApiEventType.RUN_FAILED,
            payload={"error_code": "MYSTERY_BUG"},
            timeline_fields={},
            group_key=None,
        )
        validated = _validate(rendered)
        assert validated.title == "Step failed"

    def test_error_event_uses_static_template(self) -> None:
        rendered = DeterministicTemplates.render(
            event_type=RuntimeApiEventType.ERROR,
            payload={"error_code": "PERMISSION_DENIED"},
            timeline_fields={},
            group_key="span_1",
        )
        validated = _validate(rendered)
        assert validated.title == "Not allowed"
        assert validated.kind == "error"

    def test_tool_call_delta_uses_progress_template(self) -> None:
        rendered = DeterministicTemplates.render(
            event_type=RuntimeApiEventType.TOOL_CALL_DELTA,
            payload={"tool_name": "web_search", "message": "Fetching page 2..."},
            timeline_fields={"display_title": "Searching the web"},
            group_key="call_42",
        )
        validated = _validate(rendered)
        assert validated.title == "Searching the web"
        assert validated.kind == "progress"
        assert validated.status_label == "Running"
        assert validated.summary == "Fetching page 2..."
        assert validated.primary_entity == "Web Search"

    def test_returns_none_for_unhandled_event_type(self) -> None:
        assert (
            DeterministicTemplates.render(
                event_type=RuntimeApiEventType.TOOL_RESULT,
                payload={},
                timeline_fields={},
                group_key=None,
            )
            is None
        )


class TestToolTemplateRenderer:
    def test_renders_start_template_for_tool_call_started(self) -> None:
        template = ToolDisplayTemplate(
            title_template="Searching {connector} for {query}",
            summary_template="Looking through inbox for {query}",
            result_title_template="Found {count} results",
        )
        rendered = ToolTemplateRenderer.render(
            event_type=RuntimeApiEventType.TOOL_CALL_STARTED,
            payload={"connector": "Gmail", "query": "Q3 invoice"},
            template=template,
            group_key="call_99",
        )
        validated = _validate(rendered)
        assert validated.title == "Searching Gmail for Q3 invoice"
        assert validated.summary == "Looking through inbox for Q3 invoice"
        assert validated.kind == "progress"
        assert validated.status_label == "Running"

    def test_renders_result_template_for_tool_result(self) -> None:
        template = ToolDisplayTemplate(
            title_template="Searching for {query}",
            result_title_template="Found {count} results",
            result_summary_template="Top match: {top_title}",
        )
        rendered = ToolTemplateRenderer.render(
            event_type=RuntimeApiEventType.TOOL_RESULT,
            payload={
                "query": "Q3 invoice",
                "count": 12,
                "top_title": "Acme Q3 invoice",
            },
            template=template,
            group_key="call_99",
        )
        validated = _validate(rendered)
        assert validated.title == "Found 12 results"
        assert validated.summary == "Top match: Acme Q3 invoice"
        assert validated.kind == "result"
        assert validated.status_label == "Done"

    def test_returns_none_when_placeholder_missing(self) -> None:
        template = ToolDisplayTemplate(
            title_template="Searching for {query}",
        )
        # 'query' missing from payload — caller should fall through to LLM.
        assert (
            ToolTemplateRenderer.render(
                event_type=RuntimeApiEventType.TOOL_CALL_STARTED,
                payload={},
                template=template,
                group_key=None,
            )
            is None
        )

    def test_returns_none_for_lifecycle_event_types(self) -> None:
        template = ToolDisplayTemplate(title_template="ignored")
        assert (
            ToolTemplateRenderer.render(
                event_type=RuntimeApiEventType.RUN_FAILED,
                payload={},
                template=template,
                group_key=None,
            )
            is None
        )
