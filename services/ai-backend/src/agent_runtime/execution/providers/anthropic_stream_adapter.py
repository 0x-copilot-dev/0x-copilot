"""Anthropic citation stream adapter (PRD 01).

Consumes LangChain ``AIMessageChunk`` objects produced by
``langchain_anthropic.ChatAnthropic``. Anthropic's native citation
primitives arrive interleaved with text content blocks: a
``citations_delta`` block lands either alongside the text it grounds
(same content-block ``index``) or as a follow-on chunk whose ``text``
is empty and whose block carries a ``citations`` list.

The adapter:

1. Detects citation blocks in ``chunk.content`` (or
   ``chunk.message.content``).
2. Builds a :class:`SourceRef` per citation and registers it through
   :meth:`CitationLedger.cite`.
3. Returns a text delta that appends the resulting ``[c<id>]`` tokens
   immediately after the cited prose, so the FE renders the chip at the
   end of the run that grounds it.

When the active ledger is unbound (citations disabled, or no run
context), the adapter returns ``raw_delta`` unchanged. When a chunk has
neither citation blocks nor text, the adapter returns ``None`` to skip
emission.

References:
- https://docs.anthropic.com/en/docs/build-with-claude/citations
- https://docs.anthropic.com/en/api/messages-streaming
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from agent_runtime.capabilities.citations import CitationLedger, SourceRef
from agent_runtime.execution.providers.citation_extraction import (
    ChunkContentReader,
)


class _Fields:
    """Stable field names on Anthropic content blocks (post-LangChain)."""

    TYPE = "type"
    TEXT = "text"
    CITATIONS = "citations"

    BLOCK_TEXT = "text"

    CITATION_TYPE = "type"
    CITATION_URL = "url"
    CITATION_TITLE = "title"
    CITATION_DOCUMENT_TITLE = "document_title"
    CITATION_DOCUMENT_INDEX = "document_index"
    CITATION_CITED_TEXT = "cited_text"


class _CitationKinds:
    """Recognised Anthropic citation kinds."""

    CHAR_LOCATION = "char_location"
    PAGE_LOCATION = "page_location"
    CONTENT_BLOCK_LOCATION = "content_block_location"
    WEB_SEARCH_RESULT_LOCATION = "web_search_result_location"


class AnthropicCitationStreamAdapter:
    """Lift Anthropic native citations through :class:`CitationLedger`.

    Stateless across chunks: each chunk is self-describing. The adapter
    relies on the ledger's idempotency to dedupe the same source across
    repeated chunks within a single run.
    """

    CONNECTOR = "anthropic"

    async def adapt_chunk(self, *, chunk: object, raw_delta: str | None) -> str | None:
        """See :class:`ProviderCitationAdapter.adapt_chunk`."""

        if CitationLedger.active() is None:
            return raw_delta

        blocks = ChunkContentReader.content_blocks(chunk)
        if not blocks:
            return raw_delta

        chips: list[str] = []
        for block in blocks:
            block_chips = await self._chips_from_block(block)
            chips.extend(block_chips)

        if not chips:
            return raw_delta

        chip_text = "".join(chips)
        if raw_delta is None or raw_delta == "":
            return chip_text
        return raw_delta + chip_text

    @classmethod
    async def _chips_from_block(cls, block: Mapping[str, object]) -> list[str]:
        if block.get(_Fields.TYPE) != _Fields.BLOCK_TEXT:
            return []
        citations = block.get(_Fields.CITATIONS)
        if not isinstance(citations, Sequence) or isinstance(citations, (str, bytes)):
            return []
        chips: list[str] = []
        for citation in citations:
            if not isinstance(citation, Mapping):
                continue
            source = cls._source_from_citation(citation)
            if source is None:
                continue
            token = await CitationLedger.cite(source)
            if token:
                chips.append(token)
        return chips

    @classmethod
    def _source_from_citation(cls, citation: Mapping[str, object]) -> SourceRef | None:
        url = cls._coerce_text(citation.get(_Fields.CITATION_URL))
        title = cls._coerce_text(
            citation.get(_Fields.CITATION_TITLE)
        ) or cls._coerce_text(citation.get(_Fields.CITATION_DOCUMENT_TITLE))
        cited = cls._coerce_text(citation.get(_Fields.CITATION_CITED_TEXT))
        document_index = citation.get(_Fields.CITATION_DOCUMENT_INDEX)
        doc_id = url
        if doc_id is None and isinstance(document_index, int):
            doc_id = f"document_index:{document_index}"
        if doc_id is None or title is None:
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

    @classmethod
    def recognised_citation_kinds(cls) -> Iterable[str]:
        """Test helper: surface the citation kinds the adapter handles.

        Anthropic emits citations across several location kinds (char,
        page, content_block, web search). The adapter doesn't switch on
        kind today — it just reads ``url`` / ``title`` / ``cited_text``
        which every kind carries — but tests assert that no recognised
        kind regresses.
        """

        return (
            _CitationKinds.CHAR_LOCATION,
            _CitationKinds.PAGE_LOCATION,
            _CitationKinds.CONTENT_BLOCK_LOCATION,
            _CitationKinds.WEB_SEARCH_RESULT_LOCATION,
        )


__all__ = ("AnthropicCitationStreamAdapter",)
