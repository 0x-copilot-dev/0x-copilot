"""Thin MCP-facing wrapper around :class:`CitationProjector` for stable import isolation."""

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
