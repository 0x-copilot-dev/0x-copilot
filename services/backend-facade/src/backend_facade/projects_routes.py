"""Public ``/v1/projects`` facade — thin proxy onto ``services/backend``.

Single source of truth is the backend; this module is a fourteen-route
forwarder that:

1. Authenticates the caller via :class:`FacadeAuthenticator`.
2. Forwards the request to ``backend`` with the verified identity in
   query params (dev fallback) and service-token headers (production).
3. Preserves multi-value ``filter[<axis>]=`` query semantics
   (cross-audit §1.5) by forwarding
   ``request.query_params.multi_items()`` rather than ``dict(...)``
   (which collapses repeats).

Wire shape matches ``packages/api-types/src/projects.ts``; see the
backend ``backend_app/projects`` module for ACL + audit + transfer
semantics.

The routes:

  * GET    /v1/projects                                  — list + search
  * GET    /v1/projects/{id}                             — single project
  * POST   /v1/projects                                  — create
  * PATCH  /v1/projects/{id}                             — owner-only mutate
  * DELETE /v1/projects/{id}                             — owner-only soft-delete
  * POST   /v1/projects/{id}/restore                     — un-archive (owner)
  * GET    /v1/projects/{id}/members                     — list members
  * POST   /v1/projects/{id}/members                     — add member (owner)
  * PATCH  /v1/projects/{id}/members/{user_id}           — change role (owner)
  * DELETE /v1/projects/{id}/members/{user_id}           — remove (owner or self)
  * POST   /v1/projects/{id}/transfer                    — owner transfer
  * POST   /v1/admin/projects/{id}/force-transfer        — admin force-transfer
  * POST   /v1/projects/{id}/star  + /unstar             — per-user star
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


def register_projects_routes(app: FastAPI) -> None:
    """Attach ``/v1/projects`` proxy routes to a facade FastAPI app."""

    @app.get("/v1/projects")
    async def list_projects(request: Request) -> dict[str, object]:
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
            f"{backend_url}/v1/projects",
            params=forwarded_params,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.get("/v1/projects/{project_id}")
    async def get_project(request: Request, project_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.get(
            f"{backend_url}/v1/projects/{project_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.post("/v1/projects", status_code=status.HTTP_201_CREATED)
    async def create_project(request: Request) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/projects",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.patch("/v1/projects/{project_id}")
    async def update_project(request: Request, project_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.patch(
            f"{backend_url}/v1/projects/{project_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.delete("/v1/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_project(request: Request, project_id: str) -> Response:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.delete(
            f"{backend_url}/v1/projects/{project_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        if response.status_code >= 400:
            _raise_for_upstream(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/v1/projects/{project_id}/restore")
    async def restore_project(request: Request, project_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.post(
            f"{backend_url}/v1/projects/{project_id}/restore",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json={},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # -- members ------------------------------------------------------

    @app.get("/v1/projects/{project_id}/members")
    async def list_members(request: Request, project_id: str) -> dict[str, object]:
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
            f"{backend_url}/v1/projects/{project_id}/members",
            params=forwarded_params,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.post(
        "/v1/projects/{project_id}/members",
        status_code=status.HTTP_201_CREATED,
    )
    async def add_member(request: Request, project_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/projects/{project_id}/members",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.patch("/v1/projects/{project_id}/members/{member_user_id}")
    async def change_member_role(
        request: Request, project_id: str, member_user_id: str
    ) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.patch(
            f"{backend_url}/v1/projects/{project_id}/members/{member_user_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.delete(
        "/v1/projects/{project_id}/members/{member_user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def remove_member(
        request: Request, project_id: str, member_user_id: str
    ) -> Response:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.delete(
            f"{backend_url}/v1/projects/{project_id}/members/{member_user_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        if response.status_code >= 400:
            _raise_for_upstream(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # -- transfer -----------------------------------------------------

    @app.post("/v1/projects/{project_id}/transfer")
    async def transfer_ownership(
        request: Request, project_id: str
    ) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/projects/{project_id}/transfer",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # Phase 6 product decision (user override 2026-05-18): admin force-transfer
    # deferred — backend route is no longer registered, so this facade proxy
    # would 404 anyway. Code preserved as commented decorator for future revive.
    #
    # @app.post("/v1/admin/projects/{project_id}/force-transfer")
    async def force_transfer_ownership(
        request: Request, project_id: str
    ) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/admin/projects/{project_id}/force-transfer",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    # -- stars --------------------------------------------------------

    @app.post(
        "/v1/projects/{project_id}/star",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def star_project(request: Request, project_id: str) -> Response:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.post(
            f"{backend_url}/v1/projects/{project_id}/star",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json={},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        if response.status_code >= 400:
            _raise_for_upstream(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/v1/projects/{project_id}/unstar",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def unstar_project(request: Request, project_id: str) -> Response:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.post(
            f"{backend_url}/v1/projects/{project_id}/unstar",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json={},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        if response.status_code >= 400:
            _raise_for_upstream(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers (same shape as routines_routes / inbox_routes)
# ---------------------------------------------------------------------------


async def _safe_json(request: Request) -> dict[str, object]:
    """Pass through the request body, defaulting empty bodies to ``{}``."""

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
    raise HTTPException(response.status_code, detail)


def _settings_for(app: FastAPI) -> FacadeSettings:
    return app.state.settings


__all__ = ["register_projects_routes"]
