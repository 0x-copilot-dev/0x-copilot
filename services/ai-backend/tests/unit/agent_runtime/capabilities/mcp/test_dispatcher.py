"""Unit tests for ``McpDispatcherUnwrap``.

The helper consolidates the dispatcher-unwrap logic previously duplicated
across the event projector and presentation generator. Tests cover the
contract that callers rely on: non-dispatcher events pass through
unchanged; dispatcher events surface the inner tool / server name when
present; both shapes fall back safely when args are missing or malformed.
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.mcp.dispatcher import McpDispatcherUnwrap


# --- effective_tool_name ---------------------------------------------------


def test_effective_tool_name_passes_through_non_dispatcher() -> None:
    payload = {"tool_name": "web_search"}

    assert McpDispatcherUnwrap.effective_tool_name(payload) == "web_search"


def test_effective_tool_name_returns_none_when_payload_is_empty() -> None:
    assert McpDispatcherUnwrap.effective_tool_name({}) is None


def test_effective_tool_name_returns_none_when_tool_name_is_blank() -> None:
    assert McpDispatcherUnwrap.effective_tool_name({"tool_name": "   "}) is None


def test_effective_tool_name_returns_none_when_tool_name_is_non_string() -> None:
    assert McpDispatcherUnwrap.effective_tool_name({"tool_name": 42}) is None


def test_effective_tool_name_unwraps_dispatcher_inner_tool() -> None:
    payload = {
        "tool_name": "call_mcp_tool",
        "args": {"tool_name": "list_issues", "server_name": "linear"},
    }

    assert McpDispatcherUnwrap.effective_tool_name(payload) == "list_issues"


def test_effective_tool_name_trims_inner_whitespace() -> None:
    payload = {
        "tool_name": "call_mcp_tool",
        "args": {"tool_name": "  list_issues  "},
    }

    assert McpDispatcherUnwrap.effective_tool_name(payload) == "list_issues"


def test_effective_tool_name_falls_back_to_dispatcher_when_args_missing() -> None:
    """The fallback is the dispatcher's own name so a started/delta event
    that hasn't streamed its args yet still produces a non-bogus title."""

    payload = {"tool_name": "call_mcp_tool"}

    assert McpDispatcherUnwrap.effective_tool_name(payload) == "call_mcp_tool"


def test_effective_tool_name_falls_back_when_args_is_empty_dict() -> None:
    payload = {"tool_name": "call_mcp_tool", "args": {}}

    assert McpDispatcherUnwrap.effective_tool_name(payload) == "call_mcp_tool"


def test_effective_tool_name_falls_back_when_args_inner_tool_is_non_string() -> None:
    payload = {
        "tool_name": "call_mcp_tool",
        "args": {"tool_name": 42, "server_name": "linear"},
    }

    assert McpDispatcherUnwrap.effective_tool_name(payload) == "call_mcp_tool"


def test_effective_tool_name_falls_back_when_args_inner_tool_is_blank() -> None:
    payload = {
        "tool_name": "call_mcp_tool",
        "args": {"tool_name": "  ", "server_name": "linear"},
    }

    assert McpDispatcherUnwrap.effective_tool_name(payload) == "call_mcp_tool"


def test_effective_tool_name_falls_back_when_args_is_non_mapping() -> None:
    payload = {"tool_name": "call_mcp_tool", "args": "not-a-mapping"}

    assert McpDispatcherUnwrap.effective_tool_name(payload) == "call_mcp_tool"


# --- effective_server_name -------------------------------------------------


def test_effective_server_name_returns_dispatcher_server() -> None:
    payload = {
        "tool_name": "call_mcp_tool",
        "args": {"tool_name": "list_issues", "server_name": "linear"},
    }

    assert McpDispatcherUnwrap.effective_server_name(payload) == "linear"


def test_effective_server_name_trims_dispatcher_server() -> None:
    payload = {
        "tool_name": "call_mcp_tool",
        "args": {"server_name": "  linear  "},
    }

    assert McpDispatcherUnwrap.effective_server_name(payload) == "linear"


def test_effective_server_name_is_none_for_non_dispatcher() -> None:
    payload = {"tool_name": "web_search", "args": {"server_name": "linear"}}

    assert McpDispatcherUnwrap.effective_server_name(payload) is None


def test_effective_server_name_is_none_when_args_missing() -> None:
    payload = {"tool_name": "call_mcp_tool"}

    assert McpDispatcherUnwrap.effective_server_name(payload) is None


def test_effective_server_name_is_none_when_args_is_non_mapping() -> None:
    payload = {"tool_name": "call_mcp_tool", "args": "not-a-mapping"}

    assert McpDispatcherUnwrap.effective_server_name(payload) is None


def test_effective_server_name_is_none_when_server_name_is_blank() -> None:
    payload = {
        "tool_name": "call_mcp_tool",
        "args": {"server_name": "   "},
    }

    assert McpDispatcherUnwrap.effective_server_name(payload) is None


def test_effective_server_name_is_none_when_server_name_is_non_string() -> None:
    payload = {
        "tool_name": "call_mcp_tool",
        "args": {"server_name": 42},
    }

    assert McpDispatcherUnwrap.effective_server_name(payload) is None


@pytest.mark.parametrize("tool_name", ["", "   ", None])
def test_effective_server_name_is_none_for_invalid_tool_name(tool_name: object) -> None:
    payload: dict[str, object] = {"args": {"server_name": "linear"}}
    if tool_name is not None:
        payload["tool_name"] = tool_name

    assert McpDispatcherUnwrap.effective_server_name(payload) is None
