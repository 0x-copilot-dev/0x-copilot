"""Outcome classification for MCP tool-call response envelopes.

The MCP spec treats ``isError: true`` on a successful HTTP response as a
protocol-level failure. This module collapses the two divergent classifications
the dispatcher previously used (HTTP-exception vs. response-payload) into one
source-of-truth helper class used by :mod:`call_tool` middleware.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from agent_runtime.capabilities.mcp.constants import Keys, Messages, Values


class McpToolCallOutcome:
    """Classify an MCP tool-call response envelope per the MCP spec."""

    @classmethod
    def is_protocol_error(cls, output: Mapping[str, Any]) -> bool:
        """Return ``True`` when ``output`` reports a protocol-level failure.

        Looks for ``isError: true`` at the outer envelope and at the optional
        nested ``output["output"]`` envelope some MCP servers emit.
        """
        if not isinstance(output, Mapping):
            return False
        if output.get(Keys.Content.IS_ERROR) is True:
            return True
        nested = output.get(Keys.Content.OUTPUT)
        if isinstance(nested, Mapping) and nested.get(Keys.Content.IS_ERROR) is True:
            return True
        return False

    @classmethod
    def extract_error_text(cls, output: Mapping[str, Any]) -> str:
        """Walk content blocks and return the first non-empty text payload.

        Inspects the outer ``content`` array and the optional nested
        ``output["output"]["content"]`` array. Falls back to a safe generic
        message defined in :class:`Messages.Loader` when no text block is found.
        """
        if isinstance(output, Mapping):
            text = cls._first_text_block(output.get(Keys.Content.CONTENT))
            if text:
                return text
            nested = output.get(Keys.Content.OUTPUT)
            if isinstance(nested, Mapping):
                text = cls._first_text_block(nested.get(Keys.Content.CONTENT))
                if text:
                    return text
        return Messages.Loader.PROTOCOL_ERROR_FALLBACK

    @classmethod
    def _first_text_block(cls, content: Any) -> str | None:
        """Return the first non-empty ``text`` from a content-block array."""
        if not isinstance(content, Iterable) or isinstance(
            content, (str, bytes, Mapping)
        ):
            return None
        for block in content:
            if not isinstance(block, Mapping):
                continue
            if block.get(Keys.Content.TYPE) != Values.ContentType.TEXT:
                continue
            text = block.get(Keys.Content.TEXT)
            if isinstance(text, str) and text:
                return text
        return None


__all__ = ("McpToolCallOutcome",)
