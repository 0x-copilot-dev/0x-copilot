"""Unit tests for ``McpDisplayRegistryContext`` (polish-removal Phase 2.B).

Pins the ContextVar lifecycle the run / approval handlers depend on:
register-while-bound writes; register-while-unbound is a no-op; nested
binds restore correctly; ``get`` mirrors the binding state.

See ``docs/refactor/01-presentation-polish-removal.md`` §4 Phase 2.B.
"""

from __future__ import annotations

from agent_runtime.capabilities.mcp.descriptor_registry import (
    McpDisplayRegistryContext,
)
from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate


def _template(title: str = "Title") -> ToolDisplayTemplate:
    return ToolDisplayTemplate(title_template=title, synthetic=True)


def test_active_returns_none_when_unbound() -> None:
    assert McpDisplayRegistryContext.active() is None


def test_register_is_noop_when_unbound() -> None:
    """Lazy descriptor builds outside a run context (replay / eval / unit
    tests of the loader in isolation) must never raise — Phase 2.B's
    ``BackendMcpClient._tool_descriptor`` calls ``register`` unconditionally.
    """

    # Should not raise. Subsequent get returns None (still unbound).
    McpDisplayRegistryContext.register("list_issues", _template())
    assert McpDisplayRegistryContext.get("list_issues") is None


def test_bind_register_get_round_trip() -> None:
    registry: dict[str, ToolDisplayTemplate] = {}
    template = _template("List Linear issues for {query}")
    token = McpDisplayRegistryContext.bind_for_run(registry)
    try:
        McpDisplayRegistryContext.register("list_issues", template)
        assert McpDisplayRegistryContext.get("list_issues") is template
        assert registry == {"list_issues": template}
    finally:
        McpDisplayRegistryContext.unbind(token)
    assert McpDisplayRegistryContext.active() is None


def test_get_returns_none_for_unknown_tool_when_bound() -> None:
    registry: dict[str, ToolDisplayTemplate] = {}
    token = McpDisplayRegistryContext.bind_for_run(registry)
    try:
        assert McpDisplayRegistryContext.get("never_registered") is None
    finally:
        McpDisplayRegistryContext.unbind(token)


def test_register_overwrites_on_duplicate_name() -> None:
    """Two MCP servers exposing tools with the same name → last one wins.
    The runtime relies on this rather than failing — the loader's
    duplicate-server-name guard catches the more interesting collision."""

    registry: dict[str, ToolDisplayTemplate] = {}
    first = _template("First")
    second = _template("Second")
    token = McpDisplayRegistryContext.bind_for_run(registry)
    try:
        McpDisplayRegistryContext.register("dup_name", first)
        McpDisplayRegistryContext.register("dup_name", second)
        assert McpDisplayRegistryContext.get("dup_name") is second
    finally:
        McpDisplayRegistryContext.unbind(token)


def test_nested_binds_restore_outer_token() -> None:
    """Mirrors the citation ledger contract — used by tests that nest
    binds and by any in-process worker that reuses ContextVars across
    runs in the same thread."""

    outer: dict[str, ToolDisplayTemplate] = {"outer_tool": _template("Outer")}
    inner: dict[str, ToolDisplayTemplate] = {"inner_tool": _template("Inner")}

    outer_token = McpDisplayRegistryContext.bind_for_run(outer)
    try:
        assert McpDisplayRegistryContext.active() is outer
        inner_token = McpDisplayRegistryContext.bind_for_run(inner)
        try:
            assert McpDisplayRegistryContext.active() is inner
            assert McpDisplayRegistryContext.get("inner_tool") is not None
            assert McpDisplayRegistryContext.get("outer_tool") is None
        finally:
            McpDisplayRegistryContext.unbind(inner_token)
        assert McpDisplayRegistryContext.active() is outer
        assert McpDisplayRegistryContext.get("outer_tool") is not None
        assert McpDisplayRegistryContext.get("inner_tool") is None
    finally:
        McpDisplayRegistryContext.unbind(outer_token)

    assert McpDisplayRegistryContext.active() is None
