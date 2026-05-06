"""Local-tool citation capture (PR 1.1 follow-up D).

Bridges :class:`CitationProjector` to the LangChain tool dispatch loop.
Mirrors :class:`agent_runtime.capabilities.tool_budget_guard.ToolBudgetGuardedRegistry`:
the registry-of-tools port is passed through unchanged, only the
``list_available_tools`` output is rewritten so each model-visible
LangChain :class:`BaseTool` runs through :class:`CitationCapturingTool`.

The wrapper is a passthrough for the model — the tool's name,
description, args_schema, and result are returned unchanged. Side effect:
after each successful invocation, the result is fed to
:meth:`CitationProjector.project`, which registers detected sources
through the active per-run :class:`CitationLedger` (and so emits one
``source_ingested`` event per unique source). When no ledger is bound
(unit tests of the inner tool, eval/replay), the projector silently
returns and the wrapper is a no-op.

We deliberately do NOT inject ``[c<id>]`` tokens into the tool result
text. The model wasn't trained to expect chip tokens inside arbitrary
tool outputs, and rewriting them would (1) make the model echo chips for
sources it didn't actually use and (2) break tools that read the result
back. The right rail :class:`SourcesTab` populates from the same SSE
events; the user gets a clean source list without inline chip noise.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import ConfigDict

from agent_runtime.capabilities.citation_projection import CitationProjector


_LOGGER = logging.getLogger(__name__)


class CitationCapturingTool(BaseTool):
    """LangChain ``BaseTool`` wrapper that projects tool results to the ledger.

    Inner tool's ``name`` / ``description`` / ``args_schema`` are
    propagated so the model sees an identical surface. Only the
    invocation path differs: after the inner returns, we project the
    result through :class:`CitationProjector` (best-effort, never raises
    into the tool path).

    The sync ``_run`` path delegates without projection — the citation
    ledger is async-only and the runtime always invokes tools via
    ``_arun``. Sync invocation is reserved for unit tests of inner
    tools, where citation capture is irrelevant.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseTool

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        return self.inner._run(*args, **kwargs)

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        result = await self.inner._arun(*args, **kwargs)
        await CitationProjector.project(
            connector=self.name,
            tool_call_id=self._extract_tool_call_id(kwargs),
            result=result,
        )
        return result

    @staticmethod
    def _extract_tool_call_id(kwargs: dict[str, Any]) -> str | None:
        """Pull the tool_call_id out of kwargs when LangChain injected it.

        LangChain passes ``tool_call_id`` to ``_arun`` only when the tool
        opts in via ``Annotated[str, InjectedToolCallId]``. Most stock
        tools (DuckDuckGoSearchResults included) don't, so we silently
        fall back to ``None`` — the ledger accepts that.
        """

        value = kwargs.get("tool_call_id")
        return value if isinstance(value, str) else None


class CitationCapturingRegistry:
    """Wrap a tool registry so every returned tool projects citations.

    Tools that aren't LangChain ``BaseTool`` instances pass through
    untouched — citation capture only applies to the model-visible
    LangChain layer. Already-wrapped tools are returned unchanged so
    nesting registries is idempotent.
    """

    def __init__(self, *, inner: object) -> None:
        self._inner = inner

    def list_available_tools(self, context: object) -> tuple[object, ...]:
        rendered = self._inner.list_available_tools(context)  # type: ignore[attr-defined]
        return tuple(self._wrap(tool) for tool in rendered)

    @staticmethod
    def _wrap(tool: object) -> object:
        if not isinstance(tool, BaseTool):
            return tool
        if isinstance(tool, CitationCapturingTool):
            return tool
        return CitationCapturingTool(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            inner=tool,
        )


__all__ = ("CitationCapturingRegistry", "CitationCapturingTool")
