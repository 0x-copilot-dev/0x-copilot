"""Unit tests for ``DisplayMetadataMiddleware`` (polish-removal Phase 2.A).

Covers verb-form humanisation, primary-placeholder picking, output-shape
walking, row-key heuristics, idempotency, and the ``synthetic=True`` flag.

See ``docs/refactor/01-presentation-polish-removal.md`` §4 Phase 2.A.
"""

from __future__ import annotations

from agent_runtime.capabilities.middleware.display_metadata import (
    DisplayMetadataMiddleware,
)
from agent_runtime.capabilities.tools.cards import ToolDisplayTemplate


# --- Verb-form humanisation -----------------------------------------------


def test_synthesise_for_mcp_list_with_query_placeholder() -> None:
    template = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="list_issues",
        connector="Linear",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}, "status": {"type": "string"}},
        },
        output_shape={"type": "object", "properties": {"items": {"type": "array"}}},
    )

    assert template.title_template == "List Linear issues for {query}"
    assert template.synthetic is True


def test_synthesise_for_mcp_search_picks_query_placeholder() -> None:
    template = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="search_repos",
        connector="GitHub",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}, "language": {"type": "string"}},
        },
        output_shape={},
    )

    assert template.title_template == "Search GitHub repos for {query}"


def test_synthesise_for_mcp_post_picks_channel_placeholder() -> None:
    template = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="post_message",
        connector="Slack",
        input_schema={
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "text": {"type": "string"},
            },
        },
        output_shape={},
    )

    # Note: "Post to" verb form, not "Post" — distinguishes from blog posts etc.
    assert template.title_template == "Post to Slack message for {channel}"


def test_synthesise_for_mcp_get_with_id_placeholder() -> None:
    template = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="get_user",
        connector="Linear",
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
        },
        output_shape={},
    )

    assert template.title_template == "Get Linear user for {id}"


def test_synthesise_for_mcp_create_update_delete_verb_forms() -> None:
    create = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="create_issue",
        connector="Linear",
        input_schema={
            "type": "object",
            "properties": {"title": {"type": "string"}},
        },
        output_shape={},
    )
    update = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="update_issue",
        connector="Linear",
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
        },
        output_shape={},
    )
    delete = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="delete_issue",
        connector="Linear",
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
        },
        output_shape={},
    )

    assert create.title_template == "Create Linear issue for {title}"
    assert update.title_template == "Update Linear issue for {id}"
    assert delete.title_template == "Delete Linear issue for {id}"


def test_synthesise_for_mcp_unknown_verb_falls_back_to_connector_colon_name() -> None:
    template = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="run_workflow",
        connector="Custom",
        input_schema={
            "type": "object",
            "properties": {"workflow_id": {"type": "string"}},
        },
        output_shape={},
    )

    # No verb match — fallback to ``"<Connector>: <humanised>"``. The agent
    # is expected to override via _display_* (Phase 3) for these.
    assert template.title_template == "Custom: Run Workflow"
    assert template.synthetic is True


def test_synthesise_for_mcp_skips_placeholder_when_no_string_property() -> None:
    template = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="list_recent",
        connector="Notion",
        input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
        output_shape={},
    )

    # ``limit`` is not a string and no preferred key matched. Synthesiser
    # falls back to the first property regardless of type — better to
    # render ``"List Notion recent for {limit}"`` than drop the noun.
    assert template.title_template == "List Notion recent for {limit}"


def test_synthesise_for_mcp_no_input_properties_omits_placeholder() -> None:
    template = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="list_issues",
        connector="Linear",
        input_schema=None,
        output_shape={},
    )

    assert template.title_template == "List Linear issues"


# --- Output-shape walking + row heuristics --------------------------------


def test_synthesise_for_mcp_walks_items_array_for_preview_path() -> None:
    template = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="list_issues",
        connector="Linear",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        output_shape={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "status": {"type": "string"},
                            "url": {"type": "string"},
                        },
                    },
                }
            },
        },
    )

    assert template.result_preview_path == "items"
    assert template.result_preview_row == {
        "title": "title",
        "subtitle": "status",
        "url": "url",
    }


