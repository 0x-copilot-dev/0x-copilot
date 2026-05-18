"""Public ``/v1/agents`` facade — thin proxy onto ``services/backend``.

Single source of truth is the backend; this module is a five-route
forwarder (P8-A1 CRUD shape) that:

1. Authenticates the caller via :class:`FacadeAuthenticator`.
2. Forwards the request to ``backend`` with the verified identity in
   query params (dev fallback) and service-token headers (production).
3. Preserves multi-value ``filter[<axis>]=`` query semantics
   (cross-audit §1.5) by forwarding ``request.query_params.multi_items()``
   rather than ``dict(...)`` (which collapses repeats).

Wire shape matches ``packages/api-types/src/agents.ts``; see the backend
``backend_app/agents`` module for ACL + audit + state-machine semantics.

P8-A1 ships:

  * GET    /v1/agents               — list + filter + search
  * GET    /v1/agents/{id}          — single agent (merged-overrides view)
  * POST   /v1/agents               — create custom agent
  * PATCH  /v1/agents/{id}          — owner-only mutate
  * DELETE /v1/agents/{id}          — owner-only soft-delete

P8-A2 (versions), P8-A3 (install/uninstall), P8-A4 (usage) hook
additional routes onto the same surface via separate sub-files.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


def register_agents_routes(app: FastAPI) -> None:
    """Attach ``/v1/agents`` proxy routes to a facade FastAPI app."""

    @app.get("/v1/agents")
    async def list_agents(request: Request) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        # Preserve multi-value filter[<axis>]= repeats.
        forwarded_params: list[tuple[str, str]] = [
            ("org_id", identity.org_id),
            ("user_id", identity.user_id),
        ]
        for key, value in request.query_params.multi_items():
            if key in {"org_id", "user_id"}:
                continue
            forwarded_params.append((key, value))
        response = await client.get(
            f"{backend_url}/v1/agents",
            params=forwarded_params,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.get("/v1/agents/{agent_id}")
    async def get_agent(request: Request, agent_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.get(
            f"{backend_url}/v1/agents/{agent_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.post("/v1/agents", status_code=status.HTTP_201_CREATED)
    async def create_agent(request: Request) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/agents",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.patch("/v1/agents/{agent_id}")
    async def update_agent(request: Request, agent_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.patch(
            f"{backend_url}/v1/agents/{agent_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.delete("/v1/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_agent(request: Request, agent_id: str) -> Response:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.delete(
            f"{backend_url}/v1/agents/{agent_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        if response.status_code >= 400:
            _raise_for_upstream(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers (same shape as projects_routes / routines_routes / inbox_routes)
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


__all__ = ["register_agents_routes"]
