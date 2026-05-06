"""Gemini grounding citation stream adapter (PRD 03).

Consumes LangChain ``AIMessageChunk`` objects produced by
``langchain_google_genai.ChatGoogleGenerativeAI`` (Vertex / Gemini)
when a grounding tool such as ``google_search`` is enabled. Grounding
arrives as ``response_metadata.grounding_metadata`` (or, on legacy
versions, ``additional_kwargs.grounding_metadata``) with the shape::

    {
      "grounding_chunks": [
        {"web": {"uri": "...", "title": "..."}},
        {"retrieved_context": {"uri": "...", "title": "..."}},
      ],
      "grounding_supports": [
        {
          "segment": {"start_index": 0, "end_index": 30, "text": "..."},
          "grounding_chunk_indices": [0, 1],
        },
      ],
    }

Each support says: a substring of the response was grounded by these
chunks. v1 emits ``[c<id>]`` chips at the chunk boundary that carries
the grounding metadata, so chips trail the prose run that grounds
them. Inline-position chip placement using ``segment.start_index`` /
``end_index`` is deferred (PRD 03 non-goal).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent_runtime.capabilities.citations import CitationLedger, SourceRef
from agent_runtime.execution.providers.citation_extraction import (
    ChunkMetadataReader,
)


class _Fields:
    """Stable field names on Gemini grounding metadata."""

    GROUNDING_METADATA = "grounding_metadata"
    GROUNDING_CHUNKS = "grounding_chunks"
    GROUNDING_SUPPORTS = "grounding_supports"
    GROUNDING_CHUNK_INDICES = "grounding_chunk_indices"

    WEB = "web"
    RETRIEVED_CONTEXT = "retrieved_context"

    URI = "uri"
    TITLE = "title"

    SEGMENT = "segment"
    SEGMENT_TEXT = "text"


class _Connectors:
    WEB = "gemini_web"
    RETRIEVED = "gemini_retrieved"


class GeminiGroundingCitationStreamAdapter:
    """Lift Gemini grounding chunks through :class:`CitationLedger`."""

    async def adapt_chunk(self, *, chunk: object, raw_delta: str | None) -> str | None:
        """See :class:`ProviderCitationAdapter.adapt_chunk`."""

        if CitationLedger.active() is None:
            return raw_delta

        metadata = ChunkMetadataReader.metadata(chunk, _Fields.GROUNDING_METADATA)
        if metadata is None:
            return raw_delta

        grounding_chunks = self._sequence(metadata.get(_Fields.GROUNDING_CHUNKS))
        if not grounding_chunks:
            return raw_delta
        sources = [self._source_from_chunk(item) for item in grounding_chunks]

        supports = self._sequence(metadata.get(_Fields.GROUNDING_SUPPORTS))
        cited_indices = self._cited_indices(supports, fallback=len(grounding_chunks))

        chips: list[str] = []
        for idx in cited_indices:
            if idx < 0 or idx >= len(sources):
                continue
            source = sources[idx]
            if source is None:
                continue
            token = await CitationLedger.cite(source)
            if token:
                chips.append(token)

        if not chips:
            return raw_delta

        chip_text = "".join(chips)
        if raw_delta is None or raw_delta == "":
            return chip_text
        return raw_delta + chip_text

    @classmethod
    def _source_from_chunk(cls, item: object) -> SourceRef | None:
        if not isinstance(item, Mapping):
            return None
        web = item.get(_Fields.WEB)
        if isinstance(web, Mapping):
            return cls._source_from_pair(web, connector=_Connectors.WEB)
        retrieved = item.get(_Fields.RETRIEVED_CONTEXT)
        if isinstance(retrieved, Mapping):
            return cls._source_from_pair(retrieved, connector=_Connectors.RETRIEVED)
        return None

    @classmethod
    def _source_from_pair(
        cls,
        pair: Mapping[str, object],
        *,
        connector: str,
    ) -> SourceRef | None:
        uri = cls._coerce_text(pair.get(_Fields.URI))
        title = cls._coerce_text(pair.get(_Fields.TITLE))
        if uri is None:
            return None
        return SourceRef(
            source_connector=connector,
            source_doc_id=uri,
            title=title or uri,
            source_url=uri,
            snippet=None,
        )

    @classmethod
    def _cited_indices(cls, supports: Sequence[object], *, fallback: int) -> list[int]:
        """Return chunk indices the model says it grounded.

        When ``grounding_supports`` is missing or empty we conservatively
        cite every grounding chunk — the model declared them as evidence
        for the response even if it didn't tag specific spans.
        """

        if not supports:
            return list(range(fallback))
        seen: list[int] = []
        seen_set: set[int] = set()
        for support in supports:
            if not isinstance(support, Mapping):
                continue
            indices = support.get(_Fields.GROUNDING_CHUNK_INDICES)
            if not isinstance(indices, Sequence) or isinstance(indices, (str, bytes)):
                continue
            for idx in indices:
                if isinstance(idx, int) and idx not in seen_set:
                    seen_set.add(idx)
                    seen.append(idx)
        return seen

    @staticmethod
    def _sequence(value: object) -> Sequence[object]:
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return value
        return ()

    @staticmethod
    def _coerce_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None


__all__ = ("GeminiGroundingCitationStreamAdapter",)