def test_synthesise_for_mcp_walks_results_array_when_items_absent() -> None:
    template = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="search_docs",
        connector="Notion",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        output_shape={
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "snippet": {"type": "string"},
                        },
                    },
                }
            },
        },
    )

    assert template.result_preview_path == "results"
    assert template.result_preview_row == {"title": "name", "subtitle": "snippet"}


def test_synthesise_for_mcp_picks_first_known_array_in_order() -> None:
    """``items`` outranks ``results`` when both are present (declared
    preference order)."""

    template = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="list_things",
        connector="X",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        output_shape={
            "type": "object",
            "properties": {
                "results": {"type": "array"},
                "items": {"type": "array"},
            },
        },
    )

    assert template.result_preview_path == "items"


def test_synthesise_for_mcp_returns_none_path_when_no_array() -> None:
    template = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="get_count",
        connector="X",
        input_schema={"type": "object"},
        output_shape={"type": "object", "properties": {"count": {"type": "integer"}}},
    )

    assert template.result_preview_path is None
    assert template.result_preview_row is None


def test_synthesise_for_mcp_returns_none_row_when_no_known_keys() -> None:
    """Array shape known but row property names are all unrecognised —
    leave ``result_preview_row`` as ``None`` so ``PayloadProjector``
    falls back to its built-in heuristics on the actual payload."""

    template = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="list_things",
        connector="X",
        input_schema={"type": "object"},
        output_shape={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "weird_key_a": {"type": "string"},
                            "weird_key_b": {"type": "string"},
                        },
                    },
                }
            },
        },
    )

    assert template.result_preview_path == "items"
    assert template.result_preview_row is None


# --- result_title_template by verb family ---------------------------------


def test_synthesise_for_mcp_result_title_for_read_verb_family() -> None:
    """List / Search / Get / Read / Fetch / Query → ``"<Connector> results"``."""

    for tool_name, expected_verb in [
        ("list_issues", "List"),
        ("search_repos", "Search"),
        ("get_user", "Get"),
        ("read_doc", "Read"),
        ("fetch_state", "Fetch"),
        ("query_db", "Query"),
    ]:
        template = DisplayMetadataMiddleware.synthesise_for_mcp(
            tool_name=tool_name,
            connector="Linear",
            input_schema={"type": "object"},
            output_shape={},
        )
        assert template.title_template.startswith(f"{expected_verb} Linear "), (
            f"{tool_name} → {template.title_template}"
        )
        assert template.result_title_template == "Linear results"


def test_synthesise_for_mcp_result_title_for_write_verb_family() -> None:
    """Post to / Send → ``"<Connector> message sent"``;
    Create / Update / Delete → ``"<Connector> updated"``."""

    post = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="post_message",
        connector="Slack",
        input_schema={},
        output_shape={},
    )
    create = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="create_issue",
        connector="Linear",
        input_schema={},
        output_shape={},
    )

    assert post.result_title_template == "Slack message sent"
    assert create.result_title_template == "Linear updated"


def test_synthesise_for_mcp_result_title_none_for_unknown_verb() -> None:
    template = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="run_workflow",
        connector="Custom",
        input_schema={},
        output_shape={},
    )

    assert template.result_title_template is None


# --- Connector-name humanisation -----------------------------------------


def test_synthesise_for_mcp_humanises_connector_id_in_title() -> None:
    template = DisplayMetadataMiddleware.synthesise_for_mcp(
        tool_name="list_issues",
        connector="enterprise_linear_io",
        input_schema={},
        output_shape={},
    )

    # ``_io`` suffix stripped, snake_case → Title Case.
    assert template.title_template == "List Enterprise Linear issues"


# --- Idempotency / determinism -------------------------------------------


def test_synthesise_for_mcp_is_pure_idempotent() -> None:
    """Same inputs always produce the same template — no module-level state."""

    inputs = dict(
        tool_name="list_issues",
        connector="Linear",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        output_shape={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}},
                    },
                }
            },
        },
    )

    first = DisplayMetadataMiddleware.synthesise_for_mcp(**inputs)
    second = DisplayMetadataMiddleware.synthesise_for_mcp(**inputs)

    assert first == second


