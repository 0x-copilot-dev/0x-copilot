"""Public SCIM 2.0 surface (A7).

Mounted at ``/scim/v2/*`` (NOT under ``/v1`` — IdPs expect the bare
SCIM root). Each request:

1. Pulls the bearer token from ``Authorization: Bearer <token>``.
2. Forwards to ``/internal/v1/auth/scim/resource/*`` with the bearer in
   the ``x-scim-bearer-token`` header (the backend re-validates, so the
   facade is a thin proxy).

We do NOT validate the token at the facade — the backend is the single
source of truth for SCIM tokens. This keeps the facade stateless w.r.t.
SCIM and avoids a second lookup per request.
"""

from __future__ import annotations


from fastapi import FastAPI, HTTPException, Request, Response, status

from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


def register_scim_routes(app: FastAPI) -> None:
    @app.api_route(
        "/scim/v2/Users",
        methods=["GET", "POST"],
    )
    async def scim_users(request: Request) -> Response:
        return await _proxy(app, request, suffix="Users")

    @app.api_route(
        "/scim/v2/Users/{user_id}",
        methods=["GET", "PUT", "PATCH", "DELETE"],
    )
    async def scim_users_one(request: Request, user_id: str) -> Response:
        return await _proxy(app, request, suffix=f"Users/{user_id}")

    @app.api_route(
        "/scim/v2/Groups",
        methods=["GET", "POST"],
    )
    async def scim_groups(request: Request) -> Response:
        return await _proxy(app, request, suffix="Groups")

    @app.api_route(
        "/scim/v2/Groups/{group_id}",
        methods=["GET", "PUT", "PATCH", "DELETE"],
    )
    async def scim_groups_one(request: Request, group_id: str) -> Response:
        return await _proxy(app, request, suffix=f"Groups/{group_id}")

    @app.get("/scim/v2/ServiceProviderConfig")
    async def scim_service_provider_config(request: Request) -> Response:
        return await _proxy(app, request, suffix="ServiceProviderConfig")

    @app.get("/scim/v2/Schemas")
    async def scim_schemas(request: Request) -> Response:
        return await _proxy(app, request, suffix="Schemas")

    @app.get("/scim/v2/ResourceTypes")
    async def scim_resource_types(request: Request) -> Response:
        return await _proxy(app, request, suffix="ResourceTypes")


async def _proxy(app: FastAPI, request: Request, *, suffix: str) -> Response:
    bearer = _bearer_from_request(request)
    if not bearer:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing SCIM bearer token",
        )

    backend_url = settings_for(app).backend_url
    upstream = f"{backend_url}/internal/v1/auth/scim/resource/{suffix}"
    headers = {
        "x-enterprise-service-token": _service_token(),
        "x-enterprise-org-id": "-",
        "x-enterprise-user-id": "-",
        "x-enterprise-roles": "service",
        "x-enterprise-permission-scopes": "",
        "x-enterprise-connector-scopes": "{}",
        "x-scim-bearer-token": bearer,
        "accept": request.headers.get("accept", "application/scim+json"),
    }
    if request.method in {"POST", "PUT", "PATCH"}:
        headers["content-type"] = request.headers.get(
            "content-type", "application/scim+json"
        )

    client = http_client(request.app)
    response = await client.request(
        method=request.method,
        url=upstream,
        params=dict(request.query_params),
        content=await request.body() if request.method != "GET" else None,
        headers=headers,
        timeout=30,
    )
    media_type = response.headers.get("content-type", "application/scim+json")
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=media_type,
    )


def _bearer_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[len("Bearer ") :].strip()
    return None


def _service_token() -> str:
    import os

    token = os.environ.get("ENTERPRISE_SERVICE_TOKEN", "")
    if not token:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "SCIM proxy requires ENTERPRISE_SERVICE_TOKEN",
        )
    return token


def settings_for(app: FastAPI) -> FacadeSettings:
    settings = getattr(app.state, "settings", None)
    if isinstance(settings, FacadeSettings):
        return settings
    return FacadeSettings()


__all__ = ["register_scim_routes"]
