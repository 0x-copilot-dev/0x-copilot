"""Unit tests for `DeterministicTemplates` and `ToolTemplateRenderer`.

These verify that every event type listed in `DeterministicTemplates.HANDLED`
produces a presentation that satisfies `RuntimeEventPresentation` end-to-end,
and that `ToolTemplateRenderer` honors / safely fails on a tool author's
`ToolDisplayTemplate`.
"""

from __future__ import annotations

from agent_runtime.api.presentation_templates import (
    DeterministicTemplates,
    PayloadProjector,
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

    def test_tool_call_delta_drops_raw_delta_token(self) -> None:
        """Raw streaming JSON-arg tokens (`{"`, `"}`, `":` …) are not
        user-readable and must not leak into summary/action_label. Only an
        explicit ``message`` populates the progress fields."""
        rendered = DeterministicTemplates.render(
            event_type=RuntimeApiEventType.TOOL_CALL_DELTA,
            payload={"tool_name": "ls", "delta": '"}'},
            timeline_fields={"display_title": "ls running"},
            group_key="call_7",
        )
        validated = _validate(rendered)
        assert validated.title == "ls running"
        assert validated.summary is None
        assert validated.action_label is None

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

    def test_projects_declared_result_preview_rows_for_tool_result(self) -> None:
        template = ToolDisplayTemplate(
            title_template="Searching {connector}",
            result_title_template="Web Search Result",
            result_preview_path="output.results",
            result_preview_row={
                "title": "title",
                "subtitle": "snippet",
                "url": "url",
            },
        )
        rendered = ToolTemplateRenderer.render(
            event_type=RuntimeApiEventType.TOOL_RESULT,
            payload={
                "connector": "DuckDuckGo",
                "output": {
                    "results": [
                        {
                            "title": "LangGraph streaming docs",
                            "snippet": "Build streaming graphs",
                            "url": "https://docs.langchain.com/langgraph",
                        },
                        {
                            "title": "LangGraph release notes",
                            "snippet": "0.2 release",
                            "url": "https://github.com/langchain-ai/langgraph",
                        },
                    ]
                },
            },
            template=template,
            group_key="call_77",
        )
        validated = _validate(rendered)
        assert validated.title == "Web Search Result"
        assert validated.kind == "result"
        assert len(validated.result_preview) == 2
        assert validated.result_preview[0].title == "LangGraph streaming docs"
        assert validated.result_preview[0].subtitle == "Build streaming graphs"
        assert validated.result_preview[0].url == "https://docs.langchain.com/langgraph"


class TestPayloadProjector:
    def test_projects_declared_path_and_row_mapping(self) -> None:
        template = ToolDisplayTemplate(
            title_template="Search",
            result_preview_path="output.results",
            result_preview_row={
                "title": "headline",
                "subtitle": "blurb",
                "url": "link",
            },
        )
        preview = PayloadProjector.project(
            payload={
                "output": {
                    "results": [
                        {
                            "headline": "Doc one",
                            "blurb": "First match",
                            "link": "https://example.com/one",
                        }
                    ]
                }
            },
            template=template,
        )
        assert preview == [
            {
                "title": "Doc one",
                "subtitle": "First match",
                "url": "https://example.com/one",
            }
        ]

    def test_falls_back_to_heuristics_when_no_template(self) -> None:
        # DuckDuckGo-style payload — list of dicts under output, with
        # title / snippet / link fields.
        preview = PayloadProjector.project(
            payload={
                "output": [
                    {
                        "title": "Result A",
                        "snippet": "Brief A",
                        "link": "https://example.com/a",
                    },
                    {
                        "title": "Result B",
                        "snippet": "Brief B",
                        "link": "https://example.com/b",
                    },
                ]
            },
            template=None,
        )
        assert len(preview) == 2
        assert preview[0]["title"] == "Result A"
        assert preview[0]["subtitle"] == "Brief A"
        assert preview[0]["url"] == "https://example.com/a"

    def test_walks_common_container_keys(self) -> None:
        preview = PayloadProjector.project(
            payload={
                "items": [
                    {
                        "name": "Item one",
                        "description": "First item",
                        "href": "https://example.com/i1",
                    }
                ]
            },
            template=None,
        )
        assert preview == [
            {
                "title": "Item one",
                "subtitle": "First item",
                "url": "https://example.com/i1",
            }
        ]

    def test_caps_at_five_rows(self) -> None:
        rows = [
            {"title": f"row {index}", "url": f"https://example.com/{index}"}
            for index in range(8)
        ]
        preview = PayloadProjector.project(payload={"results": rows}, template=None)
        assert len(preview) == PayloadProjector.MAX_ROWS == 5

    def test_skips_rows_without_title(self) -> None:
        preview = PayloadProjector.project(
            payload={
                "results": [
                    {"snippet": "no title here"},
                    {"title": "Kept"},
                ]
            },
            template=None,
        )
        assert preview == [{"title": "Kept"}]

    def test_strips_html_tags_from_strings(self) -> None:
        preview = PayloadProjector.project(
            payload={
                "results": [
                    {
                        "title": "<b>Bold title</b>",
                        "snippet": "  spaced  text  ",
                        "url": "https://example.com/x",
                    }
                ]
            },
            template=None,
        )
        assert preview == [
            {
                "title": "bBold title/b",
                "subtitle": "spaced text",
                "url": "https://example.com/x",
            }
        ]

    def test_drops_non_http_urls(self) -> None:
        preview = PayloadProjector.project(
            payload={
                "results": [
                    {
                        "title": "Local file",
                        "url": "file:///tmp/local",
                    }
                ]
            },
            template=None,
        )
        assert preview == [{"title": "Local file"}]

    def test_returns_empty_for_payloads_without_rows(self) -> None:
        assert PayloadProjector.project(payload={}, template=None) == []
        assert (
            PayloadProjector.project(payload={"output": "plain string"}, template=None)
            == []
        )

    def test_parses_json_string_output(self) -> None:
        preview = PayloadProjector.project(
            payload={
                "output": '[{"title": "From JSON", "url": "https://example.com/j"}]'
            },
            template=None,
        )
        assert preview == [{"title": "From JSON", "url": "https://example.com/j"}]
