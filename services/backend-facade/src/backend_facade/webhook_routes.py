"""Public ``/v1/connectors/webhooks`` facade — thin proxy onto ``services/backend``.

Phase 11 P11-A3 webhook lifecycle endpoints, exposed on the facade.
Mirrors :mod:`connector_routes` / :mod:`tool_routes` proxy pattern; single
source of truth is the backend ``webhooks`` module.

Lives in a separate file from ``connector_routes`` because the webhook
surface is operationally distinct (rotation worker reasons about secret
strategy + grace window; tenant admins / routine owners manage it
independently of the connector lifecycle) and the wire shape is its own
sub-section of ``connectors-prd.md`` §4.10.

Wire shape matches ``packages/api-types/src/connectors.ts`` (Webhook,
WebhookCreateResponse, WebhookRotateResponse, WebhookListResponse,
WebhookTestFireResponse — all re-exported from the package index after
audit gate §9.12).
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


class Constants:
    """Class-namespaced constants for the webhooks facade routes."""

    class Paths:
        LIST = "/v1/connectors/webhooks"
        ITEM = "/v1/connectors/webhooks/{webhook_id}"
        ROTATE = "/v1/connectors/webhooks/{webhook_id}/rotate"
        TEST_FIRE = "/v1/connectors/webhooks/{webhook_id}/test-fire"


def register_webhook_routes(app: FastAPI) -> None:
    """Attach ``/v1/connectors/webhooks/*`` proxy routes to a facade app."""

    # ----- List -----------------------------------------------------------

    @app.get(Constants.Paths.LIST)
    async def list_webhooks(request: Request) -> dict[str, object]:
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

    # ----- Detail ---------------------------------------------------------

    @app.get(Constants.Paths.ITEM)
    async def get_webhook(request: Request, webhook_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.get(
            f"{backend_url}/v1/connectors/webhooks/{webhook_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Create ---------------------------------------------------------

    @app.post(Constants.Paths.LIST, status_code=status.HTTP_201_CREATED)
    async def create_webhook(request: Request) -> dict[str, object]:
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

    # ----- Patch ----------------------------------------------------------

    @app.patch(Constants.Paths.ITEM)
    async def patch_webhook(request: Request, webhook_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.patch(
            f"{backend_url}/v1/connectors/webhooks/{webhook_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Rotate (copy-once reveal happens at the response envelope) -----

    @app.post(Constants.Paths.ROTATE)
    async def rotate_webhook(request: Request, webhook_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body: dict[str, object] = {}
        if request.headers.get("content-length") not in (None, "0"):
            body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/connectors/webhooks/{webhook_id}/rotate",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # ----- Delete ---------------------------------------------------------

    @app.delete(Constants.Paths.ITEM, status_code=status.HTTP_204_NO_CONTENT)
    async def delete_webhook(request: Request, webhook_id: str) -> Response:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.delete(
            f"{backend_url}/v1/connectors/webhooks/{webhook_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        if response.status_code >= 400:
            _raise_for_upstream(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ----- Test-fire ------------------------------------------------------

    @app.post(Constants.Paths.TEST_FIRE)
    async def test_fire_webhook(request: Request, webhook_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body: dict[str, object] = {}
        if request.headers.get("content-length") not in (None, "0"):
            body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/connectors/webhooks/{webhook_id}/test-fire",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=30,
        )
        return _coerce_object_or_raise(response)


# ---------------------------------------------------------------------------
# Helpers (mirror tool_routes / connector_routes shapes)
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


__all__ = ["register_webhook_routes"]
