"""Facade proxy for ``/v1/local-models/*`` (Round 2 — local Ollama models).

Thin passthrough to ai-backend: JSON for status/list/size/delete and a
byte-for-byte SSE proxy for the pull-progress stream. No orchestration here
(that lives in ai-backend); the facade only authenticates and forwards. The
feature is gated in ai-backend, so a disabled deployment 404s these here too.

PRD-P8 §4.3 adds ``POST /v1/local-models/runtime/start``. Unlike every other
route in this module it is **not** read-only: it asks ai-backend to spawn the
local model runtime as an OS process on the user's machine. Two consequences:

1. It authenticates through the async DB-backed ``verify_with_touch`` path
   (the same one every other state-changing facade module uses — todos,
   connectors, agents) rather than the sync HMAC-only
   ``authenticate_request`` used by the read routes here, so a revoked
   session cannot start a process inside the 30s HMAC-only blind spot.
2. It emits a structured ``LogEvent`` (who / what / when / outcome) on every
   call, success or failure. The durable audit *row* is written by
   ai-backend, which owns the side effect — this module has no audit store
   and the facade's only other audit involvement is the read-only
   ``/v1/audit`` merge in :mod:`backend_facade.audit_routes`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from backend_facade.auth import AuthenticatedIdentity, FacadeAuthenticator
from backend_facade.http_client import http_client
from backend_facade.observability import get_logger
from backend_facade.settings import FacadeSettings

_BASE = "/v1/local-models"
_SSE_MEDIA_TYPE = "text/event-stream"

# ``runtime/start`` spawns the runtime and then polls it to a bounded
# timeout upstream. The facade's own read timeout has to sit above that
# bound or the caller sees a facade timeout for a start that in fact
# succeeded.
_RUNTIME_START_PATH = f"{_BASE}/runtime/start"
_RUNTIME_START_TIMEOUT_SECONDS = 60

_AUDIT_EVENT_RUNTIME_START = "local_models.runtime.start"
_AUDIT_LOGGER_NAME = "backend_facade.local_models"
_AUDIT_OUTCOME_STARTED = "started"
_AUDIT_OUTCOME_REJECTED = "rejected"
_AUDIT_OUTCOME_UNREACHABLE = "unreachable"


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

    # PRD-P8 §4.3. Declared BEFORE the ``{name:path}`` wildcard below so the
    # literal path wins FastAPI's registration-order matcher. (Starlette
    # defers method-only mismatches, so a DELETE-scoped wildcard would not
    # actually swallow a POST — but ordering makes the guarantee structural
    # instead of dependent on router internals, and survives someone widening
    # the wildcard's method set later.)
    @app.post(_RUNTIME_START_PATH)
    async def local_models_runtime_start(request: Request) -> dict[str, object]:
        settings = _settings_for(app)
        client = http_client(app)
        # State-changing route → DB-backed identity, not the sync HMAC-only
        # path the read routes in this module use.
        identity = await FacadeAuthenticator.verify_with_touch(
            request, backend_url=settings.backend_url, http_client=client
        )
        try:
            response = await client.post(
                f"{settings.ai_backend_url}{_RUNTIME_START_PATH}",
                params=identity.scoped_params(),
                headers=FacadeAuthenticator.service_headers(identity),
                timeout=_RUNTIME_START_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            # Distinct outcome: upstream never answered, so we genuinely do
            # not know whether the runtime started. Recording this as
            # "rejected" would assert something the facade cannot know.
            _emit_runtime_start_audit(
                identity,
                status_code=status.HTTP_502_BAD_GATEWAY,
                runtime_state=None,
                outcome=_AUDIT_OUTCOME_UNREACHABLE,
            )
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Local model runtime is unavailable",
            ) from exc
        _emit_runtime_start_audit(
            identity,
            status_code=response.status_code,
            runtime_state=_runtime_state_of(response),
        )
        # Faithful passthrough: a deployment with ``enable_local_models`` or
        # ``manage_runtime`` off 404s upstream and must 404 here, not 500.
        return _coerce_object_or_raise(response)

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


def _emit_runtime_start_audit(
    identity: AuthenticatedIdentity,
    *,
    status_code: int,
    runtime_state: str | None,
    outcome: str | None = None,
) -> None:
    """Record who asked to start the local runtime, when, and what happened.

    Shape is the facade's existing ``LogEvent`` (Pydantic-validated,
    denylist-redacted, scalar-only metadata) obtained via ``get_logger`` —
    no new logging or audit primitive is introduced here. Failures log at
    ``warning`` so a start that was attempted and refused is never silent.
    """

    logger = get_logger(_AUDIT_LOGGER_NAME)
    metadata: dict[str, object] = {
        "runtime": "ollama",
        "outcome": outcome
        or (_AUDIT_OUTCOME_STARTED if status_code < 400 else _AUDIT_OUTCOME_REJECTED),
    }
    if runtime_state is not None:
        metadata["runtime_state"] = runtime_state
    fields: dict[str, object] = {
        "org_id": identity.org_id,
        "user_id": identity.user_id,
        "route": _RUNTIME_START_PATH,
        "method": "POST",
        "status_code": status_code,
        "metadata": metadata,
    }
    if status_code >= 400:
        logger.warning(_AUDIT_EVENT_RUNTIME_START, **fields)
    else:
        logger.info(_AUDIT_EVENT_RUNTIME_START, **fields)


def _runtime_state_of(response: httpx.Response) -> str | None:
    """Best-effort ``runtime_state`` from a successful upstream status body.

    Audit content only — never a control-flow input, and absence is normal
    (PRD-P8 D3 makes the field optional).
    """

    if response.status_code >= 400 or not response.content:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    value = body.get("runtime_state")
    return value if isinstance(value, str) else None


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
