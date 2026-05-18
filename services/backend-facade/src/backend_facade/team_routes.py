"""Public ``/v1/team`` facade — thin proxy onto ``services/backend``.

Phase 12 P12-A7. Mirrors the ``tool_routes.py`` / ``library_routes.py``
shape: every method authenticates via :class:`FacadeAuthenticator`,
forwards the verified identity in query params + service-token headers,
and proxies the upstream byte-for-byte (including the SSE stream).

Routes (sub-PRD §4.1):
  * GET    /v1/team                        — list / search (filters + sort)
  * GET    /v1/team/{id}                   — person detail
  * POST   /v1/team/invite                 — invite (admin)
  * POST   /v1/team/{id}/offboard          — admin offboarding wizard
  * PATCH  /v1/team/{id}/role              — admin role change
  * GET    /v1/team/stream                 — SSE (Last-Event-ID resume)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


class Constants:
    """Class-namespaced constants for the team facade routes."""

    class Paths:
        LIST = "/v1/team"
        ITEM = "/v1/team/{person_id}"
        INVITE = "/v1/team/invite"
        OFFBOARD = "/v1/team/{person_id}/offboard"
        ROLE = "/v1/team/{person_id}/role"
        STREAM = "/v1/team/stream"

    class Sse:
        MEDIA_TYPE = "text/event-stream"

    class Headers:
        LAST_EVENT_ID = "Last-Event-ID"


def register_team_routes(app: FastAPI) -> None:
    """Attach ``/v1/team/*`` proxy routes to a facade FastAPI app."""

    # ----- List + filters -------------------------------------------------

    @app.get(Constants.Paths.LIST)
    async def list_team(request: Request) -> dict[str, object]:
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

    # ----- SSE stream — declared BEFORE ``/{person_id}`` so the literal
    # path takes precedence over the path-parameter route in FastAPI's
    # router. (FastAPI matches in registration order.)

    @app.get(Constants.Paths.STREAM)
    async def stream_team(
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

    # ----- Invite (declared before ``/{person_id}/...`` for the same reason).

    @app.post(Constants.Paths.INVITE, status_code=status.HTTP_201_CREATED)
    async def invite_member(request: Request) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}{Constants.Paths.INVITE}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Detail ---------------------------------------------------------

    @app.get(Constants.Paths.ITEM)
    async def get_team_member(request: Request, person_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.get(
            f"{backend_url}/v1/team/{person_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Offboard (admin) ----------------------------------------------

    @app.post(Constants.Paths.OFFBOARD)
    async def offboard_member(request: Request, person_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/team/{person_id}/offboard",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=30,
        )
        return _coerce_object_or_raise(response)

    # ----- Role change (admin) -------------------------------------------

    @app.patch(Constants.Paths.ROLE)
    async def patch_role(request: Request, person_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.patch(
            f"{backend_url}/v1/team/{person_id}/role",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)


# ---------------------------------------------------------------------------
# Helpers (same shape as tool_routes / library_routes)
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


__all__ = ["register_team_routes"]
