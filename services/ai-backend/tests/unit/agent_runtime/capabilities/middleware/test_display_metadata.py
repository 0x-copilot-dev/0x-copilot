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
