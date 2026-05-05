"""Generic citation projector for MCP tool results (PR 1.1 follow-up C).

Pattern-matches the three MCP result shapes that produce ~90% of citations
in the wild — Anthropic-style ``content`` blocks, generic ``results``
lists, and single ``resource`` reads — and registers each detected source
through :class:`CitationLedger`. Tool authors get citations for free
without per-tool wiring; the ledger is the single seam.

Important: the projector does NOT mutate the structured result returned to
the model. The model wasn't trained to expect ``[c<id>]`` tokens inside
JSON results, and rewriting them would break tools that read the result
back. Inline chips for MCP-derived sources require an opt-in shape change
documented in ``docs/new-design/02-citations-followups.md`` §C.

Best-effort: when no ledger is bound (citations disabled, or no run
context — e.g. eval / replay), the projector is a silent no-op.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from agent_runtime.capabilities.citations import CitationLedger, SourceRef


_LOGGER = logging.getLogger(__name__)


class _Limits:
    """Per-result caps to keep the registry tiny on noisy connectors."""

    PER_RESULT_MAX = 25


class CitationProjectingMcpMiddleware:
    """Stateless extractor: tool result → list of registered sources."""

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
        """

        ledger = CitationLedger.active()
        if ledger is None or not isinstance(result, dict):
            return
        try:
            sources = list(cls._extract_sources(connector, result))
        except Exception:  # noqa: BLE001 - best-effort enrichment
            _LOGGER.warning(
                "MCP citation projector raised on %s; skipping",
                connector,
                exc_info=True,
            )
            return
        for source in sources[: _Limits.PER_RESULT_MAX]:
            await ledger.register(
                source.model_copy(update={"source_tool_call_id": tool_call_id})
            )

    # --- recognized shapes -------------------------------------------------

    @classmethod
    def _extract_sources(
        cls, connector: str, result: dict[str, Any]
    ) -> Iterable[SourceRef]:
        """Yield :class:`SourceRef` for every recognized source in ``result``.

        The three shapes (in priority order) are:

        1. Anthropic MCP content blocks: ``{"content": [{"type":"text",
           "text": "...", "url": "..."}, {"type":"resource",
           "resource":{"uri":..., "name":...}}, ...]}``.
        2. Generic results list: ``{"results": [{"id":..., "title":...,
           "url":..., "snippet":...}, ...]}``.
        3. Single resource read: ``{"resource":{"uri":..., "title":...,
           "name":..., "content":...}}``.

        A result that matches none of these yields nothing.
        """

        yielded = False
        for ref in cls._from_content_blocks(connector, result.get("content")):
            yielded = True
            yield ref
        if not yielded:
            for ref in cls._from_results_list(connector, result.get("results")):
                yielded = True
                yield ref
        if not yielded:
            single = cls._from_single_resource(connector, result.get("resource"))
            if single is not None:
                yield single

    @classmethod
    def _from_content_blocks(
        cls, connector: str, content: object
    ) -> Iterable[SourceRef]:
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                ref = cls._build(
                    connector=connector,
                    doc_id=cls._coerce_text(block.get("url"))
                    or cls._coerce_text(block.get("source")),
                    title=cls._coerce_text(block.get("title"))
                    or cls._coerce_text(block.get("name"))
                    or cls._coerce_text(block.get("url")),
                    url=cls._coerce_text(block.get("url")),
                    snippet=cls._coerce_text(block.get("text")),
                )
                if ref is not None:
                    yield ref
            elif block_type == "resource":
                resource = block.get("resource")
                if isinstance(resource, dict):
                    yield from cls._yield_resource_refs(connector, resource)

    @classmethod
    def _from_results_list(cls, connector: str, results: object) -> Iterable[SourceRef]:
        if not isinstance(results, list):
            return
        for entry in results:
            if not isinstance(entry, dict):
                continue
            ref = cls._build(
                connector=connector,
                doc_id=cls._coerce_text(entry.get("id"))
                or cls._coerce_text(entry.get("url"))
                or cls._coerce_text(entry.get("uri")),
                title=cls._coerce_text(entry.get("title"))
                or cls._coerce_text(entry.get("name")),
                url=cls._coerce_text(entry.get("url"))
                or cls._coerce_text(entry.get("uri")),
                snippet=cls._coerce_text(entry.get("snippet"))
                or cls._coerce_text(entry.get("excerpt"))
                or cls._coerce_text(entry.get("summary")),
            )
            if ref is not None:
                yield ref

    @classmethod
    def _from_single_resource(
        cls, connector: str, resource: object
    ) -> SourceRef | None:
        if not isinstance(resource, dict):
            return None
        refs = list(cls._yield_resource_refs(connector, resource))
        return refs[0] if refs else None

    @classmethod
    def _yield_resource_refs(
        cls, connector: str, resource: dict[str, Any]
    ) -> Iterable[SourceRef]:
        ref = cls._build(
            connector=connector,
            doc_id=cls._coerce_text(resource.get("uri"))
            or cls._coerce_text(resource.get("id")),
            title=cls._coerce_text(resource.get("title"))
            or cls._coerce_text(resource.get("name"))
            or cls._coerce_text(resource.get("uri")),
            url=cls._coerce_text(resource.get("uri")),
            snippet=cls._coerce_text(resource.get("description"))
            or cls._coerce_text(resource.get("content")),
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
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None
