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


async def _forward_me(request: Request, method: str, slug: str) -> dict[str, object]:
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
