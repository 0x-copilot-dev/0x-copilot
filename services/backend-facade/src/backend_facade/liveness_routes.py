"""Public ``/v1/liveness/...`` facade — thin proxy onto ``services/backend``.

The backend exposes the actual aggregator on its INTERNAL surface at
``/internal/v1/liveness/project/{id}``. Per the cross-component contract,
the facade adapts that to a public ``/v1/liveness/project/{id}`` endpoint
that the FE archive-409 modal can call to refresh the LivenessReport
without re-issuing the DELETE (§6.3 "Refresh status" button).

Wire shape: ``LivenessReport`` from ``packages/api-types/src/projects.ts``.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException, Request, status

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


def register_liveness_routes(app: FastAPI) -> None:
    """Attach the liveness proxy to a facade FastAPI app."""

    @app.get("/v1/liveness/project/{project_id}")
    async def get_project_liveness(
        request: Request, project_id: str
    ) -> dict[str, object]:
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
            f"{backend_url}/internal/v1/liveness/project/{project_id}",
            params=forwarded_params,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)


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


__all__ = ["register_liveness_routes"]