# --- The ``synthetic`` flag invariant ------------------------------------


def test_every_synthesised_template_marks_synthetic_true() -> None:
    """Tier 3 (Phase 3) only allows agent override on synthetic templates.
    Pin the invariant: every output of ``synthesise_for_mcp`` is synthetic."""

    cases: list[tuple[str, str]] = [
        ("list_issues", "Linear"),
        ("search_repos", "GitHub"),
        ("post_message", "Slack"),
        ("create_issue", "Linear"),
        ("get_user", "Notion"),
        ("run_workflow", "Custom"),  # fallback path
        ("totally_unknown_x", "X"),  # also fallback
    ]
    for tool_name, connector in cases:
        template = DisplayMetadataMiddleware.synthesise_for_mcp(
            tool_name=tool_name,
            connector=connector,
            input_schema={},
            output_shape={},
        )
        assert template.synthetic is True, f"{tool_name} on {connector} not synthetic"


def test_author_written_template_defaults_synthetic_false() -> None:
    """``ToolDisplayTemplate(...)`` with no explicit ``synthetic=`` defaults
    to ``False`` — author copy beats Tier 3 (Phase 3) overrides."""

    template = ToolDisplayTemplate(title_template="Authored title")
    assert template.synthetic is False


# --- Phase 3.A receive-side helpers ---------------------------------------


def test_wrap_args_schema_extends_with_optional_display_fields() -> None:
    from pydantic import BaseModel

    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_SUMMARY_KEY,
        DISPLAY_TITLE_KEY,
        wrap_args_schema,
    )

    class OriginalArgs(BaseModel):
        query: str

    Wrapped = wrap_args_schema(OriginalArgs)

    # Original field still required.
    instance = Wrapped(query="Q1 launch")
    assert instance.query == "Q1 launch"
    # Display fields default to None.
    assert getattr(instance, "display_title") is None
    assert getattr(instance, "display_summary") is None

    # Agent fills them via the wire alias (the underscore-prefixed key).
    filled = Wrapped(
        **{
            "query": "Q1 launch",
            DISPLAY_TITLE_KEY: "Looking up Q1 launch tickets",
            DISPLAY_SUMMARY_KEY: "Risk-tagged tickets opened in Q1",
        }
    )
    assert filled.display_title == "Looking up Q1 launch tickets"
    assert filled.display_summary == "Risk-tagged tickets opened in Q1"

    # JSON-schema (the form the agent sees in its tool block) carries
    # both fields with their underscore-prefixed names.
    schema = Wrapped.model_json_schema()
    assert DISPLAY_TITLE_KEY in schema["properties"]
    assert DISPLAY_SUMMARY_KEY in schema["properties"]
    # No max_length cap (PRD §8 — brevity comes from the field
    # ``description``, not validation rejection).
    assert "maxLength" not in schema["properties"][DISPLAY_TITLE_KEY]
    assert "maxLength" not in schema["properties"][DISPLAY_SUMMARY_KEY]


def test_wrap_args_schema_with_none_returns_display_only_model() -> None:
    """Some LangChain tools have no ``args_schema``. The wrap returns a
    model with just the display fields so the wrap is uniform."""

    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_TITLE_KEY,
        wrap_args_schema,
    )

    Wrapped = wrap_args_schema(None)
    instance = Wrapped()
    assert getattr(instance, "display_title") is None
    schema = Wrapped.model_json_schema()
    assert DISPLAY_TITLE_KEY in schema["properties"]


def test_wrap_args_schema_is_idempotent() -> None:
    """Wrapping a wrapped schema returns the wrapped schema unchanged.
    Pins the contract that ``build_deep_agent`` (Phase 3.B) can call the
    wrap on any tools list without double-wrapping."""

    from pydantic import BaseModel

    from agent_runtime.capabilities.middleware.display_metadata import (
        wrap_args_schema,
    )

    class OriginalArgs(BaseModel):
        query: str

    once = wrap_args_schema(OriginalArgs)
    twice = wrap_args_schema(once)
    assert once is twice


