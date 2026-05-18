"""Public ``/v1/tools`` facade — thin proxy onto ``services/backend``.

Phase 10 P10-A4. Single source of truth is the backend; this module is
a thin forwarder for the entire Tools destination wire surface defined
in ``docs/atlas-new-design/destinations/tools-prd.md`` §4:

1. Authenticates the caller via :class:`FacadeAuthenticator`.
2. Forwards the request to ``backend`` with the verified identity in
   query params (dev fallback) and service-token headers (production).
3. Preserves multi-value ``filter[<axis>]=`` query semantics
   (cross-audit §1.5) by forwarding ``request.query_params.multi_items()``
   rather than ``dict(...)`` (which collapses repeats).
4. Streams ``/v1/tools/stream`` byte-for-byte so SSE framing
   (``event:``/``id:``/``data:``) lands on the wire unchanged.

Wire shape matches ``packages/api-types/src/tools.ts`` (P10-A2). The
facade does not own any of the tool registration, ACL, audit, or
projection logic — every meaningful response is constructed by
``services/backend`` (``backend_app/tools/``).

Routes:
  * GET    /v1/tools                       — list/search (filters + sort)
  * GET    /v1/tools/{id}                  — detail
  * POST   /v1/tools                       — register
  * PATCH  /v1/tools/{id}                  — edit
  * DELETE /v1/tools/{id}                  — soft-delete
  * POST   /v1/tools/{id}/test             — test-call
  * POST   /v1/tools/{id}/disable          — mark disabled
  * POST   /v1/tools/{id}/enable           — re-enable a disabled tool
  * GET    /v1/tools/{id}/invocations      — invocation history (paginated)
  * GET    /v1/tools/{id}/usage            — usage projection (24h/7d/30d)
  * GET    /v1/tools/stream                — SSE (Last-Event-ID resume)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


# ---------------------------------------------------------------------------
# Constants — class-namespaced so call sites never inline a magic string.
# Mirrors the inbox / home stream proxies.
# ---------------------------------------------------------------------------


class Constants:
    """Class-namespaced constants for the tools facade routes."""

    class Paths:
        LIST = "/v1/tools"
        ITEM = "/v1/tools/{tool_id}"
        TEST = "/v1/tools/{tool_id}/test"
        DISABLE = "/v1/tools/{tool_id}/disable"
        ENABLE = "/v1/tools/{tool_id}/enable"
        INVOCATIONS = "/v1/tools/{tool_id}/invocations"
        USAGE = "/v1/tools/{tool_id}/usage"
        STREAM = "/v1/tools/stream"

    class Sse:
        MEDIA_TYPE = "text/event-stream"

    class Headers:
        LAST_EVENT_ID = "Last-Event-ID"
        """Forwarded upstream so the backend's ``LastEventIdResolver``
        can resolve the reconnect cursor."""


def register_tool_routes(app: FastAPI) -> None:
    """Attach ``/v1/tools/*`` proxy routes to a facade FastAPI app."""

    # ----- List + filters -------------------------------------------------

    @app.get(Constants.Paths.LIST)
    async def list_tools(request: Request) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        # Forward the multi-value filter[*] params verbatim — list/tuple
        # values preserve repeats over the wire so ``kind`` / ``status``
        # OR-semantics survive the proxy.
        forwarded_params: list[tuple[str, str]] = [
            ("org_id", identity.org_id),
            ("user_id", identity.user_id),
        ]
        for key, value in request.query_params.multi_items():
            if key in {"org_id", "user_id"}:
                continue
            forwarded_params.append((key, value))
        response = await client.get(
            f"{backend_url}{Constants.Paths.LIST}",
            params=forwarded_params,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- SSE stream — declared BEFORE ``/{tool_id}`` so the literal
    # path takes precedence over the path-parameter route in FastAPI's
    # router. (FastAPI matches in registration order; declaring the
    # stream first guarantees ``/v1/tools/stream`` is not swallowed by
    # ``/v1/tools/{tool_id}``.)

    @app.get(Constants.Paths.STREAM)
    async def stream_tools(
        request: Request,
        last_event_id: str | None = Header(
            default=None, alias=Constants.Headers.LAST_EVENT_ID
        ),
    ) -> StreamingResponse:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )

        forwarded_params: list[tuple[str, str]] = [
            ("org_id", identity.org_id),
            ("user_id", identity.user_id),
        ]
        for key, value in request.query_params.multi_items():
            if key in {"org_id", "user_id"}:
                continue
            forwarded_params.append((key, value))

        outbound_headers = dict(FacadeAuthenticator.service_headers(identity))
        if last_event_id is not None:
            outbound_headers[Constants.Headers.LAST_EVENT_ID] = last_event_id

        upstream = await client.send(
            client.build_request(
                "GET",
                f"{backend_url}{Constants.Paths.STREAM}",
                params=forwarded_params,
                headers=outbound_headers,
                timeout=None,
            ),
            stream=True,
        )

        if upstream.status_code >= 400:
            # Drain + close so the pool reclaims the socket; surface the
            # upstream error faithfully (facade is a thin proxy).
            await upstream.aread()
            await upstream.aclose()
            raise HTTPException(upstream.status_code, _upstream_error_detail(upstream))

        async def event_stream() -> AsyncIterator[bytes]:
            # Pass-through: yield upstream chunks unmodified so SSE
            # framing lands on the wire byte-for-byte. Heartbeats are
            # emitted upstream; we do not synthesise our own.
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

    # ----- Detail ---------------------------------------------------------

    @app.get(Constants.Paths.ITEM)
    async def get_tool(request: Request, tool_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.get(
            f"{backend_url}/v1/tools/{tool_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Register -------------------------------------------------------

    @app.post(Constants.Paths.LIST, status_code=status.HTTP_201_CREATED)
    async def register_tool(request: Request) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}{Constants.Paths.LIST}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Edit -----------------------------------------------------------

    @app.patch(Constants.Paths.ITEM)
    async def patch_tool(request: Request, tool_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.patch(
            f"{backend_url}/v1/tools/{tool_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Soft-delete ----------------------------------------------------

    @app.delete(Constants.Paths.ITEM, status_code=status.HTTP_204_NO_CONTENT)
    async def delete_tool(request: Request, tool_id: str) -> Response:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.delete(
            f"{backend_url}/v1/tools/{tool_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        if response.status_code >= 400:
            _raise_for_upstream(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ----- Test-call ------------------------------------------------------

    @app.post(Constants.Paths.TEST)
    async def test_tool(request: Request, tool_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/tools/{tool_id}/test",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=30,
        )
        return _coerce_object_or_raise(response)

    # ----- Disable / Enable ----------------------------------------------

    @app.post(Constants.Paths.DISABLE)
    async def disable_tool(request: Request, tool_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        # Optional reason body — default to {} so the backend can
        # validate the shape. Disable is owner-or-admin; ACL on backend.
        body: dict[str, object] = {}
        if request.headers.get("content-length") not in (None, "0"):
            body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/tools/{tool_id}/disable",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.post(Constants.Paths.ENABLE)
    async def enable_tool(request: Request, tool_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body: dict[str, object] = {}
        if request.headers.get("content-length") not in (None, "0"):
            body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/tools/{tool_id}/enable",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Invocation history --------------------------------------------

    @app.get(Constants.Paths.INVOCATIONS)
    async def list_tool_invocations(
        request: Request, tool_id: str
    ) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        forwarded_params: list[tuple[str, str]] = [
            ("org_id", identity.org_id),
            ("user_id", identity.user_id),
        ]
        for key, value in request.query_params.multi_items():
            if key in {"org_id", "user_id"}:
                continue
            forwarded_params.append((key, value))
        response = await client.get(
            f"{backend_url}/v1/tools/{tool_id}/invocations",
            params=forwarded_params,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Usage projection ----------------------------------------------

    @app.get(Constants.Paths.USAGE)
    async def get_tool_usage(request: Request, tool_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.get(
            f"{backend_url}/v1/tools/{tool_id}/usage",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)


# ---------------------------------------------------------------------------
# Helpers (same shape as library_routes / routines_routes / projects_routes)
# ---------------------------------------------------------------------------


async def _safe_json(request: Request) -> dict[str, object]:
    """Pass through the request body, defaulting empty bodies to ``{}``."""

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "request_body_must_be_object")
    return body


def _coerce_object_or_raise(response: httpx.Response) -> dict[str, object]:
    if response.status_code >= 400:
        _raise_for_upstream(response)
    if response.status_code == 204 or not response.content:
        return {}
    payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Upstream response was not an object"
        )
    return payload


def _raise_for_upstream(response: httpx.Response) -> None:
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


__all__ = ["register_tool_routes"]
