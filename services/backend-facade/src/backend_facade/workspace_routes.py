"""``/v1/workspace/*`` + ``/v1/auth/invitations/{token}/accept`` (PR 4.2).

Thin proxy from the public surface to the backend's internal plane. No
business logic. The accept endpoint is the only un-authenticated route in
this module — the backend treats it as ``public_route()``.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

from backend_facade.auth import FacadeAuthenticator
from backend_facade.settings import FacadeSettings


def register_workspace_routes(app: FastAPI) -> None:
    # ----- Workspace branding -----------------------------------------
    @app.get("/v1/workspace")
    async def get_workspace(request: Request) -> dict[str, object]:
        return await _forward(request, "GET", "/internal/v1/workspace")

    @app.patch("/v1/workspace")
    async def patch_workspace(request: Request) -> dict[str, object]:
        return await _forward(request, "PATCH", "/internal/v1/workspace")

    @app.delete("/v1/workspace")
    async def delete_workspace(request: Request) -> dict[str, object]:
        # Backend returns 501; surface upstream status faithfully.
        return await _forward(
            request,
            "DELETE",
            "/internal/v1/workspace",
            extra_query={"confirm_slug": request.query_params.get("confirm_slug", "")},
        )

    # ----- Members directory ------------------------------------------
    @app.get("/v1/workspace/members")
    async def list_members(request: Request) -> dict[str, object]:
        return await _forward(
            request,
            "GET",
            "/internal/v1/workspace/members",
            extra_query={
                k: v
                for k, v in request.query_params.items()
                if k in {"include_removed", "role"}
            },
        )

    @app.patch("/v1/workspace/members/{member_user_id}")
    async def patch_member(request: Request, member_user_id: str) -> dict[str, object]:
        return await _forward(
            request,
            "PATCH",
            f"/internal/v1/workspace/members/{member_user_id}",
        )

    @app.delete("/v1/workspace/members/{member_user_id}", status_code=204)
    async def delete_member(request: Request, member_user_id: str) -> None:
        await _forward(
            request,
            "DELETE",
            f"/internal/v1/workspace/members/{member_user_id}",
            return_json=False,
        )

    # ----- Invitations (admin) ----------------------------------------
    @app.post("/v1/workspace/invitations")
    async def create_invitation(request: Request) -> dict[str, object]:
        result = await _forward(request, "POST", "/internal/v1/workspace/invitations")
        # Decorate with an ``accept_url`` so the FE doesn't have to know the
        # facade host. The token is in the body; we only echo the URL shape.
        token = result.get("token") if isinstance(result, dict) else None
        if isinstance(token, str):
            origin = (
                request.headers.get("origin")
                or f"{request.url.scheme}://{request.url.netloc}"
            )
            result["accept_url"] = f"{origin}/invite/{token}"
        return result

    @app.get("/v1/workspace/invitations")
    async def list_invitations(request: Request) -> dict[str, object]:
        return await _forward(request, "GET", "/internal/v1/workspace/invitations")

    @app.delete("/v1/workspace/invitations/{invite_id}", status_code=204)
    async def revoke_invitation(request: Request, invite_id: str) -> None:
        await _forward(
            request,
            "DELETE",
            f"/internal/v1/workspace/invitations/{invite_id}",
            return_json=False,
        )

    # ----- Accept (no auth) -------------------------------------------
    @app.post("/v1/auth/invitations/{token}/accept")
    async def accept_invitation(request: Request, token: str) -> dict[str, object]:
        backend_url = settings_for(request.app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{backend_url}/internal/v1/auth/invitations/{token}/accept",
                # Forward only the IP / UA hint headers; the backend route
                # is unauthenticated so the service token isn't needed.
                headers={
                    "x-forwarded-for": request.headers.get("x-forwarded-for")
                    or (request.client.host if request.client else ""),
                    "user-agent": request.headers.get("user-agent", ""),
                },
            )
        _raise_for_upstream(response)
        return response.json()

    # ----- Billing (admin read-only) ----------------------------------
    @app.get("/v1/workspace/billing")
    async def get_billing(request: Request) -> dict[str, object]:
        return await _forward(request, "GET", "/internal/v1/workspace/billing")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _forward(
    request: Request,
    method: str,
    path: str,
    *,
    extra_query: dict[str, str] | None = None,
    return_json: bool = True,
) -> dict[str, object]:
    backend_url = settings_for(request.app).backend_url
    body: bytes | None = None
    if method not in ("GET", "DELETE"):
        body = await request.body()
    async with httpx.AsyncClient(timeout=10) as client:
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        headers = FacadeAuthenticator.service_headers(identity)
        if body is not None:
            headers = {**headers, "content-type": "application/json"}
        params: dict[str, str] = {
            "org_id": identity.org_id,
            "user_id": identity.user_id,
        }
        if extra_query:
            params.update({k: v for k, v in extra_query.items() if v != ""})
        response = await client.request(
            method,
            f"{backend_url}{path}",
            params=params,
            content=body,
            headers=headers,
        )
    _raise_for_upstream(response)
    if not return_json:
        return {}
    if not response.content:
        return {}
    return response.json()


def _raise_for_upstream(response: httpx.Response) -> None:
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


__all__ = ["register_workspace_routes"]
