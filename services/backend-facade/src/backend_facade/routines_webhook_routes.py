"""``/v1/webhook/routines/*`` facade proxy — P5-A3.

Separate from ``routines_routes.py`` (P5-A1) on purpose: routines CRUD
is bearer-authenticated, but the webhook POST is authenticated by the
**secret + HMAC headers** (cross-audit §2.4). Putting the two on the
same router risks an accidental ``Depends(FacadeAuthenticator)``
slipping onto a public route at review time.

This module:

* Proxies ``POST /v1/webhook/routines/{trigger_id}`` to ``backend``
  **without** running the facade auth. Backend's validator IS the
  gate; the facade just forwards the body + auth headers.
* Forwards source-IP context via ``X-Forwarded-For`` so backend's
  audit row records the *true* caller, not the facade-local
  ``client.host``.

Owner-scoped rotate + reveal endpoints stay on the bearer-auth path —
they ride the existing routines surface and live in
``routines_routes.py`` (P5-A1). This module deliberately does NOT
register them.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request

from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings


# Header allowlist forwarded verbatim to backend. We don't pass through
# arbitrary headers — that risks header smuggling. Content-Type is needed
# so backend can parse the body shape; the two auth headers are the
# whole point of this route; UA + Forwarded-For ride for audit fidelity.
_FORWARDED_HEADERS: tuple[str, ...] = (
    "content-type",
    "x-atlas-routine-secret",
    "x-atlas-routine-signature",
    "user-agent",
)


def register_routines_webhook_routes(app: FastAPI) -> None:
    """Attach the public webhook ingest proxy. NO auth dependency."""

    @app.post("/v1/webhook/routines/{trigger_id}")
    async def proxy_webhook(request: Request, trigger_id: str) -> dict[str, Any]:
        # Pull raw body — backend computes HMAC over the exact bytes the
        # caller signed. Re-encoding via ``json=...`` would silently
        # change whitespace and break the signature. (This is a classic
        # webhook proxy footgun; the cross-audit §2.4 decision binds us
        # to byte-level fidelity here.)
        body = await request.body()
        headers = _outbound_headers(request)
        client = http_client(request.app)
        backend_url = _settings_for(request.app).backend_url
        try:
            upstream = await client.request(
                "POST",
                f"{backend_url}/v1/webhook/routines/{trigger_id}",
                content=body,
                headers=headers,
                timeout=15,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(502, "Upstream backend unreachable") from exc
        if upstream.status_code >= 400:
            raise HTTPException(upstream.status_code, _upstream_error_detail(upstream))
        if upstream.status_code == 204 or not upstream.content:
            return {}
        payload = upstream.json()
        if not isinstance(payload, dict):
            raise HTTPException(502, "Upstream response was not an object")
        return payload


def _outbound_headers(request: Request) -> dict[str, str]:
    """Copy the allowlisted incoming headers + stamp X-Forwarded-For."""

    headers: dict[str, str] = {}
    for name in _FORWARDED_HEADERS:
        value = request.headers.get(name)
        if value is not None:
            headers[name] = value
    # X-Forwarded-For chain: append the immediate caller (could be a
    # reverse proxy upstream of us, or the raw external sender). The
    # backend's ``_request_ip`` picks the first entry, which is the
    # original source.
    immediate = request.client.host if request.client else None
    chain = request.headers.get("x-forwarded-for")
    if chain and immediate:
        headers["x-forwarded-for"] = f"{chain}, {immediate}"
    elif chain:
        headers["x-forwarded-for"] = chain
    elif immediate:
        headers["x-forwarded-for"] = immediate
    return headers


def _upstream_error_detail(response: httpx.Response) -> object:
    try:
        payload = response.json()
    except ValueError:
        return response.text or "Upstream request failed"
    if isinstance(payload, dict) and "detail" in payload:
        return payload["detail"]
    return payload if payload else "Upstream request failed"


def _settings_for(app: FastAPI) -> FacadeSettings:
    return app.state.settings


__all__ = ["register_routines_webhook_routes"]