def test_wrap_args_schema_rejects_unknown_display_keys() -> None:
    """``extra="forbid"`` on ``_DisplayFields`` prevents typos like
    ``_display_summery`` from silently dropping the field. The wrap
    fails loudly during testing rather than degrading at runtime."""

    from pydantic import BaseModel, ValidationError

    from agent_runtime.capabilities.middleware.display_metadata import (
        wrap_args_schema,
    )

    class OriginalArgs(BaseModel):
        query: str

    Wrapped = wrap_args_schema(OriginalArgs)
    try:
        Wrapped(query="x", _display_summery="typo")  # type: ignore[call-arg]
    except ValidationError:
        pass
    else:
        raise AssertionError("expected ValidationError on typoed display key")


def test_strip_display_splits_args_and_returns_both_keys() -> None:
    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_SUMMARY_KEY,
        DISPLAY_TITLE_KEY,
        strip_display,
    )

    real, display = strip_display(
        {
            "query": "Q1 launch",
            DISPLAY_TITLE_KEY: "Looking up Q1 launch tickets",
            DISPLAY_SUMMARY_KEY: "Risk-tagged tickets opened in Q1",
        }
    )
    assert real == {"query": "Q1 launch"}
    assert display == {
        DISPLAY_TITLE_KEY: "Looking up Q1 launch tickets",
        DISPLAY_SUMMARY_KEY: "Risk-tagged tickets opened in Q1",
    }


def test_strip_display_backfills_missing_display_keys() -> None:
    """The wrapped tool's invoke always reads both display keys; backfill
    so the caller doesn't need a separate guard."""

    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_SUMMARY_KEY,
        DISPLAY_TITLE_KEY,
        strip_display,
    )

    real, display = strip_display({"query": "x"})
    assert real == {"query": "x"}
    assert display == {DISPLAY_TITLE_KEY: None, DISPLAY_SUMMARY_KEY: None}


def test_strip_display_tolerates_none_input() -> None:
    """Defensive: misshaped LangChain invocations may pass ``None``."""

    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_SUMMARY_KEY,
        DISPLAY_TITLE_KEY,
        strip_display,
    )

    real, display = strip_display(None)
    assert real == {}
    assert display == {DISPLAY_TITLE_KEY: None, DISPLAY_SUMMARY_KEY: None}


def test_strip_display_drops_non_string_display_values() -> None:
    """Pydantic should never let a non-string ``_display_*`` reach the
    wire, but defensive: ``strip_display`` coerces non-strings to None
    so the projector never has to type-check."""

    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_TITLE_KEY,
        strip_display,
    )

    _, display = strip_display({DISPLAY_TITLE_KEY: 42})
    assert display[DISPLAY_TITLE_KEY] is None


# --- agent_display_from_payload ------------------------------------------


def test_agent_display_from_payload_reads_args_keys() -> None:
    """The agent's ``_display_*`` lands at ``payload.args._display_*``
    (same shape for regular tools and the ``call_mcp_tool`` dispatcher)."""

    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_SUMMARY_KEY,
        DISPLAY_TITLE_KEY,
        agent_display_from_payload,
    )

    title, summary = agent_display_from_payload(
        {
            "tool_name": "search_docs",
            "args": {
                "query": "Q1",
                DISPLAY_TITLE_KEY: "Looking up Q1 docs",
                DISPLAY_SUMMARY_KEY: "Recent launch documents",
            },
        }
    )
    assert title == "Looking up Q1 docs"
    assert summary == "Recent launch documents"


def test_agent_display_from_payload_returns_none_when_args_missing() -> None:
    from agent_runtime.capabilities.middleware.display_metadata import (
        agent_display_from_payload,
    )

    title, summary = agent_display_from_payload(
        {"tool_name": "search_docs"}  # no ``args``
    )
    assert title is None and summary is None


def test_agent_display_from_payload_treats_empty_strings_as_missing() -> None:
    """An empty title would render an empty card; treat as absent so the
    Tier-2 fallback wins."""

    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_TITLE_KEY,
        agent_display_from_payload,
    )

    title, _ = agent_display_from_payload(
        {"args": {"query": "x", DISPLAY_TITLE_KEY: "   "}}
    )
    assert title is None


