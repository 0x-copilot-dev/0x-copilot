"""Public ``/v1/library`` facade — thin proxy onto ``services/backend``.

Single source of truth is the backend; this module forwards the Phase 7
P7-A1 CRUD surface plus the P7.5-A4 hybrid search routes:

1. Authenticates the caller via :class:`FacadeAuthenticator`.
2. Forwards the request to ``backend`` with the verified identity in
   query params (dev fallback) and service-token headers (production).
3. Preserves multi-value ``filter[<axis>]=`` query semantics (cross-
   audit §1.5) by forwarding ``request.query_params.multi_items()``
   rather than ``dict(...)`` (which collapses repeats).
4. Forwards the ``If-Match`` header verbatim on PATCH so the page
   body-edit optimistic-concurrency contract survives the proxy.

Wire shape matches ``packages/api-types/src/library.ts``; see the
backend ``backend_app/library`` module for ACL + audit + canonical
membership-port semantics.

The five routes:
  * GET    /v1/library             — kind-agnostic list + filters
  * GET    /v1/library/{id}        — single item (file / page / dataset)
  * POST   /v1/library/pages       — create page (markdown body)
  * PATCH  /v1/library/{id}        — metadata + page body (If-Match)
  * DELETE /v1/library/{id}        — owner-only soft-delete

Out of scope of P7-A1 (other agents own these):

* ``POST /v1/library/files`` (signed-URL initiate) + ``…/finalize`` — P7-A2.
* ``POST /v1/library/datasets`` + ``…/finalize`` — P7-A2.
* ``GET /v1/library/{id}/preview`` + ``…/download`` — P7-A2.
* ``POST /v1/library/search`` + ``GET /v1/library/search/stream`` — P7-A3.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


def register_library_routes(app: FastAPI) -> None:
    """Attach ``/v1/library`` proxy routes to a facade FastAPI app."""

    @app.get("/v1/library")
    async def list_library(request: Request) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        # Forward the multi-value filter[*] params verbatim — list/tuple
        # values preserve repeats over the wire (httpx encodes
        # `params=[("filter[kind]","file"),("filter[kind]","page")]`
        # as `filter[kind]=file&filter[kind]=page`).
        forwarded_params: list[tuple[str, str]] = [
            ("org_id", identity.org_id),
            ("user_id", identity.user_id),
        ]
        for key, value in request.query_params.multi_items():
            if key in {"org_id", "user_id"}:
                continue
            forwarded_params.append((key, value))
        response = await client.get(
            f"{backend_url}/v1/library",
            params=forwarded_params,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.get("/v1/library/{item_id}")
    async def get_library_item(request: Request, item_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.get(
            f"{backend_url}/v1/library/{item_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.post("/v1/library/pages", status_code=status.HTTP_201_CREATED)
    async def create_library_page(request: Request) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/library/pages",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.patch("/v1/library/{item_id}")
    async def patch_library_item(request: Request, item_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        headers = FacadeAuthenticator.service_headers(identity)
        # If-Match is the optimistic-concurrency token for page body
        # edits — survive the proxy verbatim. Header is optional; only
        # present when the FE submits a markdown change on a page.
        if_match = request.headers.get("if-match")
        if if_match:
            headers["If-Match"] = if_match
        response = await client.patch(
            f"{backend_url}/v1/library/{item_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=headers,
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.get("/v1/library/search")
    async def search_library(request: Request) -> dict[str, object]:
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
            f"{backend_url}/v1/library/search",
            params=forwarded_params,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=30,
        )
        return _coerce_object_or_raise(response)

    @app.get("/v1/library/search/stream")
    async def search_library_stream(
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        # Pass-through SSE proxy. Mirrors the inbox stream pattern — we
        # never buffer; bytes ride the wire as the upstream wrote them
        # so the SSE framing (event:/id:/data:) stays byte-for-byte.
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
            outbound_headers["Last-Event-ID"] = last_event_id
        upstream = await client.send(
            client.build_request(
                "GET",
                f"{backend_url}/v1/library/search/stream",
                params=forwarded_params,
                headers=outbound_headers,
                timeout=None,
            ),
            stream=True,
        )
        if upstream.status_code >= 400:
            await upstream.aread()
            await upstream.aclose()
            _raise_for_upstream(upstream)

        async def event_stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_bytes():
                    if await request.is_disconnected():
                        break
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
        )

    @app.delete(
        "/v1/library/{item_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_library_item(request: Request, item_id: str) -> Response:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.delete(
            f"{backend_url}/v1/library/{item_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        if response.status_code >= 400:
            _raise_for_upstream(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers (same shape as inbox_routes / routines_routes / projects_routes)
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
    raise HTTPException(response.status_code, detail)


def _settings_for(app: FastAPI) -> FacadeSettings:
    return app.state.settings


__all__ = ["register_library_routes"]
