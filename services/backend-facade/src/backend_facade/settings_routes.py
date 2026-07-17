"""Public ``/v1/settings/*`` facade — thin proxy onto ``services/backend``.

Phase 12 P12-A6+A7. The Settings module exposes six endpoints across
three JSONB namespaces (sub-PRD §4.4); the facade is a thin
authenticator + forwarder, mirroring ``tool_routes.py``.

Routes:
  * GET    /v1/settings/notifications              (user)
  * PATCH  /v1/settings/notifications              (user)
  * GET    /v1/settings/workspace/notifications    (admin)
  * PATCH  /v1/settings/workspace/notifications    (admin)
  * GET    /v1/settings/security/webhooks          (admin)
  * PATCH  /v1/settings/security/webhooks          (admin)
  * GET    /v1/settings/provider-keys              (user, BYOK)
  * PUT    /v1/settings/provider-keys/{provider}   (user, BYOK)
  * DELETE /v1/settings/provider-keys/{provider}   (user, BYOK)

ACL is enforced server-side by ``backend_app.settings.service``. The
facade never opens an admin path; it simply forwards the verified
identity and lets the backend project ``CallerIdentity.is_admin`` from
the trusted facade-headers envelope.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


class Constants:
    """Class-namespaced constants for the settings facade routes."""

    class Paths:
        USER_NOTIFICATIONS = "/v1/settings/notifications"
        WORKSPACE_NOTIFICATIONS = "/v1/settings/workspace/notifications"
        SECURITY_WEBHOOKS = "/v1/settings/security/webhooks"
        PROVIDER_KEYS = "/v1/settings/provider-keys"
        PROVIDER_KEY_ITEM = "/v1/settings/provider-keys/{provider}"


def register_settings_routes(app: FastAPI) -> None:
    """Attach ``/v1/settings/*`` proxy routes to a facade FastAPI app."""

    # ----- User notifications -----------------------------------------

    @app.get(Constants.Paths.USER_NOTIFICATIONS)
    async def get_user_notifications(request: Request) -> dict[str, object]:
        return await _forward_get(app, request, Constants.Paths.USER_NOTIFICATIONS)

    @app.patch(Constants.Paths.USER_NOTIFICATIONS)
    async def patch_user_notifications(request: Request) -> dict[str, object]:
        return await _forward_patch(app, request, Constants.Paths.USER_NOTIFICATIONS)

    # ----- Workspace notifications (admin) -----------------------------

    @app.get(Constants.Paths.WORKSPACE_NOTIFICATIONS)
    async def get_workspace_notifications(request: Request) -> dict[str, object]:
        return await _forward_get(app, request, Constants.Paths.WORKSPACE_NOTIFICATIONS)

    @app.patch(Constants.Paths.WORKSPACE_NOTIFICATIONS)
    async def patch_workspace_notifications(request: Request) -> dict[str, object]:
        return await _forward_patch(
            app, request, Constants.Paths.WORKSPACE_NOTIFICATIONS
        )

    # ----- Webhook security defaults (admin) ---------------------------

    @app.get(Constants.Paths.SECURITY_WEBHOOKS)
    async def get_security_webhooks(request: Request) -> dict[str, object]:
        return await _forward_get(app, request, Constants.Paths.SECURITY_WEBHOOKS)

    @app.patch(Constants.Paths.SECURITY_WEBHOOKS)
    async def patch_security_webhooks(request: Request) -> dict[str, object]:
        return await _forward_patch(app, request, Constants.Paths.SECURITY_WEBHOOKS)

    # ----- Provider keys (BYOK) ----------------------------------------
    #
    # The facade never inspects or logs the key material: PUT bodies are
    # forwarded verbatim to the backend, and responses only ever carry
    # the backend-computed ``key_hint`` (last 4 chars), never plaintext.

    @app.get(Constants.Paths.PROVIDER_KEYS)
    async def list_provider_keys(request: Request) -> dict[str, object]:
        return await _forward_get(app, request, Constants.Paths.PROVIDER_KEYS)

    @app.put(Constants.Paths.PROVIDER_KEY_ITEM)
    async def put_provider_key(request: Request, provider: str) -> dict[str, object]:
        return await _forward_put(
            app, request, f"{Constants.Paths.PROVIDER_KEYS}/{provider}"
        )

    @app.delete(
        Constants.Paths.PROVIDER_KEY_ITEM, status_code=status.HTTP_204_NO_CONTENT
    )
    async def delete_provider_key(request: Request, provider: str) -> Response:
        return await _forward_delete_no_content(
            app, request, f"{Constants.Paths.PROVIDER_KEYS}/{provider}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _forward_get(app: FastAPI, request: Request, path: str) -> dict[str, object]:
    backend_url = _settings_for(app).backend_url
    client = http_client(app)
    identity = await FacadeAuthenticator.verify_with_touch(
        request, backend_url=backend_url, http_client=client
    )
    response = await client.get(
        f"{backend_url}{path}",
        params={"org_id": identity.org_id, "user_id": identity.user_id},
        headers=FacadeAuthenticator.service_headers(identity),
        timeout=15,
    )
    return _coerce_object_or_raise(response)


async def _forward_patch(
    app: FastAPI, request: Request, path: str
) -> dict[str, object]:
    backend_url = _settings_for(app).backend_url
    client = http_client(app)
    identity = await FacadeAuthenticator.verify_with_touch(
        request, backend_url=backend_url, http_client=client
    )
    body = await _safe_json(request)
    response = await client.patch(
        f"{backend_url}{path}",
        params={"org_id": identity.org_id, "user_id": identity.user_id},
        json=body,
        headers=FacadeAuthenticator.service_headers(identity),
        timeout=15,
    )
    return _coerce_object_or_raise(response)


async def _forward_put(app: FastAPI, request: Request, path: str) -> dict[str, object]:
    backend_url = _settings_for(app).backend_url
    client = http_client(app)
    identity = await FacadeAuthenticator.verify_with_touch(
        request, backend_url=backend_url, http_client=client
    )
    body = await _safe_json(request)
    response = await client.put(
        f"{backend_url}{path}",
        params={"org_id": identity.org_id, "user_id": identity.user_id},
        json=body,
        headers=FacadeAuthenticator.service_headers(identity),
        timeout=15,
    )
    return _coerce_object_or_raise(response)


async def _forward_delete_no_content(
    app: FastAPI, request: Request, path: str
) -> Response:
    backend_url = _settings_for(app).backend_url
    client = http_client(app)
    identity = await FacadeAuthenticator.verify_with_touch(
        request, backend_url=backend_url, http_client=client
    )
    response = await client.delete(
        f"{backend_url}{path}",
        params={"org_id": identity.org_id, "user_id": identity.user_id},
        headers=FacadeAuthenticator.service_headers(identity),
        timeout=15,
    )
    if response.status_code >= 400:
        _raise_for_upstream(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


__all__ = ["register_settings_routes"]
