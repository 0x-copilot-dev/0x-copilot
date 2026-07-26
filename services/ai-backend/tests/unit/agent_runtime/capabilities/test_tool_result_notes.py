"""Shape-preserving append of a model-visible note onto a tool result.

A tool result is not always a string: web-search wrappers return LangChain
``content_and_artifact`` tuples, MCP servers return a ``content`` block array,
and internal tools return plain dicts. The note has to land where the model
actually reads, without disturbing the structured payload beside it. This is
shared by the citation pointer and the tool-budget notice, so a regression
here silently breaks both.
"""

from __future__ import annotations

from agent_runtime.capabilities.tool_result_notes import ToolResultNote

NOTE = "[a note]"
KEY = "_test_note"


def _append(result: object) -> object:
    return ToolResultNote.append(result, note=NOTE, dict_key=KEY)


class TestStringAndSequenceShapes:
    def test_string_is_appended(self) -> None:
        assert _append("body") == f"body\n\n{NOTE}"

    def test_tuple_head_is_annotated_and_artifact_untouched(self) -> None:
        # LangChain content_and_artifact: the head is what the model reads;
        # the tail is structured data a downstream consumer parses.
        artifact = [{"link": "https://example.test"}]
        result = _append(("body", artifact))
        assert isinstance(result, tuple)
        assert result[0] == f"body\n\n{NOTE}"
        assert result[1] is artifact

    def test_tuple_without_a_string_head_uses_the_last_string(self) -> None:
        result = _append((123, "tail"))
        assert result == (123, f"tail\n\n{NOTE}")

    def test_tuple_with_no_string_still_carries_the_note(self) -> None:
        result = _append((1, 2))
        assert isinstance(result, tuple)
        assert result[0] == NOTE

    def test_list_annotates_the_last_string(self) -> None:
        assert _append(["a", "b"]) == ["a", f"b\n\n{NOTE}"]

    def test_list_with_no_string_appends_the_note(self) -> None:
        assert _append([1, 2]) == [1, 2, NOTE]


class TestDictShapes:
    def test_mcp_envelope_gets_a_text_block(self) -> None:
        result = _append({"content": [{"type": "text", "text": "body"}]})
        assert isinstance(result, dict)
        assert result["content"][-1] == {"type": "text", "text": NOTE}
        # The server's own block is preserved verbatim.
        assert result["content"][0] == {"type": "text", "text": "body"}

    def test_generic_dict_gets_a_dedicated_key(self) -> None:
        result = _append({"rows": [1, 2]})
        assert isinstance(result, dict)
        assert result[KEY] == NOTE
        assert result["rows"] == [1, 2]

    def test_two_notes_with_different_keys_coexist(self) -> None:
        """The citation hint and the budget notice both ride on one result."""

        once = ToolResultNote.append({"rows": []}, note="first", dict_key="_a")
        twice = ToolResultNote.append(once, note="second", dict_key="_b")
        assert twice == {"rows": [], "_a": "first", "_b": "second"}

    def test_input_is_not_mutated(self) -> None:
        original = {"rows": [1]}
        _append(original)
        assert original == {"rows": [1]}


class TestUnknownShapes:
    def test_unrecognised_shape_passes_through(self) -> None:
        # Annotating is best-effort; an unknown shape must not raise.
        sentinel = object()
        assert _append(sentinel) is sentinel

    def test_none_passes_through(self) -> None:
        assert _append(None) is None
