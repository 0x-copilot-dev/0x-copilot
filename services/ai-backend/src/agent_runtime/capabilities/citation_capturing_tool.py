"""LangChain BaseTool wrappers that project citation sources and append ordinal [[N]] hints to tool results."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from langchain_core.tools import BaseTool, InjectedToolCallId
from pydantic import BaseModel, ConfigDict, create_model

from agent_runtime.capabilities.citation_projection import CitationProjector
from agent_runtime.capabilities.conversation_ordinals import (
    ConversationOrdinalAllocator,
)


_LOGGER = logging.getLogger(__name__)


class _CitationHint:
    """Appends a ``[[N]]`` ordinal pointer to a tool result so the model can cite it.

    Handles ``str``, ``tuple`` (LangChain content_and_artifact), ``list``, ``dict``
    (MCP content array or generic key-value), and falls through unchanged for all
    other shapes. The hint is the only instruction the model needs to embed a stable
    pointer in its prose.
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
        """Return the formatted hint string for ``ordinal`` and ``tool_name``."""
        return cls.HINT_TEMPLATE.format(ordinal=ordinal, tool_name=tool_name)

    @classmethod
    def append_to(cls, result: object, *, ordinal: int, tool_name: str) -> object:
        """Append the rendered hint to ``result``, preserving its shape.

        Handles str, tuple, list, dict (MCP content-array or generic), and
        returns any other shape unchanged. Never raises.
        """
        rendered = cls.render(ordinal=ordinal, tool_name=tool_name)
        suffix = cls.SEPARATOR + rendered
        if isinstance(result, str):
            return result + suffix
        if isinstance(result, tuple):
            # LangChain content_and_artifact shape: head is the string the
            # model reads; the tail is the structured artifact we must not
            # modify (DuckDuckGo, most web-search wrappers use this).
            if len(result) >= 1 and isinstance(result[0], str):
                return (result[0] + suffix, *result[1:])
            # Tuple with no string head — walk backwards for the last string.
            updated_seq: list[Any] = list(result)
            for idx in range(len(updated_seq) - 1, -1, -1):
                if isinstance(updated_seq[idx], str):
                    updated_seq[idx] = updated_seq[idx] + suffix
                    return tuple(updated_seq)
            # No string entry at all — prepend the hint so the model still
            # gets a stable pointer even from a non-string tuple.
            updated_seq.insert(0, suffix.lstrip())
            return tuple(updated_seq)
        if isinstance(result, list):
            updated = list(result)
            for idx in range(len(updated) - 1, -1, -1):
                if isinstance(updated[idx], str):
                    updated[idx] = updated[idx] + suffix
                    return updated
            # No string entry — append the hint as its own element.
            updated.append(suffix.lstrip())
            return updated
        if isinstance(result, dict):
            updated_dict = dict(result)
            content = updated_dict.get(cls.MCP_CONTENT_KEY)
            if isinstance(content, list):
                # MCP CallToolResult envelope — add a TextContent block so
                # the hint appears in the same array the server data uses.
                updated_content = list(content)
                updated_content.append(
                    {
                        cls.MCP_BLOCK_TYPE_KEY: cls.MCP_TEXT_VALUE,
                        cls.MCP_TEXT_VALUE: rendered,
                    }
                )
                updated_dict[cls.MCP_CONTENT_KEY] = updated_content
                return updated_dict
            # Generic dict (internal API, custom tool) — add a dedicated
            # top-level key so JSON-rendering consumers still expose it.
            updated_dict[cls.DICT_HINT_KEY] = rendered
            return updated_dict
        return result


