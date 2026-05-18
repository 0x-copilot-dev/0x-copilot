"""Tests for dispatcher-aware ``display_title`` projection.

The projector at ``RuntimeEventPresentationProjector._display_title_for``
delegates to :class:`McpDispatcherUnwrap` so that ``call_mcp_tool`` events
render the inner tool name (e.g. ``"list_issues"``) rather than the raw
dispatcher name. Before the helper existed the projector inlined the
``tool_name`` lookup and skipped the unwrap entirely — every MCP tool row
read ``"Calling call_mcp_tool"`` instead of the user-meaningful action.

These tests pin the projector's behaviour at the unwrap boundary so any
regression that drops the helper call surfaces immediately. Tool-name
mapping for regular (non-dispatcher) tools is already covered elsewhere
(e.g. ``tests/unit/agent_runtime/capabilities/test_citations.py``).
"""

from __future__ import annotations

from runtime_api.schemas import (
    RuntimeApiEventType,
    RuntimeEventPresentationProjector,
)


def _display_title(
    *,
    event_type: RuntimeApiEventType,
    payload: dict[str, object],
) -> str | None:
    return RuntimeEventPresentationProjector._display_title_for(  # noqa: SLF001
        event_type=event_type,
        payload=payload,
    )


class TestDispatcherDisplayTitle:
    def test_started_unwraps_inner_tool_name(self) -> None:
        payload = {
            "tool_name": "call_mcp_tool",
            "args": {"tool_name": "list_issues", "server_name": "linear"},
        }

        title = _display_title(
            event_type=RuntimeApiEventType.TOOL_CALL_STARTED, payload=payload
        )

        assert title is not None
        assert "list_issues" in title
        assert "call_mcp_tool" not in title

    def test_delta_unwraps_inner_tool_name(self) -> None:
        payload = {
            "tool_name": "call_mcp_tool",
            "args": {"tool_name": "list_issues", "server_name": "linear"},
        }

        title = _display_title(
            event_type=RuntimeApiEventType.TOOL_CALL_DELTA, payload=payload
        )

        assert title is not None
        assert "list_issues" in title
        assert "call_mcp_tool" not in title

    def test_completed_unwraps_inner_tool_name(self) -> None:
        payload = {
            "tool_name": "call_mcp_tool",
            "args": {"tool_name": "list_issues", "server_name": "linear"},
        }

        title = _display_title(
            event_type=RuntimeApiEventType.TOOL_CALL_COMPLETED, payload=payload
        )

        assert title is not None
        assert "list_issues" in title
        assert "call_mcp_tool" not in title

    def test_result_unwraps_inner_tool_name(self) -> None:
        payload = {
            "tool_name": "call_mcp_tool",
            "args": {"tool_name": "list_issues", "server_name": "linear"},
        }

        title = _display_title(
            event_type=RuntimeApiEventType.TOOL_RESULT, payload=payload
        )

        assert title is not None
        assert "list_issues" in title
        assert "call_mcp_tool" not in title

    def test_started_with_missing_args_falls_back_to_dispatcher_name(self) -> None:
        """At ``tool_call_started`` the args may not have streamed yet. The
        title should still be informative — falling back to the dispatcher
        name (``"Calling call_mcp_tool"``) is acceptable; falling back to
        the empty / generic ``"Calling tool"`` is not, because the dispatcher
        name carries the dispatcher's identity rather than dropping it."""

        payload = {"tool_name": "call_mcp_tool"}

        title = _display_title(
            event_type=RuntimeApiEventType.TOOL_CALL_STARTED, payload=payload
        )

        assert title is not None
        assert "call_mcp_tool" in title

    def test_started_with_empty_args_falls_back_to_dispatcher_name(self) -> None:
        payload = {"tool_name": "call_mcp_tool", "args": {}}

        title = _display_title(
            event_type=RuntimeApiEventType.TOOL_CALL_STARTED, payload=payload
        )

        assert title is not None
        assert "call_mcp_tool" in title

    def test_non_dispatcher_tool_passes_through_unchanged(self) -> None:
        """Regular tools have no nested args and must render their raw name."""

        payload = {"tool_name": "web_search"}

        title = _display_title(
            event_type=RuntimeApiEventType.TOOL_CALL_STARTED, payload=payload
        )

        assert title is not None
        assert "web_search" in title

    def test_configured_display_title_wins_over_unwrap(self) -> None:
        """An explicit ``display_title`` on the payload must still win — the
        unwrap is a fallback, not an override. The projector reads the
        configured title first and short-circuits before consulting the
        unwrap helper."""

        payload = {
            "display_title": "Atlas pre-set title",
            "tool_name": "call_mcp_tool",
            "args": {"tool_name": "list_issues"},
        }

        title = _display_title(
            event_type=RuntimeApiEventType.TOOL_CALL_STARTED, payload=payload
        )

        assert title == "Atlas pre-set title"
