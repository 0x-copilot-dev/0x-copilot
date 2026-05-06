"""Shared content-block readers for provider citation adapters.

LangChain's ``AIMessageChunk`` exposes content under several shapes:

- ``chunk.content`` — either a ``str`` (older / simpler streams) or a
  list of dict-like content blocks (Anthropic, OpenAI Responses).
- ``chunk.message.content`` — same shape, but on stream-event envelopes
  that wrap the message under a ``message`` attribute / mapping key.
- ``chunk.response_metadata`` / ``chunk.additional_kwargs`` — provider-
  side metadata (Gemini grounding metadata lives here).

Each provider adapter cares about a different slice of this. The
readers below give every adapter the same tolerant entry point so the
adapters themselves stay focused on their provider's citation primitives
and don't re-implement attribute / dict probing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


class ChunkContentReader:
    """Read ``content`` from a LangChain ``AIMessageChunk``-like object.

    Returns a list of content-block mappings, or an empty list when the
    chunk has no list-shaped content (a plain-string ``content`` returns
    ``[]`` because adapters that need string-only deltas pull them via
    ``StreamMessageParser.message_delta`` instead).
    """

    @classmethod
    def content_blocks(cls, chunk: object) -> list[Mapping[str, object]]:
        value = cls._extract_content(chunk)
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return []
        blocks: list[Mapping[str, object]] = []
        for item in value:
            if isinstance(item, Mapping):
                blocks.append(item)
        return blocks

    @classmethod
    def _extract_content(cls, chunk: object) -> object:
        # Plain AIMessageChunk-style attribute first.
        direct = getattr(chunk, "content", None)
        if direct is not None:
            return direct
        # Mapping-shaped chunk (event-stream envelopes).
        if isinstance(chunk, Mapping):
            mapped = chunk.get("content")
            if mapped is not None:
                return mapped
            inner = chunk.get("message")
            if inner is not None:
                return cls._extract_content(inner)
        # Object with a nested ``message`` attribute carrying the content.
        nested = getattr(chunk, "message", None)
        if nested is not None and nested is not chunk:
            return cls._extract_content(nested)
        return None


class ChunkMetadataReader:
    """Read provider-side metadata bags from a LangChain stream chunk.

    Gemini's ``grounding_metadata`` is the immediate consumer; the
    reader stays generic so future provider hooks can reuse it.
    """

    @classmethod
    def metadata(cls, chunk: object, key: str) -> Mapping[str, object] | None:
        for bag_name in ("response_metadata", "additional_kwargs"):
            bag = cls._read_bag(chunk, bag_name)
            if bag is None:
                continue
            value = bag.get(key)
            if isinstance(value, Mapping):
                return value
        return None

    @classmethod
    def _read_bag(cls, chunk: object, name: str) -> Mapping[str, object] | None:
        # Attribute access (typed AIMessageChunk).
        attr = getattr(chunk, name, None)
        if isinstance(attr, Mapping):
            return attr
        # Mapping access (dict-shaped chunks).
        if isinstance(chunk, Mapping):
            value = chunk.get(name)
            if isinstance(value, Mapping):
                return value
            inner = chunk.get("message")
            if inner is not None:
                return cls._read_bag(inner, name)
        nested = getattr(chunk, "message", None)
        if nested is not None and nested is not chunk:
            return cls._read_bag(nested, name)
        return None


__all__ = ("ChunkContentReader", "ChunkMetadataReader")
