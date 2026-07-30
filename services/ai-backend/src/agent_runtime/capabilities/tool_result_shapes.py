"""Unwrap a tool result to the payloads worth reading — the read-side SSOT.

This is the deliberate twin of :class:`~agent_runtime.capabilities.tool_result_notes.ToolResultNote`.
That module answers the WRITE-side question ("where in this result does readable
text live, so I can append a note?"). This one answers the READ-side question
("what parts of this result should a reader scan?"). Both exist because a tool
result is not a string: it may be a LangChain ``content_and_artifact`` tuple, a
bare list, an MCP ``CallToolResult`` envelope (dict or object), a Pydantic model,
or a plain dict.

WHY THIS MODULE EXISTS
======================
It was written after a silent production bug. ``ToolResultNote`` correctly
unwrapped ``content_and_artifact`` tuples; :class:`CitationProjector` re-derived
its own walk and did not. The built-in ``web_search`` tool
(``DuckDuckGoSearchResults``) declares ``response_format="content_and_artifact"``
unconditionally, so every web search reached the projector as a 2-tuple, fell
through its dict/list checks, and registered ZERO sources. Because the note half
still worked, ``[[N]]`` citation chips resolved perfectly while the Sources rail
sat empty — the two halves of one wrapper disagreeing about one shape, which
reads to a user as "citations work, Sources is broken".

The fix is not a tuple branch in the projector; it is having exactly ONE place
that knows tool-result envelope shapes, so a reader cannot be taught a shape the
writer knows (or vice versa). Any NEW consumer that needs to inspect a tool
result should call :meth:`ToolResultPayloads.candidates` rather than adding a
fourth shape walk. Any new envelope shape is taught here, once, and every
consumer gains it.

Never raises: an unrecognised shape yields nothing rather than failing the tool
call. Reading a result for enrichment must never break the result itself.
"""

from __future__ import annotations

from typing import Any


class ToolResultPayloads:
    """Normalise any tool return into an ordered list of payloads to scan."""

    # MCP / LangChain envelope keys.
    CONTENT_KEY = "content"
    # Pydantic v2 dump hook, used by MCP client objects (``CallToolResult``).
    MODEL_DUMP = "model_dump"
    # Guard against a pathological deeply-nested envelope; real shapes are 1-2
    # layers (tuple → list, or object → dict → content list).
    MAX_DEPTH = 4

    @classmethod
    def candidates(cls, result: object) -> tuple[object, ...]:
        """Return payloads to scan, in priority order (most-specific first).

        Ordering matters to callers that stop at the first payload yielding a
        hit, so that a ``content_and_artifact`` result registers its content
        once rather than registering content AND the artifact copy of the same
        rows. Content precedes artifact; the original result is always included
        so shape-specific extractors downstream still see the envelope itself.
        """

        out: list[object] = []
        cls._collect(result, out, depth=0)
        # Stable de-duplication by identity — an envelope whose content IS the
        # object we already queued must not be scanned twice.
        seen: list[int] = []
        unique: list[object] = []
        for item in out:
            if id(item) in seen:
                continue
            seen.append(id(item))
            unique.append(item)
        return tuple(unique)

    @classmethod
    def _collect(cls, result: object, out: list[object], *, depth: int) -> None:
        if depth > cls.MAX_DEPTH:
            return

        # LangChain ``content_and_artifact``: ``(content, artifact)``. Walk every
        # element, content first — with ``output_format="string"`` the content is
        # an opaque blob while the artifact still holds the structured rows, so a
        # content-only unwrap would silently under-report.
        if isinstance(result, tuple):
            for element in result:
                cls._collect(element, out, depth=depth + 1)
            return

        if isinstance(result, (list, str, bytes)):
            out.append(result)
            return

        if isinstance(result, dict):
            # The envelope itself first (its `results` / `resource` / `content`
            # keys are meaningful to shape extractors), then its content array.
            out.append(result)
            content = result.get(cls.CONTENT_KEY)
            if isinstance(content, (list, tuple, dict)):
                cls._collect(content, out, depth=depth + 1)
            return

        # An MCP ``CallToolResult`` (or any Pydantic model) arrives as an object,
        # not a dict. Dump it and walk the dict form so the same content-array
        # logic applies without this module importing the MCP client.
        dumped = cls._model_dump(result)
        if dumped is not None:
            cls._collect(dumped, out, depth=depth + 1)
            return

        # Last resort: a plain object exposing `.content` (duck-typed envelope).
        # Re-wrap it as ``{"content": ...}`` rather than yielding the bare list:
        # the key is not decoration, it tells the reader these are content BLOCKS
        # (``{"type": "resource", ...}``) and not a results list. Unwrapping it
        # away would silently route an MCP-shaped array to the wrong extractor.
        content = getattr(result, cls.CONTENT_KEY, None)
        if isinstance(content, (list, tuple, dict)):
            cls._collect({cls.CONTENT_KEY: content}, out, depth=depth + 1)

    @classmethod
    def _model_dump(cls, result: object) -> dict[str, Any] | None:
        """Return ``result.model_dump()`` when it is a Pydantic-like model."""

        dump = getattr(result, cls.MODEL_DUMP, None)
        if not callable(dump):
            return None
        try:
            dumped = dump()
        except Exception:  # noqa: BLE001 - enrichment must never raise
            return None
        return dumped if isinstance(dumped, dict) else None


__all__ = ("ToolResultPayloads",)
