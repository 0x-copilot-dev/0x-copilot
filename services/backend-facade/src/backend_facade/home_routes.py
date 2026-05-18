"""Public ``GET /v1/home`` + ``GET /v1/home/stream`` — Home destination.

Both routes are thin proxies onto ``services/backend``:

* ``GET /v1/home`` — JSON aggregator (morning briefing). Body is the
  Phase 9 ``HomePayload`` shape from ``packages/api-types/src/home.ts``;
  the facade does not transform it.
* ``GET /v1/home/stream`` — SSE stream (LiveActivityRail). Pass-through
  proxy mirroring ``inbox_stream_routes.py``; the facade does not
  buffer so the SSE framing lands on the wire byte-for-byte and
  ``Last-Event-ID`` reconnect-resume works end-to-end.

Identity is established via ``verify_with_touch`` (the same path every
other authenticated route uses); the verified ``org_id`` / ``user_id``
ride as query params (dev fallback) AND as service-token headers
(production path). Backend owns the aggregation logic — the facade
owns nothing here.

Wire into the FastAPI app with ``register_home_routes(app)``.
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
# ---------------------------------------------------------------------------


class Constants:
    """Class-namespaced constants for the home facade routes."""

    class Paths:
        AGGREGATOR = "/v1/home"
        STREAM = "/v1/home/stream"

    class Sse:
        MEDIA_TYPE = "text/event-stream"

    class Headers:
        LAST_EVENT_ID = "Last-Event-ID"
        """Forwarded upstream so the backend's ``LastEventIdResolver``
        can resolve the reconnect cursor."""


def register_home_routes(app: FastAPI) -> None:
    """Attach ``GET /v1/home`` + ``GET /v1/home/stream`` to a facade app."""

    @app.get(Constants.Paths.AGGREGATOR)
    async def get_home(request: Request) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.get(
            f"{backend_url}{Constants.Paths.AGGREGATOR}",
            params={
                "org_id": identity.org_id,
                "user_id": identity.user_id,
            },
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        _raise_for_upstream(response)
        return response.json()

    @app.get(Constants.Paths.STREAM)
    async def stream_home(
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
                f"{backend_url}{Constants.Paths.STREAM}",
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
            # Drain + close so the connection returns to the pool;
            # surface the upstream error faithfully (cross-audit: facade
            # is a thin proxy, never invent its own error semantics).
            await upstream.aread()
            await upstream.aclose()
            raise HTTPException(upstream.status_code, _upstream_error_detail(upstream))

        async def event_stream() -> AsyncIterator[bytes]:
            # Pass-through: yield upstream chunks unmodified so SSE
            # framing (``event:``/``id:``/``data:``) lands on the wire
            # byte-for-byte. Heartbeats are emitted upstream; we do not
            # synthesise our own.
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


def _raise_for_upstream(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise HTTPException(response.status_code, _upstream_error_detail(response))


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


__all__ = ["register_home_routes"]
