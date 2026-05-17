"""Public ``/v1/routines`` facade — thin proxy onto ``services/backend``.

Single source of truth is the backend; this module is a six-route
forwarder that:

1. Authenticates the caller via :class:`FacadeAuthenticator`.
2. Forwards the request to ``backend`` with the verified identity in
   query params (dev fallback) and service-token headers (production).
3. Preserves multi-value ``filter[<axis>]=`` query semantics (cross-audit
   §1.5) by forwarding ``request.query_params.multi_items()`` rather
   than ``dict(...)`` (which collapses repeats).

Wire shape matches ``packages/api-types/src/routines.ts``; see the
backend ``backend_app/routines`` module for ACL + audit + state-machine
+ quota semantics.

The six routes:
  * GET    /v1/routines             — list with cursor pagination + filters
  * GET    /v1/routines/{id}        — single routine
  * POST   /v1/routines             — create (status defaults to draft)
  * PATCH  /v1/routines/{id}        — owner-only mutate + state transitions
  * DELETE /v1/routines/{id}        — owner-only soft-delete
  * POST   /v1/routines/{id}/run    — manual fire (ACL per permissions.manual_fire)

The webhook ingest router (``/v1/webhook/routines/{trigger_id}``) is
P5-A3's surface — a separate router because its auth shape (header
secret + IP allowlist + HMAC, no bearer) is incompatible with the
public `/v1/*` envelope this module proxies.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


def register_routines_routes(app: FastAPI) -> None:
    """Attach ``/v1/routines`` proxy routes to a facade FastAPI app."""

    @app.get("/v1/routines")
    async def list_routines(request: Request) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        # Forward the multi-value filter[*] params verbatim — list/tuple
        # values preserve repeats over the wire (httpx encodes
        # `params=[("filter[status]","active"),("filter[status]","paused")]`
        # as `filter[status]=active&filter[status]=paused`).
        forwarded_params: list[tuple[str, str]] = [
            ("org_id", identity.org_id),
            ("user_id", identity.user_id),
        ]
        for key, value in request.query_params.multi_items():
            if key in {"org_id", "user_id"}:
                continue
            forwarded_params.append((key, value))
        response = await client.get(
            f"{backend_url}/v1/routines",
            params=forwarded_params,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.get("/v1/routines/{routine_id}")
    async def get_routine(request: Request, routine_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.get(
            f"{backend_url}/v1/routines/{routine_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.post("/v1/routines", status_code=status.HTTP_201_CREATED)
    async def create_routine(request: Request) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.post(
            f"{backend_url}/v1/routines",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.patch("/v1/routines/{routine_id}")
    async def update_routine(request: Request, routine_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        body = await _safe_json(request)
        response = await client.patch(
            f"{backend_url}/v1/routines/{routine_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json=body,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)

    @app.delete("/v1/routines/{routine_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_routine(request: Request, routine_id: str) -> Response:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.delete(
            f"{backend_url}/v1/routines/{routine_id}",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        if response.status_code >= 400:
            _raise_for_upstream(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/v1/routines/{routine_id}/run")
    async def run_routine(request: Request, routine_id: str) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        # Manual fire is a no-body POST; the routine id + caller
        # identity is the entire input.
        response = await client.post(
            f"{backend_url}/v1/routines/{routine_id}/run",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
            json={},
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)


# ---------------------------------------------------------------------------
# Helpers
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


__all__ = ["register_routines_routes"]
