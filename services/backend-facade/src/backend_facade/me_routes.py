"""Public ``/v1/me/*`` routes — caller's own profile + memberships.

Browser-facing surface for the frontend's UserCard popover (PR 2.2 sidebar).
Today exposes a single endpoint, ``GET /v1/me/workspaces``, that proxies
to the backend's internal ``/internal/v1/me/workspaces`` read-through.

The facade owns no business logic here — identity is established the
same way every other authenticated route handles it (``verify_with_touch``
via the bearer header), then forwarded as a service-token request to
the backend with ``x-enterprise-org-id`` / ``x-enterprise-user-id``
populated from the verified session.

Wire into the FastAPI app with ``register_me_routes(app)``.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

from backend_facade.auth import FacadeAuthenticator
from backend_facade.settings import FacadeSettings


def register_me_routes(app: FastAPI) -> None:
    @app.get("/v1/me/workspaces")
    async def list_my_workspaces(request: Request) -> dict[str, object]:
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            identity = await FacadeAuthenticator.verify_with_touch(
                request, backend_url=backend_url, http_client=client
            )
            response = await client.get(
                f"{backend_url}/internal/v1/me/workspaces",
                params={
                    "org_id": identity.org_id,
                    "user_id": identity.user_id,
                },
                headers=FacadeAuthenticator.service_headers(identity),
            )
        _raise_for_upstream(response)
        return response.json()

    # PR 4.1 — Settings → "You" group: profile + preferences sidecars.
    # Identity is established the same way as /v1/me/workspaces — bearer
    # verify, then service-token forward. The sidecar routes are caller-
    # scoped: the backend resolves the write target from the headers, so
    # the body never carries a user_id.

    @app.get("/v1/me/profile")
    async def get_my_profile(request: Request) -> dict[str, object]:
        return await _forward_me(request, "GET", "profile")

    @app.put("/v1/me/profile")
    async def put_my_profile(request: Request) -> dict[str, object]:
        return await _forward_me(request, "PUT", "profile")

    @app.get("/v1/me/preferences")
    async def get_my_preferences(request: Request) -> dict[str, object]:
        return await _forward_me(request, "GET", "preferences")

    @app.put("/v1/me/preferences")
    async def put_my_preferences(request: Request) -> dict[str, object]:
        return await _forward_me(request, "PUT", "preferences")

    # PR B4 / 8.0.3e — typed notification preferences + quiet hours.
    @app.get("/v1/me/notifications")
    async def get_my_notifications(request: Request) -> dict[str, object]:
        return await _forward_me(request, "GET", "notifications")

    @app.put("/v1/me/notifications")
    async def put_my_notifications(request: Request) -> dict[str, object]:
        return await _forward_me(request, "PUT", "notifications")

    # PR B3 / 8.0.3g — personal API keys (atlas_pk_*).
    @app.get("/v1/me/api-keys")
    async def list_my_api_keys(request: Request) -> dict[str, object]:
        return await _forward_me(request, "GET", "api-keys")

    @app.post("/v1/me/api-keys")
    async def create_my_api_key(request: Request) -> dict[str, object]:
        return await _forward_me(request, "POST", "api-keys")

    @app.delete("/v1/me/api-keys/{api_key_id}", status_code=204)
    async def revoke_my_api_key(request: Request, api_key_id: str) -> None:
        await _forward_me(
            request,
            "DELETE",
            f"api-keys/{api_key_id}",
            expect_json=False,
        )

    @app.post("/v1/me/api-keys/{api_key_id}/rotate", status_code=201)
    async def rotate_my_api_key(request: Request, api_key_id: str) -> dict[str, object]:
        return await _forward_me(
            request,
            "POST",
            f"api-keys/{api_key_id}/rotate",
        )

    # PR 8.2 — Settings → Profile → Sign-in & security: TOTP enrollment.
    # The backend's ``/internal/v1/me/mfa/*`` wrapper takes identity in
    # query params (matching every other ``me/*`` route) so ``_forward_me``
    # works as-is — no body rewriting.
    @app.get("/v1/me/mfa/factors")
    async def list_my_mfa_factors(request: Request) -> dict[str, object]:
        return await _forward_me(request, "GET", "mfa/factors")

    @app.post("/v1/me/mfa/factors/totp/enroll")
    async def enroll_totp_factor(request: Request) -> dict[str, object]:
        return await _forward_me(request, "POST", "mfa/factors/totp/enroll")

    @app.post("/v1/me/mfa/factors/totp/confirm")
    async def confirm_totp_factor(request: Request) -> dict[str, object]:
        return await _forward_me(request, "POST", "mfa/factors/totp/confirm")

    @app.delete("/v1/me/mfa/factors/{factor_id}", status_code=204)
    async def disable_mfa_factor(request: Request, factor_id: str) -> None:
        await _forward_me(
            request,
            "DELETE",
            f"mfa/factors/{factor_id}",
            expect_json=False,
        )

    # PR 8.3 — WebAuthn enrollment ceremony. The browser handles
    # `navigator.credentials.create`; the facade just forwards the
    # base64-url'd start/finish bodies to the backend wrapper. Identity
    # comes from the verified session; rp_id / origin are caller-
    # supplied (the backend verifies them against the attestation).
    @app.post("/v1/me/mfa/factors/webauthn/register/start")
    async def webauthn_register_start(request: Request) -> dict[str, object]:
        return await _forward_me(request, "POST", "mfa/factors/webauthn/register/start")

    @app.post("/v1/me/mfa/factors/webauthn/register/finish")
    async def webauthn_register_finish(request: Request) -> dict[str, object]:
        return await _forward_me(
            request, "POST", "mfa/factors/webauthn/register/finish"
        )

    # PR B1 / 8.0.3d — tool-use policy (per-user override; admin
    # workspace-default writes go through a separate workspace route
    # below).
    @app.get("/v1/me/policies/tool-use")
    async def get_my_tool_use_policy(request: Request) -> dict[str, object]:
        return await _forward_policy(request, "GET", "tool-use", scope="user")

    @app.put("/v1/me/policies/tool-use")
    async def put_my_tool_use_policy(request: Request) -> dict[str, object]:
        return await _forward_policy(request, "PUT", "tool-use", scope="user")

    # PR B2 / 8.0.3f — privacy & data settings (per-user override).
    @app.get("/v1/me/policies/privacy")
    async def get_my_privacy_settings(request: Request) -> dict[str, object]:
        return await _forward_policy(request, "GET", "privacy", scope="user")

    @app.put("/v1/me/policies/privacy")
    async def put_my_privacy_settings(request: Request) -> dict[str, object]:
        return await _forward_policy(request, "PUT", "privacy", scope="user")

    # Workspace-default mutations require ADMIN_USERS — the backend
    # enforces the scope check; the facade just routes the request.
    @app.get("/v1/workspace/policies/tool-use")
    async def get_workspace_tool_use_policy(
        request: Request,
    ) -> dict[str, object]:
        return await _forward_policy(request, "GET", "tool-use", scope="workspace")

    @app.put("/v1/workspace/policies/tool-use")
    async def put_workspace_tool_use_policy(
        request: Request,
    ) -> dict[str, object]:
        return await _forward_policy(request, "PUT", "tool-use", scope="workspace")

    @app.get("/v1/workspace/policies/privacy")
    async def get_workspace_privacy_settings(
        request: Request,
    ) -> dict[str, object]:
        return await _forward_policy(request, "GET", "privacy", scope="workspace")

    @app.put("/v1/workspace/policies/privacy")
    async def put_workspace_privacy_settings(
        request: Request,
    ) -> dict[str, object]:
        return await _forward_policy(request, "PUT", "privacy", scope="workspace")


async def _forward_me(
    request: Request,
    method: str,
    slug: str,
    *,
    expect_json: bool = True,
) -> dict[str, object]:
    backend_url = settings_for(request.app).backend_url
    body: bytes | None = None
    if method != "GET":
        body = await request.body()
    async with httpx.AsyncClient(timeout=10) as client:
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        headers = FacadeAuthenticator.service_headers(identity)
        if method != "GET":
            # Preserve the JSON content-type so FastAPI on the upstream
            # parses Pydantic without needing a manual Content-Type header.
            headers = {**headers, "content-type": "application/json"}
        # Pass org_id / user_id as query params too. With the service token
        # set, the backend route trusts the headers and ignores the params;
        # in dev (no service token) the params are the dev fallback. This
        # matches the pattern /v1/me/workspaces uses.
        params = {"org_id": identity.org_id, "user_id": identity.user_id}
        response = await client.request(
            method,
            f"{backend_url}/internal/v1/me/{slug}",
            params=params,
            content=body,
            headers=headers,
        )
    _raise_for_upstream(response)
    if not expect_json:
        return {}
    if not response.content:
        return {}
    return response.json()


async def _forward_policy(
    request: Request,
    method: str,
    policy_kind: str,
    *,
    scope: str,
) -> dict[str, object]:
    """Forward to ``/internal/v1/policies/{policy_kind}`` with the
    appropriate ``scope_user_id`` query param.

    ``scope='user'`` adds ``scope_user_id=<caller>`` so the backend
    targets the user override row. ``scope='workspace'`` omits the
    param so the backend targets the workspace default — the backend
    requires ADMIN_USERS for that path.
    """

    backend_url = settings_for(request.app).backend_url
    body: bytes | None = None
    if method != "GET":
        body = await request.body()
    async with httpx.AsyncClient(timeout=10) as client:
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        headers = FacadeAuthenticator.service_headers(identity)
        if method != "GET":
            headers = {**headers, "content-type": "application/json"}
        params: dict[str, str] = {
            "org_id": identity.org_id,
            "user_id": identity.user_id,
        }
        if scope == "user":
            params["scope_user_id"] = identity.user_id
        response = await client.request(
            method,
            f"{backend_url}/internal/v1/policies/{policy_kind}",
            params=params,
            content=body,
            headers=headers,
        )
    _raise_for_upstream(response)
    if not response.content:
        return {}
    return response.json()


def _raise_for_upstream(response: httpx.Response) -> None:
    # Same shape as auth_routes._raise_for_upstream; kept as a private copy
    # to avoid the circular-import cost of importing it from there.
    if response.status_code < 400:
        return
    detail: Any
    try:
        body = response.json()
    except ValueError:
        detail = response.text or "Upstream error"
    else:
        detail = body.get("detail") if isinstance(body, dict) else body
    raise HTTPException(response.status_code, detail or "Upstream error")


def settings_for(app: FastAPI) -> FacadeSettings:
    return app.state.settings


__all__ = ["register_me_routes"]
