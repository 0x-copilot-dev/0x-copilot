"""Public ``/v1/connectors`` facade — thin proxy onto ``services/backend``.

Phase 11 facade proxy (was never dispatched as P11-A4 — discovered during
the 2026-05-19 smoke-test of the Phase 12 ship). Single source of truth is
the backend; this module is a thin forwarder for the entire Connectors
destination wire surface defined in
``docs/atlas-new-design/destinations/connectors-prd.md`` §4 (minus the
webhook lifecycle endpoints, which live in :mod:`webhook_routes`).

Mirrors the ``tool_routes`` proxy pattern:

1. Authenticates the caller via :class:`FacadeAuthenticator`.
2. Forwards the request to ``backend`` with the verified identity in
   query params (dev fallback) and service-token headers (production).
3. Preserves multi-value filter query semantics by forwarding
   ``request.query_params.multi_items()``.
4. Streams ``/v1/connectors/stream`` byte-for-byte so SSE framing
   lands on the wire unchanged.

Wire shape matches ``packages/api-types/src/connectors.ts``.
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
    """Class-namespaced constants for the connectors facade routes."""

    class Paths:
        LIST = "/v1/connectors"
        ITEM = "/v1/connectors/{connector_id}"
        START_OAUTH = "/v1/connectors/{slug}/start-oauth"
        OAUTH_CALLBACK = "/v1/connectors/oauth-callback"
        REFRESH = "/v1/connectors/{connector_id}/refresh"
        DISCONNECT = "/v1/connectors/{connector_id}/disconnect"
        SCOPES = "/v1/connectors/{connector_id}/scopes"
        AUDIT = "/v1/connectors/{connector_id}/audit"
        STREAM = "/v1/connectors/stream"
        # AC9 — desktop-only OAuth transport variant. Distinct paths from the
        # web START_OAUTH / OAUTH_CALLBACK above so the shipped web redirect
        # flow's wire shapes stay byte-identical.
        DESKTOP_CATALOG = "/v1/connectors/desktop/catalog"
        DESKTOP_START_OAUTH = "/v1/connectors/{slug}/desktop/start-oauth"
        DESKTOP_OAUTH_CALLBACK = "/v1/connectors/desktop/oauth-callback"

    class Sse:
        MEDIA_TYPE = "text/event-stream"

    class Headers:
        LAST_EVENT_ID = "Last-Event-ID"


def register_connector_routes(app: FastAPI) -> None:
    """Attach ``/v1/connectors/*`` proxy routes to a facade FastAPI app."""

    # ----- List -----------------------------------------------------------

    @app.get(Constants.Paths.LIST)
    async def list_connectors(request: Request) -> dict[str, object]:
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

    # ----- SSE — declared BEFORE ``/{connector_id}`` so the literal path
    # wins in FastAPI's registration-order matcher.

    @app.get(Constants.Paths.STREAM)
    async def stream_connectors(
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

    # ----- Desktop OAuth transport (AC9) ---------------------------------
    # Thin, token-free forwarders for the desktop-only OAuth variant. The
    # facade injects the VERIFIED identity (query params in dev, service-token
    # headers in prod) and never sees or stores a provider token — the backend
    # coordinator keeps them encrypted in TokenVault. Declared BEFORE the
    # ``/{connector_id}`` param route (all three carry a literal ``desktop``
    # segment, so no ambiguity, but registration-order keeps intent clear).

    @app.get(Constants.Paths.DESKTOP_CATALOG)
    async def desktop_catalog(request: Request) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.get(
            f"{backend_url}{Constants.Paths.DESKTOP_CATALOG}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.post(Constants.Paths.DESKTOP_START_OAUTH)
    async def desktop_start_oauth(request: Request, slug: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/connectors/{slug}/desktop/start-oauth",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.post(Constants.Paths.DESKTOP_OAUTH_CALLBACK)
    async def desktop_oauth_callback(request: Request) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}{Constants.Paths.DESKTOP_OAUTH_CALLBACK}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Detail ---------------------------------------------------------

    @app.get(Constants.Paths.ITEM)
    async def get_connector(request: Request, connector_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.get(
            f"{backend_url}/v1/connectors/{connector_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Start OAuth ----------------------------------------------------

    @app.post(Constants.Paths.START_OAUTH)
    async def start_oauth(request: Request, slug: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/connectors/{slug}/start-oauth",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- OAuth callback -------------------------------------------------

    @app.post(Constants.Paths.OAUTH_CALLBACK)
    async def oauth_callback(request: Request) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}{Constants.Paths.OAUTH_CALLBACK}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Refresh / Disconnect ------------------------------------------

    @app.post(Constants.Paths.REFRESH)
    async def refresh_connector(
        request: Request, connector_id: str
    ) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body: dict[str, object] = {}
        if request.headers.get("content-length") not in (None, "0"):
            body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/connectors/{connector_id}/refresh",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.post(Constants.Paths.DISCONNECT)
    async def disconnect_connector(
        request: Request, connector_id: str
    ) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body: dict[str, object] = {}
        if request.headers.get("content-length") not in (None, "0"):
            body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/connectors/{connector_id}/disconnect",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Scope patch ---------------------------------------------------

    @app.patch(Constants.Paths.SCOPES)
    async def patch_scopes(request: Request, connector_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.patch(
            f"{backend_url}/v1/connectors/{connector_id}/scopes",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Audit log -----------------------------------------------------

    @app.get(Constants.Paths.AUDIT)
    async def get_audit(request: Request, connector_id: str) -> dict[str, object]:
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
            f"{backend_url}/v1/connectors/{connector_id}/audit",
            params=forwarded_params,
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
    try:
        payload = response.json()
    except ValueError:
        return response.text or "Upstream request failed"
    if isinstance(payload, dict) and "detail" in payload:
        return payload["detail"]
    return payload if payload else "Upstream request failed"


def _settings_for(app: FastAPI) -> FacadeSettings:
    return app.state.settings


__all__ = ["register_connector_routes"]
