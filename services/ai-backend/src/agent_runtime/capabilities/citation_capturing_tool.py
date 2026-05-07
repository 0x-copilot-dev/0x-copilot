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
from agent_runtime.capabilities.conversation_ordinals import (
    ConversationOrdinalAllocator,
)


_LOGGER = logging.getLogger(__name__)


class _CitationHint:
    """Render the per-tool-call citation hint appended to result text.

    PR 1.1-rev2 — every tool result the model reads gets a one-line
    suffix that names a stable ``[[N]]`` pointer the model can cite when
    grounding any factual claim in this result. The suffix is rendered
    by a class so the format is testable and the only consumer (the
    wrapper's ``_arun``) imports a single name.

    Result types:

    - ``str`` — append ``\\n\\n[Tool call #N — <tool> — cite as [[N]]…]``.
    - ``tuple`` — LangChain's ``response_format="content_and_artifact"``
      contract; the first element is the string the model reads, the
      second is the structured artifact. We extend the first element
      and return a new tuple. ``DuckDuckGoSearchResults`` and most
      LangChain web-search wrappers use this shape.
    - ``list`` of strings — append the hint to the last string entry, or
      append a new string entry when the list is empty / has no string
      tail. (LangChain occasionally returns ``list[str]`` for tools that
      stream multi-part text outputs.)
    - ``dict`` — MCP envelope with a ``content`` array, OR generic dict
      that gets a top-level ``_citation_hint`` field added.
    - any other shape — return unchanged.
    """

    HINT_TEMPLATE = (
        "[Tool call #{ordinal} — {tool_name} — "
        "cite as [[{ordinal}]] when referencing this result.]"
    )
    SEPARATOR = "\n\n"
    # Top-level key added to dict-shaped results (MCP, internal APIs)
    # when no ``content`` text block exists to extend. The model sees
    # this as part of the JSON-encoded tool result and learns to cite.
    DICT_HINT_KEY = "_citation_hint"
    # MCP standard envelope keys.
    MCP_CONTENT_KEY = "content"
    MCP_BLOCK_TYPE_KEY = "type"
    MCP_TEXT_VALUE = "text"

    @classmethod
    def render(cls, *, ordinal: int, tool_name: str) -> str:
        return cls.HINT_TEMPLATE.format(ordinal=ordinal, tool_name=tool_name)

    @classmethod
    def append_to(cls, result: object, *, ordinal: int, tool_name: str) -> object:
        rendered = cls.render(ordinal=ordinal, tool_name=tool_name)
        suffix = cls.SEPARATOR + rendered
        if isinstance(result, str):
            return result + suffix
        if isinstance(result, tuple):
            # LangChain ``response_format="content_and_artifact"`` —
            # ``(content_string, artifact)`` is the canonical shape;
            # ``DuckDuckGoSearchResults(output_format="list")`` returns
            # ``(formatted_text, list[dict])``. Extending the first
            # element is the only place the model actually reads.
            if len(result) >= 1 and isinstance(result[0], str):
                return (result[0] + suffix, *result[1:])
            # Tuple whose head isn't a string — walk for the last
            # string entry and extend it.
            updated_seq: list[Any] = list(result)
            for idx in range(len(updated_seq) - 1, -1, -1):
                if isinstance(updated_seq[idx], str):
                    updated_seq[idx] = updated_seq[idx] + suffix
                    return tuple(updated_seq)
            updated_seq.insert(0, suffix.lstrip())
            return tuple(updated_seq)
        if isinstance(result, list):
            updated = list(result)
            for idx in range(len(updated) - 1, -1, -1):
                if isinstance(updated[idx], str):
                    updated[idx] = updated[idx] + suffix
                    return updated
            # No string entry found — append the hint as its own entry
            # so the model still sees a stable pointer.
            updated.append(suffix.lstrip())
            return updated
        if isinstance(result, dict):
            updated_dict = dict(result)
            content = updated_dict.get(cls.MCP_CONTENT_KEY)
            if isinstance(content, list):
                # MCP CallToolResult shape — append a TextContent block
                # so the hint rides the same content array the server's
                # data uses, keeping the model's view consistent.
                updated_content = list(content)
                updated_content.append(
                    {
                        cls.MCP_BLOCK_TYPE_KEY: cls.MCP_TEXT_VALUE,
                        cls.MCP_TEXT_VALUE: rendered,
                    }
                )
                updated_dict[cls.MCP_CONTENT_KEY] = updated_content
                return updated_dict
            # Generic dict (internal API, custom tool) — surface the
            # hint as a dedicated top-level field so consumers that
            # JSON-render the result still expose it to the model.
            updated_dict[cls.DICT_HINT_KEY] = rendered
            return updated_dict
        return result


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
        tool_call_id = self._extract_tool_call_id(kwargs)
        # Legacy PR 1.1 path — pattern-matches the result against a
        # fixed set of shapes and registers any sources via the
        # ``CitationLedger``. Best-effort, never raises into the tool
        # path. Kept during rollout so existing ``[c<id>]`` chips
        # continue to render for the shapes the projector recognizes.
        await CitationProjector.project(
            connector=self.name,
            tool_call_id=tool_call_id,
            result=result,
        )
        # PR 1.1-rev2 path — allocate a conversation-scoped ordinal,
        # bind it to the tool_call_id (when known) so the
        # ``CitationResolver`` can populate ``source_tool_call_id`` on
        # emit, and append a single-line hint to the result text the
        # model reads. The model embeds ``[[N]]`` in its prose; the
        # resolver fires ``citation_made`` events from the streamed
        # output. When no allocator is bound (replay/eval), the result
        # is returned unchanged.
        try:
            allocator = ConversationOrdinalAllocator.active()
            if allocator is None:
                _LOGGER.warning(
                    "[citations] tool.hint_skipped tool=%s reason=no_allocator_bound "
                    "(citations disabled or replay path)",
                    self.name,
                )
            else:
                ordinal = (
                    allocator.allocate_for_tool_call(tool_call_id=tool_call_id)
                    if tool_call_id
                    else allocator.allocate()
                )
                result = _CitationHint.append_to(
                    result, ordinal=ordinal, tool_name=self.name
                )
                _LOGGER.info(
                    "[citations] tool.hint_appended tool=%s ordinal=%d "
                    "call_id='%s' result_type=%s",
                    self.name,
                    ordinal,
                    tool_call_id or "",
                    type(result).__name__,
                )
        except Exception:  # noqa: BLE001 - best-effort, never break the tool path
            _LOGGER.warning(
                "[citations] tool.hint_raised tool=%s; returning unmodified result",
                self.name,
                exc_info=True,
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