def test_agent_display_from_payload_strips_whitespace() -> None:
    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_SUMMARY_KEY,
        agent_display_from_payload,
    )

    _, summary = agent_display_from_payload(
        {"args": {DISPLAY_SUMMARY_KEY: "  Q1 risks  "}}
    )
    assert summary == "Q1 risks"


def test_agent_display_from_payload_dispatcher_args_top_level() -> None:
    """For ``call_mcp_tool`` dispatcher events the agent puts
    ``_display_*`` at the TOP of args, not nested in ``args.arguments``.
    Pin this explicitly — Phase 3.B's dispatcher wrap depends on it."""

    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_TITLE_KEY,
        agent_display_from_payload,
    )

    title, _ = agent_display_from_payload(
        {
            "tool_name": "call_mcp_tool",
            "args": {
                "server_name": "linear",
                "tool_name": "list_issues",
                "arguments": {"query": "Q1"},
                DISPLAY_TITLE_KEY: "Looking up Q1 Linear tickets",
            },
        }
    )
    assert title == "Looking up Q1 Linear tickets"


# --- Phase 3.B tool-binding wrap -----------------------------------------


def test_wrap_tool_with_display_extends_structured_tool_schema() -> None:
    """``StructuredTool`` is the dominant shape (every custom dataclass
    adapter goes through ``factory._structured_tool``). The wrap copies
    the tool with an extended args_schema; existing fields stay required."""

    import asyncio

    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel

    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_TITLE_KEY,
        wrap_tool_with_display,
    )

    class FakeArgs(BaseModel):
        query: str

    received: list[dict[str, object]] = []

    async def _adapter(**kwargs: object) -> str:
        received.append(kwargs)
        return f"got query={kwargs['query']!r}"

    tool = StructuredTool.from_function(
        coroutine=_adapter,
        name="search_docs",
        description="Search the document corpus.",
        args_schema=FakeArgs,
    )

    wrapped = wrap_tool_with_display(tool)

    # New args_schema accepts both the original required field AND the
    # display fields (optional, defaulted to None).
    schema = wrapped.args_schema.model_json_schema()
    assert "query" in schema["properties"]
    assert DISPLAY_TITLE_KEY in schema["properties"]

    # Underlying adapter never sees ``_display_*`` — the wrap strips first.
    result = asyncio.run(
        wrapped.ainvoke(
            {
                "query": "Q1 launch",
                DISPLAY_TITLE_KEY: "Looking up Q1 launch tickets",
            }
        )
    )
    assert result == "got query='Q1 launch'"
    assert received == [{"query": "Q1 launch"}]


def test_wrap_tool_with_display_idempotent_via_schema_marker() -> None:
    """A tool whose args_schema already carries the ``__display_wrapped__``
    marker is returned unchanged. Pins the contract that
    ``build_deep_agent`` can call the wrap twice safely (e.g. subagent
    re-binding the supervisor's tools)."""

    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel

    from agent_runtime.capabilities.middleware.display_metadata import (
        wrap_tool_with_display,
    )

    class FakeArgs(BaseModel):
        query: str

    async def _adapter(**kwargs: object) -> str:
        return ""

    tool = StructuredTool.from_function(
        coroutine=_adapter,
        name="t",
        description="d",
        args_schema=FakeArgs,
    )

    once = wrap_tool_with_display(tool)
    twice = wrap_tool_with_display(once)
    assert once is twice  # idempotent — second call short-circuits


def test_wrap_tool_with_display_returns_unknown_shape_unchanged() -> None:
    """Anything that isn't a recognised LangChain tool is returned as-is.
    This is the safety contract — never break a working tool."""

    from agent_runtime.capabilities.middleware.display_metadata import (
        wrap_tool_with_display,
    )

    class _Bare:
        name = "bare"

    plain = _Bare()
    assert wrap_tool_with_display(plain) is plain
    assert wrap_tool_with_display("not a tool") == "not a tool"
    assert wrap_tool_with_display(None) is None


