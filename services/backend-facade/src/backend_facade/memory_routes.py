"""Public ``/v1/memory`` facade — thin proxy onto ``services/backend``.

Phase 12 P12-A7. Mirrors ``tool_routes.py`` / ``team_routes.py``: every
method authenticates via :class:`FacadeAuthenticator`, forwards the
verified identity in query params + service-token headers, and proxies
the upstream byte-for-byte (including the SSE stream).

Routes (sub-PRD §4.2):
  * GET    /v1/memory                       — list / filter
  * GET    /v1/memory/search                — semantic search
  * GET    /v1/memory/{id}                  — detail
  * POST   /v1/memory                       — create
  * PATCH  /v1/memory/{id}                  — update / scope flip
  * DELETE /v1/memory/{id}                  — soft-delete
  * POST   /v1/memory/{id}/touch            — runtime last_used_at bump
  * GET    /v1/memory/proposals             — pending auto-extraction queue
  * POST   /v1/memory/proposals/{id}/accept — admin accept
  * POST   /v1/memory/proposals/{id}/reject — admin reject
  * GET    /v1/memory/stream                — SSE (Last-Event-ID resume)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


class Constants:
    """Class-namespaced constants for the memory facade routes."""

    class Paths:
        LIST = "/v1/memory"
        ITEM = "/v1/memory/{memory_id}"
        SEARCH = "/v1/memory/search"
        TOUCH = "/v1/memory/{memory_id}/touch"
        PROPOSALS = "/v1/memory/proposals"
        PROPOSAL_ACCEPT = "/v1/memory/proposals/{proposal_id}/accept"
        PROPOSAL_REJECT = "/v1/memory/proposals/{proposal_id}/reject"
        STREAM = "/v1/memory/stream"

    class Sse:
        MEDIA_TYPE = "text/event-stream"

    class Headers:
        LAST_EVENT_ID = "Last-Event-ID"


def register_memory_routes(app: FastAPI) -> None:
    """Attach ``/v1/memory/*`` proxy routes to a facade FastAPI app."""

    # ----- List + filters -------------------------------------------------

    @app.get(Constants.Paths.LIST)
    async def list_memory(request: Request) -> dict[str, object]:
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
            f"{backend_url}{Constants.Paths.LIST}",
            params=forwarded_params,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- SSE stream (declared before path-parameter routes) ------------

    @app.get(Constants.Paths.STREAM)
    async def stream_memory(
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
            await upstream.aread()
            await upstream.aclose()
            raise HTTPException(upstream.status_code, _upstream_error_detail(upstream))

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
            media_type=Constants.Sse.MEDIA_TYPE,
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
        )

    # ----- Search --------------------------------------------------------

    @app.get(Constants.Paths.SEARCH)
    async def search_memory(request: Request) -> dict[str, object]:
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
            f"{backend_url}{Constants.Paths.SEARCH}",
            params=forwarded_params,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Proposals (admin) ---------------------------------------------

    @app.get(Constants.Paths.PROPOSALS)
    async def list_proposals(request: Request) -> dict[str, object]:
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
            f"{backend_url}{Constants.Paths.PROPOSALS}",
            params=forwarded_params,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.post(Constants.Paths.PROPOSAL_ACCEPT)
    async def accept_proposal(request: Request, proposal_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/memory/proposals/{proposal_id}/accept",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.post(Constants.Paths.PROPOSAL_REJECT)
    async def reject_proposal(request: Request, proposal_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body: dict[str, object] = {}
        if request.headers.get("content-length") not in (None, "0"):
            body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/memory/proposals/{proposal_id}/reject",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Detail --------------------------------------------------------

    @app.get(Constants.Paths.ITEM)
    async def get_memory(request: Request, memory_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.get(
            f"{backend_url}/v1/memory/{memory_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Create --------------------------------------------------------

    @app.post(Constants.Paths.LIST, status_code=status.HTTP_201_CREATED)
    async def create_memory(request: Request) -> dict[str, object]:
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

    # ----- Update --------------------------------------------------------

    @app.patch(Constants.Paths.ITEM)
    async def patch_memory(request: Request, memory_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.patch(
            f"{backend_url}/v1/memory/{memory_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Soft-delete ---------------------------------------------------

    @app.delete(Constants.Paths.ITEM, status_code=status.HTTP_204_NO_CONTENT)
    async def delete_memory(request: Request, memory_id: str) -> Response:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.delete(
            f"{backend_url}/v1/memory/{memory_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        if response.status_code >= 400:
            _raise_for_upstream(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ----- Runtime touch -------------------------------------------------

    @app.post(Constants.Paths.TOUCH)
    async def touch_memory(request: Request, memory_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body: dict[str, object] = {}
        if request.headers.get("content-length") not in (None, "0"):
            body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/memory/{memory_id}/touch",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _safe_json(request: Request) -> dict[str, object]:
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


__all__ = ["register_memory_routes"]
