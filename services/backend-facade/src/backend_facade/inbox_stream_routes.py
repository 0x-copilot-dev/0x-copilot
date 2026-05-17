"""Public ``GET /v1/inbox/stream`` — Inbox destination SSE proxy (P4-A3).

Pass-through proxy onto ``services/backend`` ``GET /v1/inbox/stream``.
Identity is established via ``verify_with_touch`` (the same path every
other authenticated route uses); the verified ``org_id`` / ``user_id``
are forwarded as both query params (dev fallback) and service-token
headers (production path).

Cross-audit §5.2 — the facade does **not** buffer; it streams the
upstream chunks unmodified so the SSE framing (``event:``/``id:``/
``data:``) lands on the wire byte-for-byte. The pattern matches
``/v1/agent/runs/{run_id}/stream`` (already shipping in
``backend_facade/app.py``); we copy that proxy shape rather than invent
a parallel one. ``Last-Event-ID`` is part of the request headers
forwarded by the shared HTTP client, so reconnect-resume works through
the facade without any explicit code here.

P4-A1 coordination — when ``inbox_routes.py`` lands with CRUD handlers,
the orchestrator can fold ``register_inbox_stream_routes`` into the
same registration call OR keep it as a separate, narrowly-scoped file
(this file). Both are correct; merging into one file is the lower-
diff option but introduces a coordination touchpoint. We choose
separation here to keep the parallel waves cleanly mergeable.

Wire into the FastAPI app with ``register_inbox_stream_routes(app)``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Header, Query, Request
from fastapi.responses import StreamingResponse

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


# ---------------------------------------------------------------------------
# Constants — class-namespaced so call sites never inline a magic string.
# Mirrors the ai-backend / backend SSE adapter discipline.
# ---------------------------------------------------------------------------


class Constants:
    """Class-namespaced constants for the inbox SSE facade route."""

    class Sse:
        MEDIA_TYPE = "text/event-stream"
        UPSTREAM_PATH = "/v1/inbox/stream"

    class Headers:
        LAST_EVENT_ID = "Last-Event-ID"
        """Forwarded upstream so the backend's ``LastEventIdResolver``
        can resolve the reconnect cursor."""


def register_inbox_stream_routes(app: FastAPI) -> None:
    """Attach ``GET /v1/inbox/stream`` to a facade FastAPI app.

    Pass-through SSE proxy — see module docstring for design notes.
    """

    @app.get(Constants.Sse.UPSTREAM_PATH)
    async def stream_inbox(
        request: Request,
        after_sequence: int = Query(0, ge=0),
        last_event_id: str | None = Header(
            default=None, alias=Constants.Headers.LAST_EVENT_ID
        ),
    ) -> StreamingResponse:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )

        # Build the outbound request. ``timeout=None`` keeps the stream
        # open indefinitely; the pool's default timeout protects other
        # callers. Service headers carry the verified identity.
        outbound_headers = dict(FacadeAuthenticator.service_headers(identity))
        if last_event_id is not None:
            outbound_headers[Constants.Headers.LAST_EVENT_ID] = last_event_id

        upstream = await client.send(
            client.build_request(
                "GET",
                f"{backend_url}{Constants.Sse.UPSTREAM_PATH}",
                params={
                    "org_id": identity.org_id,
                    "user_id": identity.user_id,
                    "after_sequence": after_sequence,
                },
                headers=outbound_headers,
                timeout=None,
            ),
            stream=True,
        )

        if upstream.status_code >= 400:
            # Drain + close so the connection returns to the pool; surface
            # the upstream error faithfully (cross-audit: facade is a thin
            # proxy, never invent its own error semantics).
            await upstream.aread()
            await upstream.aclose()
            raise HTTPException(upstream.status_code, _upstream_error_detail(upstream))

        async def event_stream() -> AsyncIterator[bytes]:
            # Pass-through: yield upstream chunks unmodified so SSE framing
            # (``event:``/``id:``/``data:``) lands on the wire byte-for-byte.
            # Heartbeats are emitted upstream; we do not synthesise our own.
            try:
                async for chunk in upstream.aiter_bytes():
                    if await request.is_disconnected():
                        break
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            event_stream(),
            media_type=Constants.Sse.MEDIA_TYPE,
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
        )


def _upstream_error_detail(response: httpx.Response) -> object:
    detail: object
    try:
        payload = response.json()
    except ValueError:
        detail = response.text or "Upstream request failed"
    else:
        if isinstance(payload, dict) and "detail" in payload:
            detail = payload["detail"]
        else:
            detail = payload if payload else "Upstream request failed"
    return detail


def _settings_for(app: FastAPI) -> FacadeSettings:
    return app.state.settings


__all__ = ["register_inbox_stream_routes"]
