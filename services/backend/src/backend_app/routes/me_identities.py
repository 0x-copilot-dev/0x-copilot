"""``POST /internal/v1/me/identities/wallet`` — authenticated wallet link.

Account-linking PRD FR-L1/L3/L6 (docs/plan/account-linking/PRD.md). The
caller-scoped counterpart of the public SIWE verify: the same
proof-of-ownership pipeline, but the proven wallet binds to the CALLER's
``(org_id, user_id)`` (from the verified session headers, never the body)
and no session is minted.

Conflict (FR-M1): a wallet already owned by a different account surfaces as
409 ``merge_required`` — the account-merge engine (PRD §6.3, a later PR)
takes over from there; until it lands the client shows the honest conflict.
"""

from __future__ import annotations

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import OidcAuthorizeResult, SiweLinkWalletResult
from backend_app.identity.oidc import (
    OidcConfigError,
    OidcProviderDisabled,
    OidcService,
)
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.siwe import (
    SiweError,
    SiweRateLimited,
    SiweService,
    SiweUserNotProvisioned,
    SiweWalletAlreadyLinked,
)


class LinkWalletRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    signature: str


class LinkGoogleStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    redirect_uri: str
    return_to: str | None = None


def register_me_identities_routes(
    app: FastAPI,
    *,
    siwe_service: SiweService | None = None,
    oidc_service: OidcService | None = None,
) -> None:
    """Attach ``/internal/v1/me/identities/*``. No-op parts degrade honestly.

    The services are optional so deployments without the auth block (and
    older test harnesses) keep booting; the routes then answer 503.
    """

    @app.post(
        "/internal/v1/me/identities/wallet",
        response_model=SiweLinkWalletResult,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def link_wallet(
        request: Request,
        payload: LinkWalletRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> SiweLinkWalletResult:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        if siwe_service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "wallet_linking_unavailable"
            )
        try:
            return siwe_service.link_wallet(
                org_id=identity.org_id,
                user_id=identity.user_id,
                message=payload.message,
                signature=payload.signature,
                ip=request.headers.get("x-forwarded-for"),
                user_agent=request.headers.get("user-agent"),
            )
        except SiweWalletAlreadyLinked as exc:
            # FR-M1 / D-01: the conflict is a merge trigger. Surface a
            # structured 409 the client (and the future merge engine's
            # confirm step) can branch on. The owning user id is NOT
            # leaked — only the fact of the conflict.
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "merge_required",
                    "safe_message": (
                        "This wallet already belongs to another account. "
                        "Linking it will merge that account into this one."
                    ),
                },
            ) from exc
        except SiweRateLimited as exc:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                exc.detail,
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc
        except SiweUserNotProvisioned as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, exc.detail) from exc
        except SiweError as exc:
            # Message/signature/nonce/origin failures — client mistakes, 400.
            raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.detail) from exc

    @app.post(
        "/internal/v1/me/identities/google/link/start",
        response_model=OidcAuthorizeResult,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def link_google_start(
        request: Request,
        payload: LinkGoogleStartRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> OidcAuthorizeResult:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        if oidc_service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "google_linking_unavailable"
            )
        try:
            # The link binding is written server-side onto the state row from
            # the VERIFIED identity (PRD FR-L2/L3) — the browser round-trip
            # (and the public callback) never carry it. The callback's fork
            # recovers it from the consumed row and attaches the identity to
            # this caller instead of provisioning/signing-in.
            return oidc_service.authorize(
                org_id=identity.org_id,
                provider_id="google",
                redirect_uri=payload.redirect_uri,
                return_to=payload.return_to,
                ip=request.headers.get("x-forwarded-for"),
                user_agent=request.headers.get("user-agent"),
                link_org_id=identity.org_id,
                link_user_id=identity.user_id,
            )
        except OidcProviderDisabled as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except OidcConfigError as exc:
            # Most commonly: Google OAuth is not configured on this deployment.
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


__all__ = [
    "LinkGoogleStartRequest",
    "LinkWalletRequest",
    "register_me_identities_routes",
]
