"""Public ``/v1/palette/search`` facade — thin proxy onto ``services/backend``.

Phase 12 P12-A7. The palette destination (sub-PRD §4.3) is one
fan-out search endpoint that aggregates results across people /
memory / library / agents / tools / connectors / routines / projects
/ home / inbox. The facade is a thin pass-through; the backend owns
the fan-out + ranking.

Routes:
  * GET /v1/palette/search?q=…&context=…
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException, Request, status

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


class Constants:
    """Class-namespaced constants for the palette facade routes."""

    class Paths:
        SEARCH = "/v1/palette/search"


def register_palette_routes(app: FastAPI) -> None:
    """Attach ``/v1/palette/*`` proxy routes to a facade FastAPI app."""

    @app.get(Constants.Paths.SEARCH)
    async def search_palette(request: Request) -> dict[str, object]:
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
            f"{backend_url}{Constants.Paths.SEARCH}",
            params=forwarded_params,
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        return _coerce_object_or_raise(response)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    raise HTTPException(response.status_code, _upstream_error_detail(response))


def _upstream_error_detail(response: httpx.Response) -> object:
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
    return detail


def _settings_for(app: FastAPI) -> FacadeSettings:
    return app.state.settings


__all__ = ["register_palette_routes"]
