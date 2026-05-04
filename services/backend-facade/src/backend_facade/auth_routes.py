"""Public ``/v1/auth/*`` routes for the facade.

These proxy to the backend's internal ``/internal/v1/auth/sessions/*`` API.
The backend owns the source of truth (the ``sessions`` table); the facade is
the only browser-facing surface.

Wire into the FastAPI app with ``register_auth_routes(app)``.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status

from backend_facade.auth import AuthenticatedIdentity, FacadeAuthenticator
from backend_facade.settings import FacadeSettings


def register_auth_routes(app: FastAPI) -> None:
    @app.get("/v1/auth/session")
    async def get_session(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        # Mirrors the legacy /v1/session response shape so existing frontend
        # code (apps/frontend/src/api/sessionApi.ts) keeps working without
        # changes. /v1/session itself stays as-is for one release.
        return _identity_envelope(identity)

    @app.get("/v1/auth/sessions")
    async def list_sessions(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{backend_url}/internal/v1/auth/sessions",
                params={"org_id": identity.org_id, "user_id": identity.user_id},
                headers=FacadeAuthenticator.service_headers(identity),
            )
        _raise_for_upstream(response)
        return response.json()

    @app.delete(
        "/v1/auth/sessions/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def revoke_session(request: Request, session_id: str) -> Response:
        identity = FacadeAuthenticator.authenticate_request(request)
        await _revoke(app, identity, session_id, reason="user_revoked")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/v1/auth/logout")
    async def logout(request: Request) -> Response:
        identity = FacadeAuthenticator.authenticate_request(request)
        # Best-effort: derive the session_id from the bearer's `sid` claim;
        # if there isn't one (back-compat token without sid), there is no
        # server-side session to revoke and we fall through to 204. The
        # bearer cookie / localStorage clearing is the client's job.
        token = _bearer_from_request(request)
        if token is not None:
            session_id = FacadeAuthenticator.session_id_from_token(token)
            if session_id is not None:
                await _revoke(app, identity, session_id, reason="logout")
        return Response(status_code=status.HTTP_204_NO_CONTENT)


def _identity_envelope(identity: AuthenticatedIdentity) -> dict[str, object]:
    return {
        "identity": {
            "org_id": identity.org_id,
            "user_id": identity.user_id,
            "roles": list(identity.roles),
            "permission_scopes": list(identity.permission_scopes),
        }
    }


async def _revoke(
    app: FastAPI,
    identity: AuthenticatedIdentity,
    session_id: str,
    *,
    reason: str,
) -> None:
    backend_url = settings_for(app).backend_url
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{backend_url}/internal/v1/auth/sessions/{session_id}/revoke",
            json={"org_id": identity.org_id, "reason": reason},
            headers=FacadeAuthenticator.service_headers(identity),
        )
    if response.status_code == status.HTTP_404_NOT_FOUND:
        # Idempotent: revoking an unknown / cross-tenant session id looks the
        # same as a successful revoke from the user's perspective. Avoids
        # leaking "this session id exists in some other org".
        return
    _raise_for_upstream(response)


def _bearer_from_request(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header.split(" ", maxsplit=1)[1].strip()
    return token or None


def _raise_for_upstream(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    detail: Any
    try:
        body = response.json()
    except ValueError:
        detail = response.text or "Upstream auth error"
    else:
        detail = body.get("detail") if isinstance(body, dict) else body
    raise HTTPException(response.status_code, detail or "Upstream auth error")


def settings_for(app: FastAPI) -> FacadeSettings:
    # Mirrors backend_facade.app.settings_for so this module doesn't depend
    # on the app module (which would create a circular import once auth_routes
    # is registered from create_app).
    return app.state.settings


__all__ = ["register_auth_routes"]