def test_wrap_tool_with_display_wraps_base_tool_via_delegation() -> None:
    """Generic ``BaseTool`` subclasses (e.g. ``DuckDuckGoSearchResults``)
    don't expose ``func`` / ``coroutine`` for ``model_copy`` to rewrite.
    The wrap creates a NEW ``StructuredTool`` whose coroutine delegates
    to the original via ``ainvoke`` with a full LangChain ``ToolCall``
    envelope (:class:`_DispatchEnvelope`) — required when the inner
    tool's args_schema declares ``InjectedToolCallId`` (citation-capturing
    wrapper, every MCP tool). PRD §3 Part A.
    """

    import asyncio

    from langchain_core.messages import ToolMessage
    from langchain_core.tools import BaseTool
    from pydantic import BaseModel

    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_TITLE_KEY,
        wrap_tool_with_display,
    )

    class FakeArgs(BaseModel):
        query: str

    received: list[dict[str, object]] = []

    class FakeBaseTool(BaseTool):
        name: str = "fake"
        description: str = "Fake tool that records what it received."
        args_schema: type[BaseModel] = FakeArgs

        def _run(self, query: str) -> str:  # type: ignore[override]
            return f"sync got query={query!r}"

        async def _arun(self, query: str) -> str:  # type: ignore[override]
            received.append({"query": query})
            return f"got query={query!r}"

    tool = FakeBaseTool()

    wrapped = wrap_tool_with_display(tool)
    # Wrap returns a NEW StructuredTool — different instance, same name.
    assert wrapped is not tool
    assert wrapped.name == "fake"

    schema = wrapped.args_schema.model_json_schema()
    assert DISPLAY_TITLE_KEY in schema["properties"]

    # Production contract: LangGraph dispatches the wrapped tool via a
    # ``ToolCall`` envelope. Because the wrap's delegating coroutine calls
    # ``inner.ainvoke(envelope)``, ``BaseTool.ainvoke`` returns a
    # ``ToolMessage`` carrying the raw return as ``content``. Tests must
    # match this contract (we never bypass it in production).
    result = asyncio.run(
        wrapped.ainvoke(
            {
                "args": {"query": "x", DISPLAY_TITLE_KEY: "Custom Title"},
                "name": "fake",
                "id": "call_test_1",
                "type": "tool_call",
            }
        )
    )
    assert isinstance(result, ToolMessage)
    assert result.content == "got query='x'"
    assert received == [{"query": "x"}]


def test_wrap_tools_with_display_returns_a_new_list_per_tool() -> None:
    """``wrap_tools_with_display`` is the entry point ``build_deep_agent``
    calls. It must return a new list with each entry wrapped (or returned
    unchanged for unknown shapes)."""

    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel

    from agent_runtime.capabilities.middleware.display_metadata import (
        wrap_tools_with_display,
    )

    class FakeArgs(BaseModel):
        query: str

    async def _adapter(**kwargs: object) -> str:
        return ""

    tool_a = StructuredTool.from_function(
        coroutine=_adapter, name="a", description="a", args_schema=FakeArgs
    )
    tool_b = StructuredTool.from_function(
        coroutine=_adapter, name="b", description="b", args_schema=FakeArgs
    )

    wrapped = wrap_tools_with_display([tool_a, tool_b])
    assert isinstance(wrapped, list)
    assert len(wrapped) == 2
    # Each is a fresh wrapped copy.
    assert wrapped[0] is not tool_a
    assert wrapped[1] is not tool_b
    # Both args_schemas carry the marker.
    assert getattr(wrapped[0].args_schema, "__display_wrapped__", False) is True
    assert getattr(wrapped[1].args_schema, "__display_wrapped__", False) is True


def test_wrap_tool_preserves_sync_func_when_present() -> None:
    """A tool with a sync ``func`` (rare in our codebase but valid in
    LangChain) gets its sync path wrapped too — ``_display_*`` never
    reaches the underlying function."""

    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel

    from agent_runtime.capabilities.middleware.display_metadata import (
        DISPLAY_TITLE_KEY,
        wrap_tool_with_display,
    )

    class FakeArgs(BaseModel):
        query: str

    received: list[dict[str, object]] = []

    def _sync_adapter(**kwargs: object) -> str:
        received.append(kwargs)
        return ""

    tool = StructuredTool.from_function(
        func=_sync_adapter,
        name="sync_tool",
        description="d",
        args_schema=FakeArgs,
    )

    wrapped = wrap_tool_with_display(tool)
    wrapped.invoke({"query": "x", DISPLAY_TITLE_KEY: "ignored by underlying func"})

    assert received == [{"query": "x"}]


