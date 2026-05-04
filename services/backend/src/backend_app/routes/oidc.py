"""Backend internal OIDC endpoints (A3).

All routes mount under ``/internal/v1/auth/oidc/*`` and require the service
token. Public ``/v1/auth/oidc/*`` lives on the facade.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, Request, status

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import (
    AuthProviderKind,
    OidcAuthorizeRequest,
    OidcAuthorizeResult,
    OidcCallbackRequest,
    OidcCallbackResult,
    OidcProviderSummary,
    OidcProvidersResponse,
)
from backend_app.identity import (
    AccountLocked,
    IdTokenVerificationError,
    IdentityStore,
    OidcConfigError,
    OidcProviderDisabled,
    OidcService,
    OidcStateMismatch,
    OidcTokenExchangeError,
    OidcUserNotProvisioned,
)


def register_oidc_routes(
    app: FastAPI,
    *,
    service: OidcService,
    identity_store: IdentityStore,
) -> None:
    @app.post(
        "/internal/v1/auth/oidc/{provider_id}/authorize",
        response_model=OidcAuthorizeResult,
    )
    def authorize(
        request: Request, provider_id: str, payload: OidcAuthorizeRequest
    ) -> OidcAuthorizeResult:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id="-"
        )
        try:
            return service.authorize(
                org_id=payload.org_id,
                provider_id=provider_id,
                redirect_uri=payload.redirect_uri,
                return_to=payload.return_to,
                ip=payload.ip,
                user_agent=payload.user_agent,
            )
        except OidcConfigError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except OidcProviderDisabled as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc

    @app.post(
        "/internal/v1/auth/oidc/callback",
        response_model=OidcCallbackResult,
    )
    def callback(request: Request, payload: OidcCallbackRequest) -> OidcCallbackResult:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id="-", user_id="-"
        )
        try:
            return service.callback(
                state=payload.state,
                code=payload.code,
                ip=payload.ip,
                user_agent=payload.user_agent,
            )
        except AccountLocked as exc:
            headers = (
                {"Retry-After": str(exc.retry_after_seconds)}
                if exc.retry_after_seconds > 0
                else {}
            )
            raise HTTPException(
                status.HTTP_423_LOCKED, str(exc), headers=headers
            ) from exc
        except OidcStateMismatch as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except IdTokenVerificationError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
        except OidcUserNotProvisioned as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
        except OidcTokenExchangeError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        except OidcConfigError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.get(
        "/internal/v1/auth/oidc/providers",
        response_model=OidcProvidersResponse,
    )
    def list_providers(
        request: Request,
        org_id: str = Query(..., min_length=1),
    ) -> OidcProvidersResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id="-"
        )
        records = identity_store.list_auth_providers(
            org_id=identity.org_id, enabled_only=True
        )
        # Public listing: only OIDC kind. Local / SAML / SCIM enrich the list
        # in their own PRs (A4 / A5 / A7).
        oidc_only = [
            OidcProviderSummary(
                provider_id=record.provider_id,
                kind=record.kind,
                display_name=record.display_name,
                enabled=record.enabled,
            )
            for record in records
            if record.kind == AuthProviderKind.OIDC
        ]
        return OidcProvidersResponse(providers=tuple(oidc_only))


__all__ = ["register_oidc_routes"]
