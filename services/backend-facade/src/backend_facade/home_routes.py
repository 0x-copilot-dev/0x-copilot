"""Public ``GET /v1/home`` — Home destination aggregator (Phase 2).

Thin proxy onto ``services/backend`` ``GET /v1/home``. Identity is
established via ``verify_with_touch`` (the same path every other
authenticated route uses); the verified ``org_id`` / ``user_id`` are
forwarded as both query params (dev fallback) and service-token
headers (production path).

Backend owns the aggregation logic (greeting + section composers);
the facade owns nothing here — single source of truth lives one hop
upstream.

Wire into the FastAPI app with ``register_home_routes(app)``.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException, Request

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


def register_home_routes(app: FastAPI) -> None:
    """Attach ``GET /v1/home`` to a facade FastAPI app."""

    @app.get("/v1/home")
    async def get_home(request: Request) -> dict[str, object]:
        backend_url = _settings_for(app).backend_url
        client = http_client(app)
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=backend_url, http_client=client
        )
        response = await client.get(
            f"{backend_url}/v1/home",
            params={
                "org_id": identity.org_id,
                "user_id": identity.user_id,
            },
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        _raise_for_upstream(response)
        return response.json()


def _raise_for_upstream(response: httpx.Response) -> None:
    if response.status_code >= 400:
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


__all__ = ["register_home_routes"]
