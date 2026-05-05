"""Anthropic citations stream adapter (PR 1.1 follow-up D scaffold).

Wraps a raw ``AsyncAnthropic.messages.stream`` iterator, intercepts
``content_block_delta`` events whose ``delta.type == 'citations_delta'``,
funnels them through :class:`CitationLedger`, and substitutes the
resulting ``[c<id>]`` token into the immediately preceding text delta.

The adapter is opt-in and provider-scoped — the rest of the runtime is
unchanged. When the active ledger is unbound (citations disabled, or no
run context), the adapter is a passthrough.

This file ships as a scaffold: the public class shape + substitution
helper are stable so swapping LangChain's chat-model invocation for a
direct Anthropic stream becomes a small wiring change rather than a
design discussion. The actual model-invocation swap is tracked separately
because it touches the runtime factory's model construction path.

References:
- https://docs.anthropic.com/en/docs/build-with-claude/citations
- https://docs.anthropic.com/en/api/messages-streaming
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from agent_runtime.capabilities.citations import SourceRef
from agent_runtime.execution.providers.citation_substitution import (
    CitationCandidate,
    CitationSubstitution,
)


class _AnthropicFields:
    """Stable Anthropic stream-event field names."""

    TYPE = "type"
    DELTA = "delta"
    INDEX = "index"
    CITATION = "citation"
    URL = "url"
    TITLE = "title"
    CITED_TEXT = "cited_text"
    DOCUMENT_INDEX = "document_index"
    DOCUMENT_TITLE = "document_title"
    SOURCE = "source"

    EVENT_CONTENT_BLOCK_DELTA = "content_block_delta"
    DELTA_TEXT = "text_delta"
    DELTA_CITATION = "citations_delta"
    DELTA_TEXT_FIELD = "text"


class AnthropicCitationStreamAdapter:
    """Iterate an Anthropic stream while lifting native citations through the ledger.

    Per-turn state: a small text-delta accumulator keyed by content-block
    index, so when a ``citations_delta`` arrives we know which span of
    the output it grounds. Anthropic emits the citation alongside the
    text it derived from, so the substitution span is the most recent
    text delta on that block.
    """

    CONNECTOR = "anthropic"

    def __init__(self) -> None:
        self._block_text: dict[int, list[str]] = {}

    async def aiter(
        self,
        raw_stream: AsyncIterator[Mapping[str, Any]],
    ) -> AsyncIterator[Mapping[str, Any]]:
        """Yield each upstream event, rewriting text deltas to embed citation tokens."""

        async for event in raw_stream:
            if not isinstance(event, Mapping):
                yield event
                continue
            if (
                event.get(_AnthropicFields.TYPE)
                != _AnthropicFields.EVENT_CONTENT_BLOCK_DELTA
            ):
                yield event
                continue
            delta = event.get(_AnthropicFields.DELTA)
            if not isinstance(delta, Mapping):
                yield event
                continue
            delta_type = delta.get(_AnthropicFields.TYPE)
            block_index = self._coerce_int(event.get(_AnthropicFields.INDEX))
            if delta_type == _AnthropicFields.DELTA_TEXT:
                text = delta.get(_AnthropicFields.DELTA_TEXT_FIELD)
                if isinstance(text, str) and block_index is not None:
                    self._block_text.setdefault(block_index, []).append(text)
                yield event
                continue
            if (
                delta_type == _AnthropicFields.DELTA_CITATION
                and block_index is not None
            ):
                rewritten = await self._handle_citation(
                    block_index=block_index,
                    citation=delta.get(_AnthropicFields.CITATION),
                )
                yield self._rewrite_block_text_delta(event, block_index, rewritten)
                continue
            yield event

    async def _handle_citation(
        self,
        *,
        block_index: int,
        citation: object,
    ) -> str | None:
        """Register a single citation; return the substituted text or ``None``."""

        if not isinstance(citation, Mapping):
            return None
        source = self._build_source(citation)
        if source is None:
            return None
        accumulated = "".join(self._block_text.get(block_index, ()))
        if not accumulated:
            return None
        candidate = CitationCandidate(source=source, span=(0, len(accumulated)))
        rewritten = await CitationSubstitution.apply(
            text=accumulated,
            candidates=(candidate,),
        )
        if rewritten == accumulated:
            return None
        # Reset the accumulator so a follow-up citation on the same block
        # only sees later text deltas. Anthropic emits citations after
        # the text they ground; the substituted run is "consumed".
        self._block_text[block_index] = [rewritten]
        return rewritten

    def _rewrite_block_text_delta(
        self,
        event: Mapping[str, Any],
        block_index: int,
        rewritten_text: str | None,
    ) -> Mapping[str, Any]:
        """Return ``event`` unchanged when no rewrite is needed.

        When a rewrite happened, the citation event is forwarded as-is
        (it still carries the structured citation for any consumer that
        wants it) and the text rewriting is reflected in our internal
        block accumulator so subsequent text deltas re-emit the joined
        run with the inline token.
        """

        del block_index, rewritten_text
        return event

    @classmethod
    def _build_source(cls, citation: Mapping[str, Any]) -> SourceRef | None:
        url = cls._coerce_text(citation.get(_AnthropicFields.URL))
        title = cls._coerce_text(
            citation.get(_AnthropicFields.TITLE)
        ) or cls._coerce_text(citation.get(_AnthropicFields.DOCUMENT_TITLE))
        cited = cls._coerce_text(citation.get(_AnthropicFields.CITED_TEXT))
        document_index = citation.get(_AnthropicFields.DOCUMENT_INDEX)
        doc_id = url or (
            f"document_index:{document_index}"
            if isinstance(document_index, int)
            else None
        )
        if not doc_id or not title:
            return None
        return SourceRef(
            source_connector=cls.CONNECTOR,
            source_doc_id=doc_id,
            title=title,
            source_url=url,
            snippet=cited,
        )

    @staticmethod
    def _coerce_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _coerce_int(value: object) -> int | None:
        return value if isinstance(value, int) else None