class CitationCapturingTool(BaseTool):
    """BaseTool wrapper that projects results to the citation ledger and appends ordinal hints.

    Propagates the inner tool's name, description, and args_schema unchanged.
    The sync ``_run`` path delegates without projection; the runtime always
    dispatches via ``_arun`` where both the ledger projection and the hint
    append happen best-effort.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseTool

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """Sync path: delegate unchanged — citation projection requires the async path."""
        return self.inner._run(*args, **kwargs)

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        """Async path: project result to the citation ledger and append an ordinal hint."""
        # LangGraph injects tool_call_id via InjectedToolCallId on schemas that
        # declare it; the inner tool typically does not accept it, so strip
        # before forwarding. _augment_schema_with_tool_call_id ensures the
        # wrapper's schema declares the annotation for inner schemas that don't.
        tool_call_id = self._extract_tool_call_id(kwargs)
        kwargs.pop("tool_call_id", None)
        result = await self.inner._arun(*args, **kwargs)
        # Source-registration path: pattern-match the result and register any
        # detected sources with the active CitationLedger. Best-effort.
        await CitationProjector.project(
            connector=self.name,
            tool_call_id=tool_call_id,
            result=result,
        )
        # Ordinal-hint path: allocate a conversation-scoped ordinal, bind it
        # to tool_call_id (so CitationResolver can stamp source_tool_call_id),
        # then append the hint text the model uses when writing [[N]].
        # When no allocator is bound (replay/eval) the result is unchanged.
        try:
            allocator = ConversationOrdinalAllocator.active()
            if allocator is None:
                _LOGGER.warning(
                    "[citations] tool.hint_skipped tool=%s reason=no_allocator_bound "
                    "(citations disabled or replay path)",
                    self.name,
                )
            elif not tool_call_id:
                # The schema-augmentation path guarantees a tool_call_id for
                # LangChain dispatch; an empty value here means the inner was
                # called directly (unit tests, replay) where citation is moot.
                _LOGGER.warning(
                    "[citations] tool.hint_skipped tool=%s "
                    "reason=no_tool_call_id_injected (test/replay path)",
                    self.name,
                )
            else:
                ordinal = await allocator.allocate_for_tool_call(
                    tool_call_id=tool_call_id, tool_name=self.name
                )
                result = _CitationHint.append_to(
                    result, ordinal=ordinal, tool_name=self.name
                )
                _LOGGER.info(
                    "[citations] tool.hint_appended tool=%s ordinal=%d "
                    "call_id='%s' result_type=%s",
                    self.name,
                    ordinal,
                    tool_call_id,
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
    """Registry decorator that wraps every BaseTool with CitationCapturingTool.

    Non-BaseTool entries pass through unchanged. Wrapping is idempotent:
    an already-wrapped tool is returned as-is.
    """

    def __init__(self, *, inner: object) -> None:
        """Wrap ``inner`` registry to intercept calls for citation capture."""
        self._inner = inner

    def list_available_tools(self, context: object) -> tuple[object, ...]:
        """Return all tools from the inner registry, each wrapped for citation capture."""
        rendered = self._inner.list_available_tools(context)  # type: ignore[attr-defined]
        return tuple(self._wrap(tool) for tool in rendered)

    @classmethod
    def _wrap(cls, tool: object) -> object:
        """Wrap a single tool; return it unchanged if not a BaseTool or already wrapped."""
        if not isinstance(tool, BaseTool):
            return tool
        if isinstance(tool, CitationCapturingTool):
            return tool
        # Augment the args_schema so LangGraph injects tool_call_id into
        # _arun kwargs. Tools that don't declare InjectedToolCallId natively
        # (e.g. DuckDuckGo) would otherwise emit citations with an empty
        # source_tool_call_id, forcing fragile ordinal-position lookups.
        return CitationCapturingTool(
            name=tool.name,
            description=tool.description,
            args_schema=cls._augment_schema_with_tool_call_id(tool.args_schema),
            inner=tool,
        )

    @staticmethod
    def _augment_schema_with_tool_call_id(
        inner_schema: object,
    ) -> type[BaseModel]:
        """Return a Pydantic args_schema that adds ``tool_call_id: Annotated[str, InjectedToolCallId]``.

        Extends a real Pydantic class via ``create_model``; falls back to a
        minimal synthetic schema for dict/None args_schemas. Idempotent when
        the schema already declares ``tool_call_id``.
        """

        if isinstance(inner_schema, type) and issubclass(inner_schema, BaseModel):
            existing = getattr(inner_schema, "model_fields", {})
            if "tool_call_id" in existing:
                return inner_schema
            return create_model(
                f"{inner_schema.__name__}WithToolCallId",
                __base__=inner_schema,
                tool_call_id=(Annotated[str, InjectedToolCallId], ""),
            )
        return create_model(
            "CitationCapturingToolInput",
            tool_call_id=(Annotated[str, InjectedToolCallId], ""),
        )


__all__ = ("CitationCapturingRegistry", "CitationCapturingTool")
