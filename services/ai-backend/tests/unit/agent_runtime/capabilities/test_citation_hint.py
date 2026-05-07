"""Unit tests for ``_CitationHint`` — append-to-result behavior.

PR 1.1-rev2 — model-declared citation pointers. The hint is appended
to tool results so the model has a stable ``[[N]]`` pointer to embed
in its prose. These tests pin the three result shapes (string, list,
dict) and the passthrough for unknown shapes.
"""

from __future__ import annotations

from agent_runtime.capabilities.citation_capturing_tool import _CitationHint


class _Values:
    ORDINAL = 7
    TOOL_NAME = "linear.list_issues"


class TestCitationHintRender:
    def test_render_format(self) -> None:
        rendered = _CitationHint.render(
            ordinal=_Values.ORDINAL,
            tool_name=_Values.TOOL_NAME,
        )
        assert rendered == (
            f"[Tool call #{_Values.ORDINAL} — {_Values.TOOL_NAME} — "
            f"cite as [[{_Values.ORDINAL}]] when referencing this result.]"
        )


class TestAppendToString:
    def test_appends_separator_then_hint(self) -> None:
        out = _CitationHint.append_to(
            "Some result text",
            ordinal=_Values.ORDINAL,
            tool_name=_Values.TOOL_NAME,
        )
        assert isinstance(out, str)
        assert out.startswith("Some result text")
        assert (
            _CitationHint.render(ordinal=_Values.ORDINAL, tool_name=_Values.TOOL_NAME)
            in out
        )

    def test_empty_string_still_appends(self) -> None:
        out = _CitationHint.append_to(
            "", ordinal=_Values.ORDINAL, tool_name=_Values.TOOL_NAME
        )
        assert (
            _CitationHint.render(ordinal=_Values.ORDINAL, tool_name=_Values.TOOL_NAME)
            in out
        )


class TestAppendToList:
    def test_appends_hint_to_last_string_entry(self) -> None:
        out = _CitationHint.append_to(
            ["alpha", "beta"],
            ordinal=_Values.ORDINAL,
            tool_name=_Values.TOOL_NAME,
        )
        assert isinstance(out, list)
        assert len(out) == 2
        assert out[0] == "alpha"
        assert out[1].startswith("beta")
        assert "[[7]]" in out[1]

    def test_no_string_entry_appends_new_entry(self) -> None:
        out = _CitationHint.append_to(
            [{"foo": "bar"}],
            ordinal=_Values.ORDINAL,
            tool_name=_Values.TOOL_NAME,
        )
        assert isinstance(out, list)
        assert len(out) == 2
        assert isinstance(out[1], str)
        assert "[[7]]" in out[1]


class TestAppendToDict:
    def test_mcp_content_array_gets_text_block(self) -> None:
        out = _CitationHint.append_to(
            {"content": [{"type": "text", "text": "issue body"}]},
            ordinal=_Values.ORDINAL,
            tool_name=_Values.TOOL_NAME,
        )
        assert isinstance(out, dict)
        content = out["content"]
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[1] == {
            "type": "text",
            "text": _CitationHint.render(
                ordinal=_Values.ORDINAL, tool_name=_Values.TOOL_NAME
            ),
        }

    def test_dict_without_content_gets_top_level_hint_field(self) -> None:
        out = _CitationHint.append_to(
            {"data": {"issues": []}},
            ordinal=_Values.ORDINAL,
            tool_name=_Values.TOOL_NAME,
        )
        assert isinstance(out, dict)
        assert out["data"] == {"issues": []}
        assert out[_CitationHint.DICT_HINT_KEY] == _CitationHint.render(
            ordinal=_Values.ORDINAL, tool_name=_Values.TOOL_NAME
        )

    def test_does_not_mutate_input_dict(self) -> None:
        original = {"content": [{"type": "text", "text": "x"}]}
        out = _CitationHint.append_to(
            original,
            ordinal=_Values.ORDINAL,
            tool_name=_Values.TOOL_NAME,
        )
        # The input dict's content array should still have just one
        # entry — _CitationHint must copy before extending.
        assert len(original["content"]) == 1
        assert isinstance(out, dict)
        assert len(out["content"]) == 2


class TestAppendToOtherShapes:
    def test_returns_unchanged_for_unknown_shape(self) -> None:
        sentinel = object()
        out = _CitationHint.append_to(
            sentinel, ordinal=_Values.ORDINAL, tool_name=_Values.TOOL_NAME
        )
        assert out is sentinel

    def test_returns_unchanged_for_int(self) -> None:
        out = _CitationHint.append_to(
            42, ordinal=_Values.ORDINAL, tool_name=_Values.TOOL_NAME
        )
        assert out == 42