def test_wrap_tool_does_not_break_invocation_when_agent_omits_display() -> None:
    """Agent leaves ``_display_*`` as ``None`` — the wrap still strips
    them out (they were defaulted to ``None`` by the wrapped schema) and
    the underlying tool runs normally. Pin the no-op path."""

    import asyncio

    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel

    from agent_runtime.capabilities.middleware.display_metadata import (
        wrap_tool_with_display,
    )

    class FakeArgs(BaseModel):
        query: str

    received: list[dict[str, object]] = []

    async def _adapter(**kwargs: object) -> str:
        received.append(kwargs)
        return "ok"

    tool = StructuredTool.from_function(
        coroutine=_adapter,
        name="t",
        description="d",
        args_schema=FakeArgs,
    )

    wrapped = wrap_tool_with_display(tool)
    asyncio.run(wrapped.ainvoke({"query": "x"}))
    # Underlying adapter received only ``query`` — no display keys.
    assert received == [{"query": "x"}]


# --- PRD §3 Part A — InjectedToolCallId regression guards -----------------


def test_delegation_wrap_forwards_tool_call_id_through_envelope() -> None:
    """When the inner ``BaseTool`` declares ``InjectedToolCallId`` on its
    args_schema (citation-capturing wrapper, every MCP tool), the
    delegating wrap must:

    1. Inherit the annotation via :func:`wrap_args_schema` so LangChain
       injects the calling ``tool_call_id`` into the wrapper's coroutine.
    2. Re-emit a full ``ToolCall`` envelope on ``inner.ainvoke(...)`` so
       LangChain's injection plumbing supplies the id to the inner.

    Before PRD §3 Part A, the delegating coroutine called
    ``inner.ainvoke(plain_args_dict)`` which LangChain refuses with
    ``ValueError("When tool includes an InjectedToolCallId argument,
    tool must always be invoked with a full model ToolCall ...")``.
    This regression bricked every ``web_search`` and every post-auth
    MCP tool call. Pin the fix.
    """

    import asyncio
    from typing import Annotated

    from langchain_core.messages import ToolMessage
    from langchain_core.tools import BaseTool, InjectedToolCallId
    from pydantic import BaseModel

    from agent_runtime.capabilities.middleware.display_metadata import (
        wrap_tool_with_display,
    )

    class ArgsWithInjectedId(BaseModel):
        query: str
        # Mirrors ``CitationCapturingTool`` and ``McpToolCallRequest`` —
        # both declare ``Annotated[str, InjectedToolCallId]`` on their
        # ``args_schema`` so LangChain feeds the calling tool_call_id
        # into their dispatch.
        tool_call_id: Annotated[str, InjectedToolCallId] = ""

    observed: list[dict[str, object]] = []

    class InnerToolThatNeedsToolCallId(BaseTool):
        name: str = "inner_with_injected_id"
        description: str = "Fake inner that records the injected id."
        args_schema: type[BaseModel] = ArgsWithInjectedId

        def _run(  # type: ignore[override]
            self,
            query: str,
            tool_call_id: str = "",
        ) -> str:
            return f"sync {query=!r} {tool_call_id=!r}"

        async def _arun(  # type: ignore[override]
            self,
            query: str,
            tool_call_id: str = "",
        ) -> str:
            observed.append({"query": query, "tool_call_id": tool_call_id})
            return f"got {query=!r} {tool_call_id=!r}"

    wrapped = wrap_tool_with_display(InnerToolThatNeedsToolCallId())

    # Production contract: LangGraph dispatches via ``ToolCall`` envelope.
    # Because the inner schema declares ``InjectedToolCallId`` and
    # ``wrap_args_schema`` inherits the annotation, the wrapper schema is
    # also envelope-only — that contract is exactly what we want to lock in.
    result = asyncio.run(
        wrapped.ainvoke(
            {
                "args": {"query": "x"},
                "name": "inner_with_injected_id",
                "id": "call_test_envelope",
                "type": "tool_call",
            }
        )
    )

    assert isinstance(result, ToolMessage)
    assert result.content == "got query='x' tool_call_id='call_test_envelope'"
    # The injected id reached the inner — proves the wrap forwards through
    # ``_DispatchEnvelope`` instead of calling ``ainvoke`` with a plain dict.
    assert observed == [{"query": "x", "tool_call_id": "call_test_envelope"}]


