"""Generic citation projector for MCP tool results (PR 1.1 follow-up C).

Thin adapter over :class:`CitationProjector` (the shared shape extractor).
Kept as a stable named seam so MCP-side callers can wire citation
projection without depending on the shared module's import path.

The shared projector documents the recognized result shapes and the
best-effort degradation rules.
"""

from __future__ import annotations

from agent_runtime.capabilities.citation_projection import CitationProjector


class CitationProjectingMcpMiddleware:
    """MCP-facing alias for :class:`CitationProjector`."""

    @classmethod
    async def project(
        cls,
        *,
        connector: str,
        tool_call_id: str | None,
        result: object,
    ) -> None:
        """Forward to :meth:`CitationProjector.project`."""

        await CitationProjector.project(
            connector=connector,
            tool_call_id=tool_call_id,
            result=result,
        )


__all__ = ("CitationProjectingMcpMiddleware",)
