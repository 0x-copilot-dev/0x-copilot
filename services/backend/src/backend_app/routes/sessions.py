"""Internal session endpoints for the backend service.

All routes are mounted under ``/internal/v1/auth/sessions/*`` and require the
service-token header — facade is the only legitimate caller. Public-facing
``/v1/auth/*`` lives on the facade and proxies the relevant subset.

Wire into the FastAPI app with ``register_session_routes(app, service)``.
"""

from __future__ import annotations


from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes, public_route
from backend_app.contracts import (
    CreateSessionRequest,
    DevMintRequest,
    RevokeSessionRequest,
    SessionListItem,
    SessionListResponse,
    SessionMintResult,
    SessionRecord,
    SessionTouchResult,
    TouchSessionRequest,
)
from backend_app.identity import (
    DevMintNotAllowed,
    SessionNotActive,
    SessionService,
)


def register_session_routes(app: FastAPI, service: SessionService) -> None:
    """Register POST/GET/DELETE routes against the given ``SessionService``."""

    @app.post(
        "/internal/v1/auth/sessions",
        response_model=SessionMintResult,
        status_code=status.HTTP_201_CREATED,
        # Login mints sessions; the caller does not have one yet.
        # ENTERPRISE_SERVICE_TOKEN is the trust anchor here.
        dependencies=[Depends(public_route())],
    )
    def create_session(
        request: Request, payload: CreateSessionRequest
    ) -> SessionMintResult:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        return service.create(
            org_id=payload.org_id,
            user_id=payload.user_id,
            roles=payload.roles,
            permission_scopes=payload.permission_scopes,
            connector_scopes=payload.connector_scopes,
            ttl_seconds=payload.ttl_seconds,
            auth_provider_id=payload.auth_provider_id,
            client_ip=payload.client_ip,
            user_agent=payload.user_agent,
            device_label=payload.device_label,
        )

    @app.post(
        "/internal/v1/auth/sessions/touch",
        response_model=SessionTouchResult,
        # Per-request hot path. The session may be mfa:pending — touch
        # must succeed so the facade can read the live identity.
        dependencies=[Depends(public_route())],
    )
    def touch_session(
        request: Request, payload: TouchSessionRequest
    ) -> SessionTouchResult:
        # Touch is the per-request hot path — it always requires the service
        # token (not the dev fallback) so a request that never went through
        # the facade can't refresh somebody's session.
        BackendServiceAuthenticator.internal_scoped_identity(
            request,
            org_id="-",  # placeholder; touch identifies session by sid+hash
            user_id="-",
        )
        try:
            return service.touch_by_components(
                session_id=payload.session_id, token_hash=payload.token_hash
            )
        except SessionNotActive as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    @app.post(
        "/internal/v1/auth/sessions/{session_id}/revoke",
        status_code=status.HTTP_204_NO_CONTENT,
        # Users revoke their own sessions; admins use admin:users.
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def revoke_session(
        request: Request, session_id: str, payload: RevokeSessionRequest
    ) -> Response:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id="-"
        )
        revoked = service.revoke(
            org_id=identity.org_id, session_id=session_id, reason=payload.reason
        )
        # Idempotent — a no-op revoke returns 204 too. The cross-tenant
        # guard inside the store ensures we never reveal "this session
        # exists in another org" to the caller.
        del revoked
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get(
        "/internal/v1/auth/sessions",
        response_model=SessionListResponse,
        # Users see their own active sessions.
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_sessions(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> SessionListResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        records = service.list_active(org_id=identity.org_id, user_id=identity.user_id)
        return SessionListResponse(
            sessions=tuple(_record_to_item(record) for record in records)
        )

    @app.post(
        "/internal/v1/auth/sessions/dev-mint",
        response_model=SessionMintResult,
        status_code=status.HTTP_201_CREATED,
        # Dev/test only — already gated by deployment-profile toggle at
        # SessionService construction time.
        dependencies=[Depends(public_route())],
    )
    def dev_mint_session(
        request: Request, payload: DevMintRequest
    ) -> SessionMintResult:
        # dev-mint is gated by the deployment-profile toggle (set at app
        # boot); also requires the service token like every other internal
        # endpoint. In production profiles the SessionService instance was
        # constructed with ``dev_mint_allowed=False`` so the call raises.
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        try:
            return service.dev_mint(
                org_id=payload.org_id,
                user_id=payload.user_id,
                roles=payload.roles,
                permission_scopes=payload.permission_scopes,
                connector_scopes=payload.connector_scopes,
                ttl_seconds=payload.ttl_seconds,
            )
        except DevMintNotAllowed as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


def _record_to_item(record: SessionRecord) -> SessionListItem:
    return SessionListItem(
        session_id=record.session_id,
        org_id=record.org_id,
        user_id=record.user_id,
        auth_provider_id=record.auth_provider_id,
        device_label=record.device_label,
        client_ip=record.client_ip,
        user_agent=record.user_agent,
        created_at=record.created_at,
        last_seen_at=record.last_seen_at,
        expires_at=record.expires_at,
        mfa_satisfied=record.mfa_satisfied_at is not None,
    )


__all__ = ["register_session_routes"]