def test_delegation_wrap_forwards_envelope_to_inner_without_injected_id() -> None:
    """When the inner ``BaseTool`` does NOT declare ``InjectedToolCallId``
    on its args_schema (e.g. ``DuckDuckGoSearchResults``), the wrap still
    uses :class:`_DispatchEnvelope`. LangChain's tool dispatch treats the
    envelope's ``id`` as metadata and extracts ``args`` normally — the
    inner is invoked exactly as it would be with a plain args dict.

    This is what guarantees the envelope path is safe for ALL inner
    shapes, not just those with the annotation. PRD §3 Part A.
    """

    import asyncio

    from langchain_core.messages import ToolMessage
    from langchain_core.tools import BaseTool
    from pydantic import BaseModel

    from agent_runtime.capabilities.middleware.display_metadata import (
        wrap_tool_with_display,
    )

    class PlainArgs(BaseModel):
        query: str

    observed: list[dict[str, object]] = []

    class PlainInnerTool(BaseTool):
        name: str = "plain"
        description: str = "Fake inner with no InjectedToolCallId."
        args_schema: type[BaseModel] = PlainArgs

        def _run(self, query: str) -> str:  # type: ignore[override]
            return f"sync {query=!r}"

        async def _arun(self, query: str) -> str:  # type: ignore[override]
            observed.append({"query": query})
            return f"got {query=!r}"

    wrapped = wrap_tool_with_display(PlainInnerTool())

    # The wrap ALWAYS forwards via envelope internally, so the value
    # returned by ``inner.ainvoke(envelope)`` is a ``ToolMessage`` — the
    # outer ``BaseTool.ainvoke`` passes it through. This holds whether
    # the outer is invoked with a plain dict (tests) or an envelope
    # (production / LangGraph). The contract is: the wrap surfaces a
    # ``ToolMessage`` whose ``.content`` is the inner's raw return.
    plain_result = asyncio.run(wrapped.ainvoke({"query": "x"}))
    assert isinstance(plain_result, ToolMessage)
    assert plain_result.content == "got query='x'"

    envelope_result = asyncio.run(
        wrapped.ainvoke(
            {
                "args": {"query": "y"},
                "name": "plain",
                "id": "call_xyz",
                "type": "tool_call",
            }
        )
    )
    assert isinstance(envelope_result, ToolMessage)
    assert envelope_result.content == "got query='y'"
    # Inner observed both invocations exactly once each, in order.
    assert observed == [{"query": "x"}, {"query": "y"}]


def test_dispatch_envelope_keys_are_canonical() -> None:
    """Single source of truth for the LangChain ``ToolCall`` envelope shape
    used by the wrap. If a future LangChain bump changes any of these
    keys, this test fails first and the constants update is one place."""

    from agent_runtime.capabilities.middleware.display_metadata import (
        _DispatchEnvelope,
    )

    envelope = _DispatchEnvelope.build(
        args={"k": "v"},
        name="some_tool",
        tool_call_id="call_abc",
    )

    assert envelope == {
        "args": {"k": "v"},
        "name": "some_tool",
        "id": "call_abc",
        "type": "tool_call",
    }
    # Constants exposed for fixtures + downstream tooling.
    assert _DispatchEnvelope.KEY_ARGS == "args"
    assert _DispatchEnvelope.KEY_NAME == "name"
    assert _DispatchEnvelope.KEY_ID == "id"
    assert _DispatchEnvelope.KEY_TYPE == "type"
    assert _DispatchEnvelope.TYPE_TOOL_CALL == "tool_call"
