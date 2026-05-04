"""Public ``/v1/auth/*`` routes for the facade.

These proxy to the backend's internal ``/internal/v1/auth/sessions/*`` and
``/internal/v1/auth/oidc/*`` APIs. The backend owns the source of truth
(the ``sessions`` and ``oidc_*`` tables); the facade is the only
browser-facing surface.

Wire into the FastAPI app with ``register_auth_routes(app)``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from enterprise_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from backend_facade.auth import AuthenticatedIdentity, FacadeAuthenticator
from backend_facade.settings import FacadeSettings


_ANONYMOUS_USER = "anonymous"


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

    # ------------------------------------------------------------------
    # OIDC SSO (A3) — unauthenticated public surface.
    #
    # These endpoints serve users who do NOT yet have a bearer; they are
    # the entry + exit ramp of the SSO redirect dance. The facade still
    # sends ``x-enterprise-service-token`` to the backend so cross-service
    # calls remain authenticated; ``x-enterprise-org-id`` is supplied
    # from the query string or recovered server-side via the ``state``
    # token (whose ``org_id`` is persisted in oidc_authentications).
    # ------------------------------------------------------------------

    @app.get("/v1/auth/providers")
    async def list_providers(
        request: Request,
        org_id: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{backend_url}/internal/v1/auth/oidc/providers",
                params={"org_id": org_id},
                headers=_anonymous_service_headers(org_id=org_id),
            )
        _raise_for_upstream(response)
        return response.json()

    @app.get("/v1/auth/oidc/{provider_id}/start")
    async def oidc_start(
        request: Request,
        provider_id: str,
        org_id: str = Query(..., min_length=1),
        redirect_uri: str = Query(..., min_length=1),
        return_to: str | None = Query(None),
        format: str = Query("redirect", pattern="^(redirect|json)$"),
    ) -> Response:
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{backend_url}/internal/v1/auth/oidc/{provider_id}/authorize",
                json={
                    "org_id": org_id,
                    "provider_id": provider_id,
                    "redirect_uri": redirect_uri,
                    "return_to": return_to,
                    "ip": _client_ip(request),
                    "user_agent": _user_agent(request),
                },
                headers=_anonymous_service_headers(org_id=org_id),
            )
        _raise_for_upstream(response)
        body = response.json()
        if format == "redirect":
            return RedirectResponse(
                url=body["auth_url"], status_code=status.HTTP_302_FOUND
            )
        return Response(content=response.content, media_type="application/json")

    @app.get("/v1/auth/oidc/callback")
    async def oidc_callback(
        request: Request,
        state: str = Query(..., min_length=1),
        code: str | None = Query(None),
        error: str | None = Query(None),
        error_description: str | None = Query(None),
    ) -> dict[str, object]:
        if error or not code:
            # Surface the IdP's failure verbatim (without the bearer it would
            # have minted) so the frontend can show a useful message.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                error_description
                or error
                or "OIDC callback missing authorization code",
            )
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{backend_url}/internal/v1/auth/oidc/callback",
                json={
                    "state": state,
                    "code": code,
                    "ip": _client_ip(request),
                    "user_agent": _user_agent(request),
                },
                headers=_anonymous_service_headers(org_id="-"),
            )
        _raise_for_upstream(response)
        return response.json()

    # ------------------------------------------------------------------
    # Local password (A4) — login + reset surfaces.
    # ------------------------------------------------------------------

    @app.post("/v1/auth/login")
    async def login(request: Request, payload: dict[str, object]) -> dict[str, object]:
        org_id = _required_str(payload, "org_id")
        email = _required_str(payload, "email")
        password = _required_str(payload, "password")
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{backend_url}/internal/v1/auth/local/verify",
                json={
                    "org_id": org_id,
                    "email": email,
                    "password": password,
                    "ip": _client_ip(request),
                    "user_agent": _user_agent(request),
                },
                headers=_anonymous_service_headers(org_id=org_id),
            )
        _raise_for_upstream(response)
        return response.json()

    @app.post(
        "/v1/auth/password/reset/request",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def password_reset_request(
        request: Request, payload: dict[str, object]
    ) -> Response:
        org_id = _required_str(payload, "org_id")
        email = _required_str(payload, "email")
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{backend_url}/internal/v1/auth/password/reset/request",
                json={
                    "org_id": org_id,
                    "email": email,
                    "ip": _client_ip(request),
                },
                headers=_anonymous_service_headers(org_id=org_id),
            )
        # Always 202 regardless of upstream — anti-enumeration.
        return Response(status_code=status.HTTP_202_ACCEPTED)

    @app.post(
        "/v1/auth/password/reset/confirm",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def password_reset_confirm(
        request: Request, payload: dict[str, object]
    ) -> Response:
        token = _required_str(payload, "token")
        new_password = _required_str(payload, "new_password")
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{backend_url}/internal/v1/auth/password/reset/confirm",
                json={"token": token, "new_password": new_password},
                headers=_anonymous_service_headers(org_id="-"),
            )
        _raise_for_upstream(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/v1/auth/password/change",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def password_change(request: Request, payload: dict[str, object]) -> Response:
        identity = FacadeAuthenticator.authenticate_request(request)
        current = _required_str(payload, "current_password")
        new = _required_str(payload, "new_password")
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{backend_url}/internal/v1/auth/password/change",
                json={
                    "org_id": identity.org_id,
                    "user_id": identity.user_id,
                    "current_password": current,
                    "new_password": new,
                },
                headers=FacadeAuthenticator.service_headers(identity),
            )
        _raise_for_upstream(response)
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


def _anonymous_service_headers(*, org_id: str) -> dict[str, str]:
    """Headers for unauthenticated public OIDC routes.

    The user has no bearer yet (they're literally trying to log in). The
    facade still authenticates to the backend via the service token; the
    org_id comes from the query string (or "-" placeholder when the
    backend will recover it from the state token).
    """

    return {
        SERVICE_TOKEN_HEADER: os.environ.get("ENTERPRISE_SERVICE_TOKEN", "").strip(),
        ORG_HEADER: org_id,
        USER_HEADER: _ANONYMOUS_USER,
    }


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    return request.client.host


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _required_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"missing required field: {key}"
        )
    return value


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
