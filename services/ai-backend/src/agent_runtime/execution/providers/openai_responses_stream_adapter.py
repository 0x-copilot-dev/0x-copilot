"""OpenAI Responses citation stream adapter (PRD 02).

Consumes LangChain ``AIMessageChunk`` objects produced by
``langchain_openai.ChatOpenAI(use_responses_api=True)``. The Responses
API attaches grounding evidence as ``annotations`` on the
``output_text.done`` event. LangChain reflects this by emitting a final
chunk whose content is::

    [{"type": "text", "text": "", "annotations": [...], "index": 0}]

where each annotation is one of:

- ``url_citation`` — has ``url``, ``title``, ``start_index``,
  ``end_index``.
- ``file_citation`` — has ``file_id`` and (optionally) ``filename``.

The adapter registers each annotation as a :class:`SourceRef` against
the active :class:`CitationLedger` and returns the resulting
``[c<id>]`` tokens as the chunk's text delta.

v1 emits chips at the end of the ``output_text.done`` event rather
than interleaving them at ``end_index`` positions inside earlier
``output_text.delta`` chunks. Inline interleaving requires holding back
text deltas, which we avoid for latency reasons. The chip resolves to
the same source either way; only the position differs.

References:
- https://platform.openai.com/docs/api-reference/responses
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent_runtime.capabilities.citations import CitationLedger, SourceRef
from agent_runtime.execution.providers.citation_extraction import (
    ChunkContentReader,
)


class _Fields:
    """Stable field names on OpenAI Responses content blocks."""

    TYPE = "type"
    TEXT = "text"
    ANNOTATIONS = "annotations"

    BLOCK_TEXT = "text"

    ANNOTATION_TYPE = "type"
    ANNOTATION_URL = "url"
    ANNOTATION_TITLE = "title"
    ANNOTATION_FILE_ID = "file_id"
    ANNOTATION_FILENAME = "filename"
    ANNOTATION_QUOTE = "quote"


class _AnnotationKinds:
    URL_CITATION = "url_citation"
    FILE_CITATION = "file_citation"


class _Connectors:
    URL = "openai_web"
    FILE = "openai_file"


class OpenAIResponsesCitationStreamAdapter:
    """Lift OpenAI Responses annotations through :class:`CitationLedger`."""

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
        annotations = block.get(_Fields.ANNOTATIONS)
        if not isinstance(annotations, Sequence) or isinstance(
            annotations, (str, bytes)
        ):
            return []
        chips: list[str] = []
        for annotation in annotations:
            if not isinstance(annotation, Mapping):
                continue
            source = cls._source_from_annotation(annotation)
            if source is None:
                continue
            token = await CitationLedger.cite(source)
            if token:
                chips.append(token)
        return chips

    @classmethod
    def _source_from_annotation(
        cls, annotation: Mapping[str, object]
    ) -> SourceRef | None:
        kind = annotation.get(_Fields.ANNOTATION_TYPE)
        if kind == _AnnotationKinds.URL_CITATION:
            return cls._url_source(annotation)
        if kind == _AnnotationKinds.FILE_CITATION:
            return cls._file_source(annotation)
        return None

    @classmethod
    def _url_source(cls, annotation: Mapping[str, object]) -> SourceRef | None:
        url = cls._coerce_text(annotation.get(_Fields.ANNOTATION_URL))
        title = cls._coerce_text(annotation.get(_Fields.ANNOTATION_TITLE))
        if url is None:
            return None
        snippet = cls._coerce_text(annotation.get(_Fields.ANNOTATION_QUOTE))
        return SourceRef(
            source_connector=_Connectors.URL,
            source_doc_id=url,
            title=title or url,
            source_url=url,
            snippet=snippet,
        )

    @classmethod
    def _file_source(cls, annotation: Mapping[str, object]) -> SourceRef | None:
        file_id = cls._coerce_text(annotation.get(_Fields.ANNOTATION_FILE_ID))
        if file_id is None:
            return None
        filename = cls._coerce_text(annotation.get(_Fields.ANNOTATION_FILENAME))
        snippet = cls._coerce_text(annotation.get(_Fields.ANNOTATION_QUOTE))
        return SourceRef(
            source_connector=_Connectors.FILE,
            source_doc_id=file_id,
            title=filename or file_id,
            source_url=None,
            snippet=snippet,
        )

    @staticmethod
    def _coerce_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None


__all__ = ("OpenAIResponsesCitationStreamAdapter",)
