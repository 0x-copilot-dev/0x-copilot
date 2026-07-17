"""Facade proxy for ``/v1/local-models/*`` (Round 2 — local Ollama models).

Thin passthrough to ai-backend: JSON for status/list/size/delete and a
byte-for-byte SSE proxy for the pull-progress stream. No orchestration here
(that lives in ai-backend); the facade only authenticates and forwards. The
feature is gated in ai-backend, so a disabled deployment 404s these here too.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from backend_facade.auth import FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.settings import FacadeSettings

_BASE = "/v1/local-models"
_SSE_MEDIA_TYPE = "text/event-stream"


def register_local_models_routes(app: FastAPI) -> None:
    """Attach the ``/v1/local-models/*`` proxy routes to a facade app."""

    @app.get(f"{_BASE}/status")
    async def local_models_status(request: Request) -> dict[str, object]:
        return await _forward_get(app, request, f"{_BASE}/status")

    @app.get(f"{_BASE}/size")
    async def local_models_size(
        request: Request,
        repo: str = Query(..., min_length=1),
        quant: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        return await _forward_get(
            app, request, f"{_BASE}/size", extra={"repo": repo, "quant": quant}
        )

    @app.get(f"{_BASE}/pull")
    async def local_models_pull(
        request: Request,
        repo: str = Query(..., min_length=1),
        quant: str = Query(..., min_length=1),
    ) -> StreamingResponse:
        identity = FacadeAuthenticator.authenticate_request(request)
        client = http_client(app)
        upstream = await client.send(
            client.build_request(
                "GET",
                f"{_settings_for(app).ai_backend_url}{_BASE}/pull",
                params=identity.scoped_params({"repo": repo, "quant": quant}),
                headers=FacadeAuthenticator.service_headers(identity),
                timeout=None,
            ),
            stream=True,
        )
        if upstream.status_code >= 400:
            await upstream.aread()
            await upstream.aclose()
            raise HTTPException(upstream.status_code, _upstream_error_detail(upstream))

        async def event_stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_bytes():
                    if await request.is_disconnected():
                        break
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            event_stream(),
            media_type=_SSE_MEDIA_TYPE,
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
        )

    @app.get(_BASE)
    async def local_models_list(request: Request) -> dict[str, object]:
        return await _forward_get(app, request, _BASE)

    @app.delete(f"{_BASE}/{{name:path}}", status_code=status.HTTP_204_NO_CONTENT)
    async def local_models_delete(request: Request, name: str) -> Response:
        identity = FacadeAuthenticator.authenticate_request(request)
        client = http_client(app)
        response = await client.request(
            "DELETE",
            f"{_settings_for(app).ai_backend_url}{_BASE}/{name}",
            params=identity.scoped_params(),
            headers=FacadeAuthenticator.service_headers(identity),
            timeout=15,
        )
        if response.status_code >= 400:
            raise HTTPException(response.status_code, _upstream_error_detail(response))
        return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _forward_get(
    app: FastAPI,
    request: Request,
    path: str,
    *,
    extra: dict[str, str] | None = None,
) -> dict[str, object]:
    identity = FacadeAuthenticator.authenticate_request(request)
    client = http_client(app)
    response = await client.get(
        f"{_settings_for(app).ai_backend_url}{path}",
        params=identity.scoped_params(dict(extra or {})),
        headers=FacadeAuthenticator.service_headers(identity),
        timeout=30,
    )
    return _coerce_object_or_raise(response)


def _coerce_object_or_raise(response: httpx.Response) -> dict[str, object]:
    if response.status_code >= 400:
        raise HTTPException(response.status_code, _upstream_error_detail(response))
    if response.status_code == status.HTTP_204_NO_CONTENT or not response.content:
        return {}
    payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Upstream response was not an object"
        )
    return payload


def _upstream_error_detail(response: httpx.Response) -> object:
    try:
        body = response.json()
    except ValueError:
        return response.text or "Upstream error"
    if isinstance(body, dict) and "detail" in body:
        return body["detail"]
    return body


def _settings_for(app: FastAPI) -> FacadeSettings:
    return app.state.settings


__all__ = ["register_local_models_routes"]
