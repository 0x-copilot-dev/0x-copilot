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
