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

from backend_facade.auth import (
    AuthenticatedIdentity,
    FacadeAuthenticator,
    requires_recent_mfa,
)
from backend_facade.settings import FacadeSettings


# Default step-up window for sensitive routes when the org policy
# doesn't override it. Mirrors the spec default in A6 §2.4 (5 minutes).
_DEFAULT_STEP_UP_SECONDS = 300


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
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            identity = await FacadeAuthenticator.verify_with_touch(
                request, backend_url=backend_url, http_client=client
            )
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
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            # cache_bypass=True: revoking a session is a sensitive operation
            # that must run against the canonical DB state, not a cached
            # identity that could itself be revoked already.
            identity = await FacadeAuthenticator.verify_with_touch(
                request,
                backend_url=backend_url,
                http_client=client,
                cache_bypass=True,
            )
        await _revoke(app, identity, session_id, reason="user_revoked")
        # Best-effort: drop the caller's own cached identity so the very next
        # request reflects the new state if they revoked their own session.
        token = _bearer_from_request(request)
        if token is not None:
            FacadeAuthenticator.invalidate_touch_cache(
                FacadeAuthenticator.token_hash_from_signature(token)
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/v1/auth/logout")
    async def logout(request: Request) -> Response:
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            identity = await FacadeAuthenticator.verify_with_touch(
                request,
                backend_url=backend_url,
                http_client=client,
                cache_bypass=True,
            )
        # Best-effort: derive the session_id from the bearer's `sid` claim;
        # if there isn't one (back-compat token without sid), there is no
        # server-side session to revoke and we fall through to 204.
        token = _bearer_from_request(request)
        if token is not None:
            session_id = FacadeAuthenticator.session_id_from_token(token)
            if session_id is not None:
                await _revoke(app, identity, session_id, reason="logout")
            FacadeAuthenticator.invalidate_touch_cache(
                FacadeAuthenticator.token_hash_from_signature(token)
            )
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
    # SAML 2.0 SSO (A5) — unauthenticated public surface.
    #
    # Three surfaces:
    #
    #   /start     — facade-builds the IdP redirect via the backend; either
    #                returns 302 to the IdP SSO URL (browser flow) or a JSON
    #                payload with `sso_url` + `request_xml` (test flow).
    #   /acs       — IdP POSTs the SAMLResponse here; we forward to the
    #                backend, which validates + mints a session.
    #   /metadata  — public SP metadata XML for the IdP admin to consume.
    #
    # Same anonymous service-header pattern as OIDC: the facade still sends
    # the service token, the backend pulls org_id off the resolved
    # auth_providers row.
    # ------------------------------------------------------------------

    @app.get("/v1/auth/saml/{provider_id}/start")
    async def saml_start(
        request: Request,
        provider_id: str,
        org_id: str = Query(..., min_length=1),
        relay_state: str | None = Query(None),
        format: str = Query("redirect", pattern="^(redirect|json)$"),
    ) -> Response:
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{backend_url}/internal/v1/auth/saml/{provider_id}/authorize",
                json={
                    "org_id": org_id,
                    "provider_id": provider_id,
                    "relay_state": relay_state,
                    "ip": _client_ip(request),
                    "user_agent": _user_agent(request),
                },
                headers=_anonymous_service_headers(org_id=org_id),
            )
        _raise_for_upstream(response)
        body = response.json()
        if format == "redirect":
            return RedirectResponse(
                url=body["sso_url"], status_code=status.HTTP_302_FOUND
            )
        return Response(content=response.content, media_type="application/json")

    @app.post("/v1/auth/saml/{provider_id}/acs")
    async def saml_acs(
        request: Request,
        provider_id: str,
    ) -> Response:
        # Form-encoded SAMLResponse. Both the IdP-redirect and the IdP-POST
        # bindings deliver the assertion as a form field.
        form = await request.form()
        saml_response = form.get("SAMLResponse")
        relay_state_raw = form.get("RelayState")
        if not isinstance(saml_response, str) or not saml_response:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "SAML ACS requires SAMLResponse form field",
            )
        relay_state = relay_state_raw if isinstance(relay_state_raw, str) else None
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{backend_url}/internal/v1/auth/saml/consume",
                json={
                    "provider_id": provider_id,
                    "saml_response": saml_response,
                    "relay_state": relay_state,
                    "ip": _client_ip(request),
                    "user_agent": _user_agent(request),
                },
                headers=_anonymous_service_headers(org_id="-"),
            )
        _raise_for_upstream(response)
        # The SPA consumes ``relay_state`` from the JSON body to navigate
        # post-login; the facade does not 302 here because the SPA needs
        # the bearer first. RelayState round-trip is the SPA's job.
        return Response(content=response.content, media_type="application/json")

    @app.get("/v1/auth/saml/{provider_id}/metadata")
    async def saml_metadata(
        request: Request,
        provider_id: str,
    ) -> Response:
        del request
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{backend_url}/internal/v1/auth/saml/{provider_id}/metadata",
                headers=_anonymous_service_headers(org_id="-"),
            )
        _raise_for_upstream(response)
        return Response(content=response.content, media_type="application/xml")

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
        # Sensitive operation: require a fresh MFA verify within the last
        # 5 minutes (spec A6 §2.4). cache_bypass=True so the elapsed
        # check runs against the canonical DB state, not a stale cache.
        current = _required_str(payload, "current_password")
        new = _required_str(payload, "new_password")
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            identity = await FacadeAuthenticator.verify_with_touch(
                request,
                backend_url=backend_url,
                http_client=client,
                cache_bypass=True,
            )
            requires_recent_mfa(identity, max_age_seconds=_DEFAULT_STEP_UP_SECONDS)
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

    # ------------------------------------------------------------------
    # MFA (A6) — enroll, confirm, challenge, verify, recovery.
    #
    # Every route is bearer-authenticated; the caller's identity comes
    # from the verified token, never from the request body. The session_id
    # for ``verify`` / ``recovery/consume`` is derived from the bearer's
    # ``sid`` claim so the user can't satisfy somebody else's session.
    # ------------------------------------------------------------------

    @app.get("/v1/auth/mfa/factors")
    async def list_mfa_factors(request: Request) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        return await _backend_get(
            app,
            identity,
            "/internal/v1/auth/mfa/factors",
            params={"org_id": identity.org_id, "user_id": identity.user_id},
        )

    @app.post("/v1/auth/mfa/factors/totp/enroll")
    async def mfa_totp_enroll(
        request: Request, payload: dict[str, object]
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        body = {
            "org_id": identity.org_id,
            "user_id": identity.user_id,
            "display_name": str(payload.get("display_name") or "Authenticator"),
        }
        return await _backend_post(
            app, identity, "/internal/v1/auth/mfa/factors/totp/enroll", json=body
        )

    @app.post("/v1/auth/mfa/factors/totp/confirm")
    async def mfa_totp_confirm(
        request: Request, payload: dict[str, object]
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        body = {
            "org_id": identity.org_id,
            "user_id": identity.user_id,
            "factor_id": _required_str(payload, "factor_id"),
            "code": _required_str(payload, "code"),
        }
        return await _backend_post(
            app, identity, "/internal/v1/auth/mfa/factors/totp/confirm", json=body
        )

    @app.delete(
        "/v1/auth/mfa/factors/{factor_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def mfa_disable_factor(request: Request, factor_id: str) -> Response:
        identity = FacadeAuthenticator.authenticate_request(request)
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.delete(
                f"{backend_url}/internal/v1/auth/mfa/factors/{factor_id}",
                params={"org_id": identity.org_id, "user_id": identity.user_id},
                headers=FacadeAuthenticator.service_headers(identity),
            )
        if response.status_code != status.HTTP_404_NOT_FOUND:
            _raise_for_upstream(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/v1/auth/mfa/challenge")
    async def mfa_challenge(
        request: Request, payload: dict[str, object]
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        body: dict[str, object] = {
            "org_id": identity.org_id,
            "user_id": identity.user_id,
            "kind": str(payload.get("kind") or "totp"),
        }
        if payload.get("factor_id"):
            body["factor_id"] = str(payload["factor_id"])
        return await _backend_post(
            app, identity, "/internal/v1/auth/mfa/challenge", json=body
        )

    @app.post("/v1/auth/mfa/verify")
    async def mfa_verify(
        request: Request, payload: dict[str, object]
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        bearer = _bearer_from_request(request)
        session_id = (
            FacadeAuthenticator.session_id_from_token(bearer) if bearer else None
        )
        if session_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "MFA verify requires a session-bound bearer token",
            )
        body: dict[str, object] = {
            "org_id": identity.org_id,
            "user_id": identity.user_id,
            "challenge_id": _required_str(payload, "challenge_id"),
        }
        if "code" in payload:
            body["code"] = str(payload["code"])
        if "assertion" in payload and isinstance(payload["assertion"], dict):
            body["assertion"] = payload["assertion"]
        params: dict[str, str] = {"session_id": session_id}
        if "expected_origin" in payload:
            params["expected_origin"] = str(payload["expected_origin"])
        return await _backend_post(
            app,
            identity,
            "/internal/v1/auth/mfa/verify",
            json=body,
            params=params,
        )

    @app.post("/v1/auth/mfa/recovery/consume")
    async def mfa_recovery_consume(
        request: Request, payload: dict[str, object]
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        bearer = _bearer_from_request(request)
        session_id = (
            FacadeAuthenticator.session_id_from_token(bearer) if bearer else None
        )
        if session_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "MFA recovery requires a session-bound bearer token",
            )
        body = {
            "org_id": identity.org_id,
            "user_id": identity.user_id,
            "code": _required_str(payload, "code"),
        }
        return await _backend_post(
            app,
            identity,
            "/internal/v1/auth/mfa/recovery/consume",
            json=body,
            params={"session_id": session_id},
        )

    @app.post("/v1/auth/mfa/factors/webauthn/register/start")
    async def mfa_webauthn_start(
        request: Request, payload: dict[str, object]
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        body = {
            "org_id": identity.org_id,
            "user_id": identity.user_id,
            "display_name": str(payload.get("display_name") or "Security key"),
            "rp_id": _required_str(payload, "rp_id"),
            "rp_name": _required_str(payload, "rp_name"),
            "user_name": _required_str(payload, "user_name"),
            "user_display_name": (
                str(payload.get("user_display_name"))
                if payload.get("user_display_name")
                else None
            ),
        }
        return await _backend_post(
            app,
            identity,
            "/internal/v1/auth/mfa/factors/webauthn/register/start",
            json=body,
        )

    @app.post("/v1/auth/mfa/factors/webauthn/register/finish")
    async def mfa_webauthn_finish(
        request: Request, payload: dict[str, object]
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        attestation = payload.get("attestation")
        if not isinstance(attestation, dict):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "attestation must be a JSON object"
            )
        body = {
            "org_id": identity.org_id,
            "user_id": identity.user_id,
            "factor_id": _required_str(payload, "factor_id"),
            "challenge_id": _required_str(payload, "challenge_id"),
            "rp_id": _required_str(payload, "rp_id"),
            "expected_origin": _required_str(payload, "expected_origin"),
            "attestation": attestation,
        }
        return await _backend_post(
            app,
            identity,
            "/internal/v1/auth/mfa/factors/webauthn/register/finish",
            json=body,
        )

    # ------------------------------------------------------------------
    # Login attempts (A8) — caller's own history.
    #
    # Spec §2.3 ``GET /v1/auth/me/login-attempts``. Backend validates the
    # service token + identity headers; we never accept ``user_id`` from
    # the client query, only from the verified bearer's identity.
    # ------------------------------------------------------------------

    @app.get("/v1/auth/me/login-attempts")
    async def list_my_login_attempts(
        request: Request,
        limit: int = Query(20, ge=1, le=200),
    ) -> dict[str, object]:
        identity = FacadeAuthenticator.authenticate_request(request)
        backend_url = settings_for(app).backend_url
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{backend_url}/internal/v1/auth/me/login-attempts",
                params={
                    "org_id": identity.org_id,
                    "user_id": identity.user_id,
                    "limit": limit,
                },
                headers=FacadeAuthenticator.service_headers(identity),
            )
        _raise_for_upstream(response)
        return response.json()


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


async def _backend_get(
    app: FastAPI,
    identity: AuthenticatedIdentity,
    path: str,
    *,
    params: dict[str, object] | None = None,
) -> dict[str, object]:
    backend_url = settings_for(app).backend_url
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{backend_url}{path}",
            params=params or {},
            headers=FacadeAuthenticator.service_headers(identity),
        )
    _raise_for_upstream(response)
    return response.json()


async def _backend_post(
    app: FastAPI,
    identity: AuthenticatedIdentity,
    path: str,
    *,
    json: dict[str, object] | None = None,
    params: dict[str, object] | None = None,
) -> dict[str, object]:
    backend_url = settings_for(app).backend_url
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{backend_url}{path}",
            json=json,
            params=params or {},
            headers=FacadeAuthenticator.service_headers(identity),
        )
    _raise_for_upstream(response)
    if response.status_code == status.HTTP_204_NO_CONTENT or not response.content:
        return {}
    return response.json()


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
