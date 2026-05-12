"""Stateless extractor that pattern-matches tool results to CitationLedger sources.

Recognizes content-blocks, results-list, single-resource, and top-level list-of-dicts
shapes (covering most web-search wrappers). Never mutates the result returned to the
model; registers each detected source via CitationLedger. Silent no-op when no ledger
is bound (replay / eval / test paths).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from agent_runtime.capabilities.citations import CitationLedger, SourceRef


_LOGGER = logging.getLogger(__name__)


class CitationProjector:
    """Dispatch tool results through recognized shapes and register sources with the ledger."""

    class Limits:
        """Per-result source caps to bound registry size on high-volume connectors."""

        PER_RESULT_MAX = 25

    class Keys:
        """Result-shape field names — stable for back-compat."""

        CONTENT = "content"
        RESULTS = "results"
        RESOURCE = "resource"

        BLOCK_TYPE = "type"
        BLOCK_TYPE_TEXT = "text"
        BLOCK_TYPE_RESOURCE = "resource"

        URL = "url"
        LINK = "link"
        URI = "uri"
        ID = "id"
        TITLE = "title"
        NAME = "name"
        SNIPPET = "snippet"
        EXCERPT = "excerpt"
        SUMMARY = "summary"
        DESCRIPTION = "description"
        TEXT = "text"
        SOURCE = "source"

    @classmethod
    async def project(
        cls,
        *,
        connector: str,
        tool_call_id: str | None,
        result: object,
    ) -> None:
        """Detect sources in ``result`` and register them with the active ledger.

        Best-effort: returns silently when no ledger is bound or no
        recognized shape matches. Never raises into the tool path —
        a citation projection failure must not poison a successful
        tool result.

        Emits one ``sources_ingested`` event per tool result via
        :meth:`CitationLedger.register_many`.
        """

        ledger = CitationLedger.active()
        if ledger is None:
            return
        try:
            sources = list(cls._extract_sources(connector, result))
        except Exception:  # noqa: BLE001 - best-effort enrichment
            _LOGGER.warning(
                "Citation projector raised on %s; skipping",
                connector,
                exc_info=True,
            )
            return
        prepared = [
            source.model_copy(update={"source_tool_call_id": tool_call_id})
            for source in sources[: cls.Limits.PER_RESULT_MAX]
        ]
        if not prepared:
            return
        await ledger.register_many(prepared)

    # --- shape dispatcher --------------------------------------------------

    @classmethod
    def _extract_sources(cls, connector: str, result: object) -> Iterable[SourceRef]:
        """Dispatch ``result`` to the appropriate shape extractor."""
        if isinstance(result, list):
            yield from cls._from_results_list(connector, result)
            return
        if not isinstance(result, dict):
            return

        keys = cls.Keys
        yielded = False
        for ref in cls._from_content_blocks(connector, result.get(keys.CONTENT)):
            yielded = True
            yield ref
        if not yielded:
            # Priority ordering: content-blocks first, then results list, then
            # single-resource. Each shape is tried only when the higher-priority
            # shape produced nothing, to avoid double-registering the same source.
            for ref in cls._from_results_list(connector, result.get(keys.RESULTS)):
                yielded = True
                yield ref
        if not yielded:
            single = cls._from_single_resource(connector, result.get(keys.RESOURCE))
            if single is not None:
                yield single

    # --- recognized shapes -------------------------------------------------

    @classmethod
    def _from_content_blocks(
        cls, connector: str, content: object
    ) -> Iterable[SourceRef]:
        """Yield sources from Anthropic-style ``{"content": [...]}`` blocks."""
        if not isinstance(content, list):
            return
        keys = cls.Keys
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get(keys.BLOCK_TYPE)
            if block_type == keys.BLOCK_TYPE_TEXT:
                ref = cls._build(
                    connector=connector,
                    doc_id=cls._coerce_text(block.get(keys.URL))
                    or cls._coerce_text(block.get(keys.SOURCE)),
                    title=cls._coerce_text(block.get(keys.TITLE))
                    or cls._coerce_text(block.get(keys.NAME))
                    or cls._coerce_text(block.get(keys.URL)),
                    url=cls._coerce_text(block.get(keys.URL)),
                    snippet=cls._coerce_text(block.get(keys.TEXT)),
                )
                if ref is not None:
                    yield ref
            elif block_type == keys.BLOCK_TYPE_RESOURCE:
                resource = block.get(keys.RESOURCE)
                if isinstance(resource, dict):
                    yield from cls._yield_resource_refs(connector, resource)

    @classmethod
    def _from_results_list(cls, connector: str, results: object) -> Iterable[SourceRef]:
        """Yield sources from a list of result-dict entries (web-search style)."""
        if not isinstance(results, list):
            return
        keys = cls.Keys
        for entry in results:
            if not isinstance(entry, dict):
                continue
            url = (
                cls._coerce_text(entry.get(keys.URL))
                or cls._coerce_text(entry.get(keys.LINK))
                or cls._coerce_text(entry.get(keys.URI))
            )
            ref = cls._build(
                connector=connector,
                doc_id=cls._coerce_text(entry.get(keys.ID)) or url,
                title=cls._coerce_text(entry.get(keys.TITLE))
                or cls._coerce_text(entry.get(keys.NAME))
                or url,
                url=url,
                snippet=cls._coerce_text(entry.get(keys.SNIPPET))
                or cls._coerce_text(entry.get(keys.EXCERPT))
                or cls._coerce_text(entry.get(keys.SUMMARY)),
            )
            if ref is not None:
                yield ref

    @classmethod
    def _from_single_resource(
        cls, connector: str, resource: object
    ) -> SourceRef | None:
        """Return a single source from a ``{"resource": {...}}`` payload, or ``None``."""
        if not isinstance(resource, dict):
            return None
        refs = list(cls._yield_resource_refs(connector, resource))
        return refs[0] if refs else None

    @classmethod
    def _yield_resource_refs(
        cls, connector: str, resource: dict[str, Any]
    ) -> Iterable[SourceRef]:
        """Yield zero or one SourceRef from a raw MCP resource dict."""
        keys = cls.Keys
        ref = cls._build(
            connector=connector,
            doc_id=cls._coerce_text(resource.get(keys.URI))
            or cls._coerce_text(resource.get(keys.ID)),
            title=cls._coerce_text(resource.get(keys.TITLE))
            or cls._coerce_text(resource.get(keys.NAME))
            or cls._coerce_text(resource.get(keys.URI)),
            url=cls._coerce_text(resource.get(keys.URI)),
            snippet=cls._coerce_text(resource.get(keys.DESCRIPTION))
            or cls._coerce_text(resource.get(keys.CONTENT)),
        )
        if ref is not None:
            yield ref

    # --- builders ----------------------------------------------------------

    @classmethod
    def _build(
        cls,
        *,
        connector: str,
        doc_id: str | None,
        title: str | None,
        url: str | None,
        snippet: str | None,
    ) -> SourceRef | None:
        """Construct a SourceRef; return ``None`` when doc_id or title are absent."""
        if not doc_id or not title:
            return None
        return SourceRef(
            source_connector=connector,
            source_doc_id=doc_id,
            title=title,
            source_url=url,
            snippet=snippet,
        )

    @staticmethod
    def _coerce_text(value: object) -> str | None:
        """Return ``value`` stripped if it is a non-empty string, else ``None``."""
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None


__all__ = ("CitationProjector",)
