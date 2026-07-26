"""Shape-preserving append of a model-visible note onto a tool result.

Two wrappers need to hand the model a short bracketed note alongside a tool's
own output — the citation pointer (``[Tool call #N …]``) and the tool-budget
remaining-calls notice. A tool result is not just a string: it may be a
LangChain ``content_and_artifact`` tuple, a list, an MCP ``CallToolResult``
envelope, or a plain dict, and each has a different "where does readable text
live" answer. That walk is the tricky part, and it is identical for every
note — so it lives here once rather than being re-derived per caller.

Never raises: an unrecognised shape is returned unchanged. A note is an
annotation, and failing to annotate must never fail the tool call itself.
"""

from __future__ import annotations

from typing import Any


class ToolResultNote:
    """Appends ``note`` to whatever part of a tool result the model reads."""

    SEPARATOR = "\n\n"
    # MCP standard envelope keys.
    MCP_CONTENT_KEY = "content"
    MCP_BLOCK_TYPE_KEY = "type"
    MCP_TEXT_VALUE = "text"

    @classmethod
    def append(cls, result: object, *, note: str, dict_key: str) -> object:
        """Return ``result`` with ``note`` appended, preserving its shape.

        ``dict_key`` is the top-level key used for a generic dict result that
        carries no MCP ``content`` array to extend; each caller passes its own
        so two notes on one result do not collide.
        """

        suffix = cls.SEPARATOR + note
        if isinstance(result, str):
            return result + suffix
        if isinstance(result, tuple):
            return cls._append_to_tuple(result, suffix=suffix)
        if isinstance(result, list):
            return cls._append_to_list(result, suffix=suffix)
        if isinstance(result, dict):
            return cls._append_to_dict(result, note=note, dict_key=dict_key)
        return result

    @classmethod
    def _append_to_tuple(cls, result: tuple[Any, ...], *, suffix: str) -> object:
        # LangChain content_and_artifact shape: the head is the string the
        # model reads; the tail is the structured artifact we must not
        # modify (DuckDuckGo and most web-search wrappers use this).
        if len(result) >= 1 and isinstance(result[0], str):
            return (result[0] + suffix, *result[1:])
        updated: list[Any] = list(result)
        for idx in range(len(updated) - 1, -1, -1):
            if isinstance(updated[idx], str):
                updated[idx] = updated[idx] + suffix
                return tuple(updated)
        # No string entry at all — prepend so the model still sees the note.
        updated.insert(0, suffix.lstrip())
        return tuple(updated)

    @classmethod
    def _append_to_list(cls, result: list[Any], *, suffix: str) -> list[Any]:
        updated = list(result)
        for idx in range(len(updated) - 1, -1, -1):
            if isinstance(updated[idx], str):
                updated[idx] = updated[idx] + suffix
                return updated
        updated.append(suffix.lstrip())
        return updated

    @classmethod
    def _append_to_dict(
        cls, result: dict[str, Any], *, note: str, dict_key: str
    ) -> dict[str, Any]:
        updated = dict(result)
        content = updated.get(cls.MCP_CONTENT_KEY)
        if isinstance(content, list):
            # MCP CallToolResult envelope — add a TextContent block so the
            # note appears in the same array the server's own data uses.
            updated[cls.MCP_CONTENT_KEY] = [
                *content,
                {cls.MCP_BLOCK_TYPE_KEY: cls.MCP_TEXT_VALUE, cls.MCP_TEXT_VALUE: note},
            ]
            return updated
        # Generic dict (internal API, custom tool) — a dedicated top-level key
        # so JSON-rendering consumers still expose it.
        updated[dict_key] = note
        return updated


__all__ = ("ToolResultNote",)
