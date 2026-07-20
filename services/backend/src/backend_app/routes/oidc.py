"""Backend internal OIDC endpoints (A3).

All routes mount under ``/internal/v1/auth/oidc/*`` and require the service
token. Public ``/v1/auth/oidc/*`` lives on the facade.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import public_route
from backend_app.contracts import (
    AuthProviderKind,
    AuthProviderRecord,
    OidcAuthorizeRequest,
    OidcAuthorizeResult,
    OidcCallbackRequest,
    OidcCallbackResult,
    OidcLinkCallbackResult,
    OidcProviderSummary,
    OidcProvidersResponse,
)
from backend_app.identity import (
    AccountLocked,
    IdTokenVerificationError,
    IdentityStore,
    OidcConfigError,
    OidcEmailNotVerified,
    OidcIdentityAlreadyLinked,
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
    global_providers: dict[str, AuthProviderRecord] | None = None,
) -> None:
    # Deployment-global providers (env-configured, e.g. "google") are
    # advertised to every org — and to org-less callers (org_id="-") so the
    # pre-workspace login screen can render "Continue with Google".
    resolved_global_providers = dict(global_providers or {})

    @app.post(
        "/internal/v1/auth/oidc/{provider_id}/authorize",
        response_model=OidcAuthorizeResult,
        # SSO entry ramp: caller has no session yet.
        dependencies=[Depends(public_route())],
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
        # Union response: a sign-in flow returns OidcCallbackResult (session
        # handoff); a link-bound flow returns OidcLinkCallbackResult (no
        # session — the caller is already signed in, PRD FR-L2).
        response_model=None,
        # SSO exit ramp: callback mints the session itself.
        dependencies=[Depends(public_route())],
    )
    def callback(
        request: Request, payload: OidcCallbackRequest
    ) -> OidcCallbackResult | OidcLinkCallbackResult:
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
        except OidcIdentityAlreadyLinked as exc:
            # FR-M1 / D-01: the conflict is a merge trigger (owner not leaked).
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "merge_required",
                    "safe_message": (
                        "This Google account already belongs to another "
                        "0xCopilot account. Linking it will merge that "
                        "account into this one."
                    ),
                },
            ) from exc
        except OidcEmailNotVerified as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
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
        # The login page lists IdP buttons before the user has a session.
        dependencies=[Depends(public_route())],
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
            # Reserved global ids never surface as per-org rows — resolution
            # would shadow them anyway, so hide the stale duplicate.
            and record.provider_id not in resolved_global_providers
        ]
        for global_record in resolved_global_providers.values():
            if global_record.kind != AuthProviderKind.OIDC:
                continue
            if not global_record.enabled:
                continue
            oidc_only.append(
                OidcProviderSummary(
                    provider_id=global_record.provider_id,
                    kind=global_record.kind,
                    display_name=global_record.display_name,
                    enabled=global_record.enabled,
                )
            )
        return OidcProvidersResponse(providers=tuple(oidc_only))


__all__ = ["register_oidc_routes"]
