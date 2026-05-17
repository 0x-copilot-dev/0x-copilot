"""``/v1/admin/adapter_registry/*`` — admin tier-2 adapter review surface (7C).

Phase 7 splits the tier-2 sharing problem across three agents:

- 7A owns the registry itself (storage + audit + scope gate) on the
  backend. Its public surface lives at ``/internal/v1/adapter_registry/*``.
- 7B harvests successful local adapters from the client into 7A's queue.
- 7C (this module + ``apps/frontend/src/admin/adapter-review/``) is the
  admin review UI that lets a reviewer approve / reject / request-changes
  against a candidate. The reviewer never sees tenant-private data —
  the candidate's source is tenant-anonymized at submit time (7B) and
  the preview pane runs against synthetic state, not real customer data.

This module is a thin proxy. It forwards three routes onto 7A:

  GET  /v1/admin/adapter_registry/candidates
  GET  /v1/admin/adapter_registry/candidates/{id}
  POST /v1/admin/adapter_registry/candidates/{id}/decisions

The ``admin:adapter_registry_review`` scope check lives on the backend
side. We deliberately do not duplicate it here — the same reasoning as
``audit_routes.py`` applies: a defence-in-depth gate would race with
role updates and surface as spurious 403s.

Allowlisted query params are forwarded verbatim. ``org_id``/``user_id``
ride the service-token headers (verified bearer → canonical identity),
never the body or the URL — keeping the API surface identity-stable
even if the FE somehow passes a stale or wrong identity.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

from backend_facade.auth import FacadeAuthenticator
from backend_facade.settings import FacadeSettings


# Forwarded read filters. Allowlist so a client cannot smuggle e.g.
# ``?org_id=other`` past the facade — identity is stamped by
# ``service_headers`` from the verified bearer, not from the query.
_FORWARDED_LIST_PARAMS: tuple[str, ...] = (
    "status",
    "layout",
    "scheme",
    "cursor",
    "limit",
)


def register_adapter_review_routes(app: FastAPI) -> None:
    @app.get("/v1/admin/adapter_registry/candidates")
    async def list_candidates(request: Request) -> dict[str, object]:
        params = {
            k: v
            for k, v in request.query_params.items()
            if k in _FORWARDED_LIST_PARAMS and v != ""
        }
        return await _forward_json(
            request,
            "GET",
            "/internal/v1/adapter_registry/candidates",
            params=params,
        )

    @app.get("/v1/admin/adapter_registry/candidates/{candidate_id}")
    async def get_candidate(request: Request, candidate_id: str) -> dict[str, object]:
        return await _forward_json(
            request,
            "GET",
            f"/internal/v1/adapter_registry/candidates/{candidate_id}",
        )

    @app.post("/v1/admin/adapter_registry/candidates/{candidate_id}/decisions")
    async def decide_candidate(
        request: Request,
        candidate_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        # ``action`` is the only required field. We don't validate
        # enumeration here — the backend owns the canonical list and is
        # free to add new actions (e.g. "needs-security-review") without
        # the facade gating it. ``notes`` is forwarded verbatim.
        return await _forward_json(
            request,
            "POST",
            f"/internal/v1/adapter_registry/candidates/{candidate_id}/decisions",
            json=payload,
        )


async def _forward_json(
    request: Request,
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
    json: dict[str, object] | None = None,
) -> dict[str, object]:
    backend_url = _settings_for(request.app).backend_url
    async with httpx.AsyncClient(timeout=15) as client:
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        merged_params: dict[str, str] = {
            "org_id": identity.org_id,
            "user_id": identity.user_id,
        }
        if params:
            merged_params.update(params)
        headers = FacadeAuthenticator.service_headers(identity)
        response = await client.request(
            method,
            f"{backend_url}{path}",
            params=merged_params,
            json=json,
            headers=headers,
        )
    _raise_for_upstream(response)
    if response.status_code == 204 or not response.content:
        return {}
    payload = response.json()
    if not isinstance(payload, dict):
        # 7A returns objects on every route — a non-object response is a
        # contract violation. Surface as 502 so the FE shows an error
        # banner rather than rendering ``undefined``.
        raise HTTPException(502, "Upstream response was not an object")
    return payload


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


def _settings_for(app: FastAPI) -> FacadeSettings:
    return app.state.settings


__all__ = ["register_adapter_review_routes"]
