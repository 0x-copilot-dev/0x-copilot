"""LangChain BaseTool wrappers that project citation sources and append ordinal [[N]] hints to tool results."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, InjectedToolCallId

from agent_runtime.capabilities.delegating_tool import NO_CONFIG, DelegatingTool
from pydantic import BaseModel, ConfigDict, create_model

from agent_runtime.capabilities.citation_projection import CitationProjector
from agent_runtime.capabilities.conversation_ordinals import (
    ConversationOrdinalAllocator,
)
from agent_runtime.capabilities.mcp.middleware.compose import (
    ToolResultShape,
    ToolSchemaIdentity,
)
from agent_runtime.capabilities.tool_result_notes import ToolResultNote


_LOGGER = logging.getLogger(__name__)


class CitationHint:
    """Appends a ``[[N]]`` ordinal pointer to a tool result so the model can cite it.

    Handles ``str``, ``tuple`` (LangChain content_and_artifact), ``list``, ``dict``
    (MCP content array or generic key-value), and falls through unchanged for all
    other shapes. The hint is the only instruction the model needs to embed a stable
    pointer in its prose.

    ``NOTE_PREFIX`` is the invariant head of every rendered hint, factored out of
    :attr:`HINT_TEMPLATE` so a reader of a materialized tool result can recognise
    the note without re-deriving its shape. The Context Occupancy Ledger splits
    these appended notes off a ``ToolMessage`` tail to report what they cost
    (design §4.6, audit item L); the split has to be exact rather than a regex
    guess, which it can only be while both the producer and the recogniser read
    the same constant.
    """

    NOTE_PREFIX = "[Tool call #"
    HINT_TEMPLATE = (
        NOTE_PREFIX + "{ordinal} — {tool_name} — "
        "cite as [[{ordinal}]] when referencing this result.]"
    )
    # Top-level key added to dict-shaped results (MCP, internal APIs)
    # when no ``content`` text block exists to extend. The model sees
    # this as part of the JSON-encoded tool result and learns to cite.
    DICT_HINT_KEY = "_citation_hint"

    @classmethod
    def render(cls, *, ordinal: int, tool_name: str) -> str:
        """Return the formatted hint string for ``ordinal`` and ``tool_name``."""
        return cls.HINT_TEMPLATE.format(ordinal=ordinal, tool_name=tool_name)

    @classmethod
    def append_to(cls, result: object, *, ordinal: int, tool_name: str) -> object:
        """Append the rendered hint to ``result``, preserving its shape.

        The shape walk (str / tuple / list / MCP envelope / generic dict) is
        shared with the tool-budget notice — see :class:`ToolResultNote`.
        """
        return ToolResultNote.append(
            result,
            note=cls.render(ordinal=ordinal, tool_name=tool_name),
            dict_key=cls.DICT_HINT_KEY,
        )

    @classmethod
    def append_within_response_format(
        cls,
        result: object,
        *,
        ordinal: int,
        tool_name: str,
        response_format: str,
    ) -> object:
        """Append the hint without breaking the shape ``response_format`` promised.

        ``BaseTool.arun`` unpacks **exactly two** values from a
        ``content_and_artifact`` return and raises ``ValueError`` on anything
        else, so the annotation has to land on the content half and leave the
        pair a pair.

        :meth:`append_to` cannot do that on its own: :class:`ToolResultNote`
        walks a tuple looking for a string to extend and, finding none, inserts
        the note as a new first element. That is right for a free-form tuple and
        fatal for a declared ``(content, artifact)`` pair whose content is not a
        string — the built-in ``web_search`` returns ``(list[dict], list[dict])``,
        which would reach dispatch as a three-tuple and fail the call the
        artifact was being preserved for.

        A tool that declares the format but did not return a pair is already
        breaking its own contract; the plain shape walk is kept for it so this
        method never changes behaviour it is not there to fix.
        """

        if response_format != ToolResultShape.CONTENT_AND_ARTIFACT:
            return cls.append_to(result, ordinal=ordinal, tool_name=tool_name)
        if not isinstance(result, tuple) or len(result) != 2:
            return cls.append_to(result, ordinal=ordinal, tool_name=tool_name)
        content, artifact = result
        return (
            cls.append_to(content, ordinal=ordinal, tool_name=tool_name),
            artifact,
        )


# The class was module-private until the occupancy ledger needed to recognise
# the note it appends. The old private name is kept as an alias so existing
# importers are unaffected by the promotion.
_CitationHint = CitationHint


class CitationCapturingTool(DelegatingTool):
    """BaseTool wrapper that projects results to the citation ledger and appends ordinal hints.

    Propagates the inner tool's whole surface — model-visible *and*
    dispatch-shaping — except ``args_schema``, which is deliberately augmented
    with an injected ``tool_call_id`` (see
    :meth:`CitationCapturingRegistry._augment_schema_with_tool_call_id`). The
    sync ``_run`` path delegates without projection; the runtime always
    dispatches via ``_arun`` where both the ledger projection and the hint
    append happen best-effort.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseTool

    def _run(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Sync path: delegate unchanged — citation projection requires the async path."""
        return self.delegate(*args, config=config, **kwargs)

    async def _arun(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Async path: project result to the citation ledger and append an ordinal hint."""
        # LangGraph injects tool_call_id via InjectedToolCallId on schemas that
        # declare it; the inner tool typically does not accept it, so strip
        # before forwarding. _augment_schema_with_tool_call_id ensures the
        # wrapper's schema declares the annotation for inner schemas that don't.
        tool_call_id = self._extract_tool_call_id(kwargs)
        kwargs.pop("tool_call_id", None)
        result = await self.adelegate(*args, config=config, **kwargs)
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
                result = CitationHint.append_within_response_format(
                    result,
                    ordinal=ordinal,
                    tool_name=self.name,
                    response_format=self.response_format,
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
        """Wrap a single tool; return it unchanged if not a BaseTool or already wrapped.

        The propagated field set is
        :class:`~agent_runtime.capabilities.mcp.middleware.compose.ToolSchemaIdentity`'s
        — one definition for every wrapper in the runtime rather than a
        hand-listed subset per wrap site. ``args_schema`` is the single
        deliberate override (below); everything else, including the dispatch
        surface, must arrive unchanged. Listing only name / description /
        args_schema here is what dropped ``response_format`` from the built-in
        ``web_search`` chain and stringified its ``(content, artifact)`` pair
        into the model-visible content on every call.
        """
        if not isinstance(tool, BaseTool):
            return tool
        if isinstance(tool, CitationCapturingTool):
            return tool
        # Augment the args_schema so LangGraph injects tool_call_id into
        # _arun kwargs. Tools that don't declare InjectedToolCallId natively
        # (e.g. DuckDuckGo) would otherwise emit citations with an empty
        # source_tool_call_id, forcing fragile ordinal-position lookups.
        fields = ToolSchemaIdentity.fields_of(tool)
        fields["args_schema"] = cls._augment_schema_with_tool_call_id(tool.args_schema)
        return CitationCapturingTool(**fields, inner=tool)

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


__all__ = ("CitationCapturingRegistry", "CitationCapturingTool", "CitationHint")
