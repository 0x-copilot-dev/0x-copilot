"""``/internal/v1/me/identities/*`` — authenticated identity linking.

Account-linking PRD FR-L1/L2/L5/L6/M1 (docs/plan/account-linking/PRD.md).
The caller-scoped counterpart of the public sign-in ramps: the same
proof-of-ownership pipelines, but the proven identity binds to the CALLER's
``(org_id, user_id)`` (from the verified session headers, never the body)
and no session is minted.

Conflict (FR-M1/D-01): an identity already owned by a different account
surfaces as 409 ``merge_required``; re-submitting with ``confirm_merge``
(FR-U2 — explicit consent) runs the account-merge saga (PRD §6.3) and
completes the link against the survivor.
"""

from __future__ import annotations

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import OidcAuthorizeResult, SiweLinkWalletResult
from backend_app.identity.account_merge import (
    AccountMergeService,
    MergeNotAllowed,
    MergeRuntimeFailed,
)
from backend_app.identity.oidc import (
    OidcConfigError,
    OidcProviderDisabled,
    OidcService,
)
from backend_app.identity.oidc_store import OidcStore
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.siwe import (
    SiweError,
    SiweRateLimited,
    SiweService,
    SiweUserNotProvisioned,
    SiweWalletAlreadyLinked,
    chain_display_name,
)
from backend_app.identity.siwe_store import SiweStore


class LinkWalletRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    signature: str
    # FR-U2: explicit user consent that a wallet owned by ANOTHER account
    # should merge that account into this one (D-01).
    confirm_merge: bool = False


class LinkGoogleStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    redirect_uri: str
    return_to: str | None = None
    # FR-U2: consent recorded server-side on the state row at start time —
    # the public callback honors it without trusting the browser round-trip.
    confirm_merge: bool = False


_MERGE_REQUIRED_WALLET = {
    "code": "merge_required",
    "safe_message": (
        "This wallet already belongs to another account. "
        "Linking it will merge that account into this one."
    ),
}


def register_me_identities_routes(
    app: FastAPI,
    *,
    siwe_service: SiweService | None = None,
    oidc_service: OidcService | None = None,
    merge_service: AccountMergeService | None = None,
    siwe_store: SiweStore | None = None,
    oidc_store: OidcStore | None = None,
    password_store: object | None = None,
    identity_store: object | None = None,
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
            # FR-M1 / D-01: the conflict is the merge trigger. Without
            # explicit consent, surface the structured 409 the client's
            # confirm dialog branches on (owning ids are never leaked).
            if not payload.confirm_merge or merge_service is None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, _MERGE_REQUIRED_WALLET
                ) from exc
            # FR-U2/M2: consent given; the fresh SIWE signature that raised
            # this conflict IS the proof of the absorbed identity. The saga
            # re-keys the wallet row (with everything else) to the caller.
            try:
                merge_service.merge_for_conflict(
                    survivor_org_id=identity.org_id,
                    survivor_user_id=identity.user_id,
                    absorbed_org_id=exc.org_id,
                    absorbed_user_id=exc.user_id,
                    proof_ref=f"siwe:{exc.address}",
                    ip=request.headers.get("x-forwarded-for"),
                    user_agent=request.headers.get("user-agent"),
                )
            except MergeNotAllowed as merge_exc:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    {"code": merge_exc.detail, "safe_message": merge_exc.reason},
                ) from merge_exc
            except MergeRuntimeFailed as merge_exc:
                # Resumable (NFR-3/8): nothing is half-owned; retrying the
                # same confirm resumes the saga at its checkpoint.
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    {
                        "code": merge_exc.detail,
                        "safe_message": (
                            "The merge could not complete and will resume "
                            "when you retry. No data was lost."
                        ),
                    },
                ) from merge_exc
            return SiweLinkWalletResult(
                status="merged",
                wallet_id=exc.wallet_id or "",
                address=exc.address or "",
                chain_id=exc.chain_id or 0,
                chain_name=chain_display_name(exc.chain_id or 0),
            )
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
                link_confirm_merge=payload.confirm_merge,
            )
        except OidcProviderDisabled as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except OidcConfigError as exc:
            # Most commonly: Google OAuth is not configured on this deployment.
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # --- Unlink (FR-L5) ---------------------------------------------------

    def _sign_in_method_count(org_id: str, user_id: str) -> int:
        """How many ways the caller can still sign in (lockout guard)."""

        count = 0
        if siwe_store is not None:
            count += len(
                siwe_store.list_wallets_by_user(org_id=org_id, user_id=user_id)
            )
        if oidc_store is not None:
            count += len(
                oidc_store.list_identities_by_user(org_id=org_id, user_id=user_id)
            )
        if password_store is not None:
            get_credential = getattr(password_store, "get_credential", None)
            if get_credential is not None:
                try:
                    if get_credential(org_id=org_id, user_id=user_id) is not None:
                        count += 1
                except Exception:  # pragma: no cover - defensive
                    pass
        return count

    def _audit_unlink(
        org_id: str, user_id: str, action: str, metadata: dict[str, object]
    ) -> None:
        """Append-only ``identity.*_unlinked`` trail (NFR-5). Best-effort —
        an audit hiccup must not turn a completed unlink into a 500."""

        if identity_store is None:
            return
        try:
            from backend_app.contracts import IdentityAuditEventRecord

            identity_store.append_identity_audit(  # type: ignore[attr-defined]
                IdentityAuditEventRecord(
                    org_id=org_id,
                    actor_user_id=user_id,
                    subject_user_id=user_id,
                    action=action,
                    metadata=metadata,
                )
            )
        except Exception:  # pragma: no cover - defensive
            pass

    def _guard_last_method(org_id: str, user_id: str) -> None:
        # FR-L5: never let a user unlink their only way back in.
        if _sign_in_method_count(org_id, user_id) <= 1:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "last_sign_in_method",
                    "safe_message": (
                        "This is your only way to sign in. Link another "
                        "method before removing it."
                    ),
                },
            )

    @app.delete(
        "/internal/v1/me/identities/wallet/{wallet_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def unlink_wallet(
        request: Request,
        wallet_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> None:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        if siwe_store is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "wallet_linking_unavailable"
            )
        _guard_last_method(identity.org_id, identity.user_id)
        # Owner-scoped delete: a foreign wallet_id is indistinguishable from
        # an unknown one (404) — existence is never leaked. HARD delete is
        # deliberate (documented FR-L5 deviation): wallet_identities.address
        # is deployment-wide UNIQUE with no unlinked_at column, so a soft
        # unlink would block the wallet from ever re-linking; the immutable
        # audit row below is the durable record instead.
        if not siwe_store.delete_wallet_identity(
            wallet_id=wallet_id,
            org_id=identity.org_id,
            user_id=identity.user_id,
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "identity_not_found")
        _audit_unlink(
            identity.org_id,
            identity.user_id,
            "identity.wallet_unlinked",
            {"wallet_id": wallet_id},
        )

    @app.delete(
        "/internal/v1/me/identities/oidc/{identity_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def unlink_oidc(
        request: Request,
        identity_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> None:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        if oidc_store is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "google_linking_unavailable"
            )
        _guard_last_method(identity.org_id, identity.user_id)
        if not oidc_store.unlink_identity(
            identity_id=identity_id,
            org_id=identity.org_id,
            user_id=identity.user_id,
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "identity_not_found")
        _audit_unlink(
            identity.org_id,
            identity.user_id,
            "identity.oidc_unlinked",
            {"identity_id": identity_id},
        )


__all__ = [
    "LinkGoogleStartRequest",
    "LinkWalletRequest",
    "register_me_identities_routes",
]
