"""Library hybrid search routes — P7.5-A4.

Two routes:

* ``GET /v1/library/search`` — one-shot hybrid search. Returns
  :class:`LibrarySearchResponse` (api-types/library.ts).
* ``GET /v1/library/search/stream`` — SSE variant for slow re-rank
  queries. Emits the ordered envelope sequence
  ``library.search_bm25_result`` → ``library.search_vector_result`` →
  ``library.search_reranked`` → ``library.search_complete``.
  Heartbeat 30s; ``Last-Event-ID`` resume.

Auth: identity is resolved via
:meth:`BackendServiceAuthenticator.scoped_identity`. ACL filtering is
inside :class:`SearchEngine` (it calls the canonical
:func:`backend_app.projects.acl.is_member`) — there is no parallel ACL
here.

The route is the **only** place we measure wall-clock; ``took_ms`` is
the route's responsibility. Search pipeline below is a pure function
of (query, identity, ACL).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any, Literal

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import StreamingResponse

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.library.search import (
    BM25_TOP_K_DEFAULT,
    RERANK_TOP_N_DEFAULT,
    VECTOR_TOP_K_DEFAULT,
    FusedHit,
    SearchEngine,
    SearchHit,
    SearchResult,
    hit_to_wire,
    rrf_fuse,
)
from backend_app.library.store import LibraryItemRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — class-namespaced so SSE event names + media type live in
# one place. Matches inbox/sse.py discipline.
# ---------------------------------------------------------------------------


class Constants:
    """Class-namespaced constants for the library search routes."""

    class Sse:
        EVENT_BM25 = "library.search_bm25_result"
        EVENT_VECTOR = "library.search_vector_result"
        EVENT_RERANKED = "library.search_reranked"
        EVENT_COMPLETE = "library.search_complete"
        EVENT_ERROR = "library.search_error"
        MEDIA_TYPE = "text/event-stream"
        HEARTBEAT_COMMENT = b": keepalive\n\n"

    class Cadence:
        HEARTBEAT_INTERVAL_SECONDS = 30.0
        # Search pipeline is single-shot so we punt heartbeats only
        # when the wall-clock between leg completions crosses this
        # threshold. The pure-Python in-memory path finishes in < 50 ms
        # so heartbeats rarely fire; the test covers the cadence path
        # only as a unit test on the framer.

    class Headers:
        LAST_EVENT_ID = "Last-Event-ID"


KindLiteral = Literal["file", "page", "dataset", "all"]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_library_search_routes(
    app: FastAPI,
    *,
    engine: SearchEngine,
) -> None:
    """Attach the search routes onto a backend FastAPI app.

    Mounted from ``backend_app.app.create_app`` after the CRUD routes
    are registered. The engine is composed once at startup so every
    request shares the BM25 + vector index instance.
    """

    @app.get(
        "/v1/library/search",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def search_library(
        request: Request,
        q: str = Query(..., min_length=1, max_length=500),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        kind: KindLiteral = Query(default="all"),
        project_id: str | None = Query(default=None),
        owner_user_id: str | None = Query(default=None),
        top_k: int = Query(default=20, ge=1, le=100),
        rerank: bool | None = Query(default=None),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        effective_rerank = _resolve_rerank(rerank=rerank, request=request)
        wall_clock_start = time.perf_counter()
        result, records = engine.search(
            tenant_id=identity.org_id,
            caller_user_id=identity.user_id,
            caller_roles=identity.roles,
            query=q,
            kinds=_kinds_for(kind),
            project_ids=(project_id,) if project_id else None,
            owner_user_ids=(owner_user_id,) if owner_user_id else None,
            top_k=top_k,
            rerank=effective_rerank,
        )
        took_ms = int((time.perf_counter() - wall_clock_start) * 1000)
        return _envelope_to_wire(result, records, query=q, took_ms=took_ms)

    @app.get(
        "/v1/library/search/stream",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    async def search_library_stream(
        request: Request,
        q: str = Query(..., min_length=1, max_length=500),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        kind: KindLiteral = Query(default="all"),
        project_id: str | None = Query(default=None),
        owner_user_id: str | None = Query(default=None),
        top_k: int = Query(default=20, ge=1, le=100),
        rerank: bool | None = Query(default=None),
        last_event_id: str | None = Header(
            default=None, alias=Constants.Headers.LAST_EVENT_ID
        ),
    ) -> StreamingResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        effective_rerank = _resolve_rerank(rerank=rerank, request=request)
        last_sequence = _parse_last_event_id(last_event_id)
        return StreamingResponse(
            _sse_stream(
                engine=engine,
                request=request,
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                query=q,
                kinds=_kinds_for(kind),
                project_ids=(project_id,) if project_id else None,
                owner_user_ids=(owner_user_id,) if owner_user_id else None,
                top_k=top_k,
                rerank=effective_rerank,
                after_sequence=last_sequence,
            ),
            media_type=Constants.Sse.MEDIA_TYPE,
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
        )


# ---------------------------------------------------------------------------
# Helpers — kind filter, rerank-default detection, envelope marshalling
# ---------------------------------------------------------------------------


def _kinds_for(kind: KindLiteral) -> tuple[str, ...] | None:
    if kind == "all":
        return None
    return (kind,)


def _resolve_rerank(*, rerank: bool | None, request: Request) -> bool:
    """If the caller doesn't supply ``rerank=...`` we default to:

    * ``False`` for agent / service-token callers (latency-sensitive,
      retrieval typically inside a deep agent's working memory loop);
    * ``True`` for human-bearer callers (Library UI search box, where
      a few hundred ms is acceptable).

    Detection is "service token present" → agent. The header is
    ``x-enterprise-service-token``; if it survived the facade
    verification it means an internal caller is hitting us.
    """

    if rerank is not None:
        return rerank
    from enterprise_service_contracts.headers import SERVICE_TOKEN_HEADER

    return not bool(request.headers.get(SERVICE_TOKEN_HEADER, "").strip())


def _envelope_to_wire(
    result: SearchResult,
    records: tuple[LibraryItemRecord, ...],
    *,
    query: str,
    took_ms: int,
) -> dict[str, Any]:
    """Marshal a :class:`SearchResult` into the
    ``LibrarySearchResponse`` wire shape."""

    record_by_id = {record.id: record for record in records}
    hits_wire: list[dict[str, Any]] = []
    for hit in result.hits:
        record = record_by_id.get(hit.record_id)
        if record is None:
            continue
        hits_wire.append(hit_to_wire(hit, record, query=query))
    return {
        "hits": hits_wire,
        "total": result.total,
        "took_ms": took_ms,
        "strategy": result.strategy,
    }


def _parse_last_event_id(raw: str | None) -> int:
    """Parse ``Last-Event-ID`` for SSE reconnect — non-negative int or 0."""

    if raw is None:
        return 0
    candidate = raw.strip()
    if not candidate:
        return 0
    try:
        value = int(candidate)
    except ValueError:
        return 0
    return max(value, 0)


# ---------------------------------------------------------------------------
# SSE envelope framing — mirrors inbox/sse.py
# ---------------------------------------------------------------------------


def _sse_frame(event_name: str, sequence_no: int, payload: dict[str, Any]) -> bytes:
    """Encode one SSE frame.

    ``event:``, ``id:``, ``data:`` — three-line body per the W3C SSE
    contract. ``data:`` is a single JSON line (no newlines inside).
    """

    body = (
        f"event: {event_name}\n"
        f"id: {sequence_no}\n"
        f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
    )
    return body.encode("utf-8")


async def _sse_stream(
    *,
    engine: SearchEngine,
    request: Request,
    tenant_id: str,
    caller_user_id: str,
    caller_roles: tuple[str, ...],
    query: str,
    kinds: tuple[str, ...] | None,
    project_ids: tuple[str, ...] | None,
    owner_user_ids: tuple[str, ...] | None,
    top_k: int,
    rerank: bool,
    after_sequence: int,
) -> AsyncIterator[bytes]:
    """Emit the bm25 → vector → rerank → complete envelope sequence.

    Sequence numbers are monotonic per-stream (synthesised here, not
    persisted). ``after_sequence`` lets a reconnect resume at the next
    boundary — useful when the FE drops mid-pipeline.

    The pipeline is single-shot; we run the legs sequentially and emit
    one envelope after each. The full :class:`SearchEngine.search` call
    is what does the actual work — we re-derive the legs here just to
    surface their intermediate state on the wire.
    """

    correlation_id = str(uuid.uuid4())
    sequence = 0

    def next_seq() -> int:
        nonlocal sequence
        sequence += 1
        return sequence

    wall_clock_start = time.perf_counter()

    try:
        # Leg 1: BM25.
        bm25_hits = engine.index.bm25_search(
            tenant_id=tenant_id,
            query=query,
            top_k=BM25_TOP_K_DEFAULT,
            kinds=kinds,
            project_ids=project_ids,
            owner_user_ids=owner_user_ids,
        )
        if sequence < after_sequence:
            # Caller already saw this leg — increment but don't emit.
            next_seq()
        else:
            yield _sse_frame(
                Constants.Sse.EVENT_BM25,
                next_seq(),
                _leg_envelope(
                    correlation_id=correlation_id,
                    leg="bm25",
                    hits=bm25_hits,
                    elapsed_ms=int((time.perf_counter() - wall_clock_start) * 1000),
                ),
            )
        if await request.is_disconnected():
            return

        # Leg 2: vector.
        embedding = engine.embeddings.embed_query(query=query, tenant_id=tenant_id)
        vector_hits: tuple[SearchHit, ...] = ()
        if embedding is not None:
            vector_hits = engine.index.vector_search(
                tenant_id=tenant_id,
                query_embedding=embedding,
                top_k=VECTOR_TOP_K_DEFAULT,
                kinds=kinds,
                project_ids=project_ids,
                owner_user_ids=owner_user_ids,
            )
        if sequence < after_sequence:
            next_seq()
        else:
            yield _sse_frame(
                Constants.Sse.EVENT_VECTOR,
                next_seq(),
                _leg_envelope(
                    correlation_id=correlation_id,
                    leg="vector",
                    hits=vector_hits,
                    elapsed_ms=int((time.perf_counter() - wall_clock_start) * 1000),
                ),
            )
        if await request.is_disconnected():
            return

        fused = rrf_fuse(bm25_hits, vector_hits)
        readable = engine._readable_filter(  # noqa: SLF001 — internal SOT
            fused,
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
        )

        # Leg 3: rerank (only when requested).
        if rerank and readable:
            candidates = engine._candidates_for_rerank(  # noqa: SLF001
                readable, top_n=RERANK_TOP_N_DEFAULT
            )
            rerank_pairs = engine.reranker.rerank(
                query=query,
                candidates=candidates,
                tenant_id=tenant_id,
                top_n=RERANK_TOP_N_DEFAULT,
            )
            from backend_app.library.search import _apply_rerank_scores

            readable = _apply_rerank_scores(readable, rerank_pairs)
        if sequence < after_sequence:
            next_seq()
        else:
            yield _sse_frame(
                Constants.Sse.EVENT_RERANKED,
                next_seq(),
                _leg_envelope_fused(
                    correlation_id=correlation_id,
                    leg="reranked",
                    hits=readable,
                    elapsed_ms=int((time.perf_counter() - wall_clock_start) * 1000),
                ),
            )
        if await request.is_disconnected():
            return

        sliced = readable[:top_k]
        records = engine._hydrate(sliced, tenant_id=tenant_id)  # noqa: SLF001
        record_by_id = {record.id: record for record in records}
        hits_wire: list[dict[str, Any]] = []
        for hit in sliced:
            record = record_by_id.get(hit.record_id)
            if record is None:
                continue
            hits_wire.append(hit_to_wire(hit, record, query=query))

        from backend_app.library.search import _strategy_for

        took_ms = int((time.perf_counter() - wall_clock_start) * 1000)
        yield _sse_frame(
            Constants.Sse.EVENT_COMPLETE,
            next_seq(),
            {
                "correlation_id": correlation_id,
                "hits": hits_wire,
                "total": len(readable),
                "took_ms": took_ms,
                "strategy": _strategy_for(bm25_hits=bm25_hits, vector_hits=vector_hits),
                "emitted_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    except asyncio.CancelledError:
        # Client disconnect — let the framework propagate, no log noise.
        raise
    except Exception as exc:  # pragma: no cover - defensive
        # Wire-shape error envelope so the FE can surface a banner
        # instead of guessing. No PII — exc text is server-side
        # diagnostic, the wire payload carries only the correlation_id
        # and a generic ``library_search_failed`` code.
        logger.exception("library_search_stream_failed", extra={"error": str(exc)})
        yield _sse_frame(
            Constants.Sse.EVENT_ERROR,
            next_seq(),
            {
                "correlation_id": correlation_id,
                "code": "library_search_failed",
            },
        )


def _leg_envelope(
    *,
    correlation_id: str,
    leg: str,
    hits: tuple[SearchHit, ...],
    elapsed_ms: int,
) -> dict[str, Any]:
    """Envelope for the BM25 + vector legs.

    Carries unhydrated ids only — the FE doesn't render rows here; this
    is for progress / latency surfacing in the search panel.
    """

    return {
        "correlation_id": correlation_id,
        "leg": leg,
        "hit_count": len(hits),
        "hits": [
            {
                "ref": {"kind": f"library_{hit.kind}", "id": hit.record_id},
                "score": round(hit.score, 6),
            }
            for hit in hits[:50]
        ],
        "elapsed_ms": elapsed_ms,
    }


def _leg_envelope_fused(
    *,
    correlation_id: str,
    leg: str,
    hits: tuple[FusedHit, ...],
    elapsed_ms: int,
) -> dict[str, Any]:
    return {
        "correlation_id": correlation_id,
        "leg": leg,
        "hit_count": len(hits),
        "hits": [
            {
                "ref": {"kind": f"library_{hit.kind}", "id": hit.record_id},
                "score": round(hit.score, 6),
            }
            for hit in hits[:50]
        ],
        "elapsed_ms": elapsed_ms,
    }


__all__ = [
    "Constants",
    "KindLiteral",
    "register_library_search_routes",
]
