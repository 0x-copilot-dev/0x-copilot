"""Backend internal SIWE endpoints (Sign-In-With-Ethereum).

All routes mount under ``/internal/v1/auth/siwe/*`` and require the
service token. Public ``/v1/auth/siwe/*`` lives on the facade — same
split as OIDC / SAML / login-email-first.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, status

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import (
    SiweNonceRequest,
    SiweNonceResult,
    SiweVerifyRequest,
    SiweVerifyResult,
)
from backend_app.identity import AccountLocked
from backend_app.identity.rbac import public_route
from backend_app.identity.siwe import (
    SiweAddressInvalid,
    SiweChainNotAllowed,
    SiweDomainMismatch,
    SiweError,
    SiweExpiredMessage,
    SiweMessageInvalid,
    SiweNonceExpired,
    SiweNonceInvalid,
    SiweRateLimited,
    SiweSelfSignupDisabled,
    SiweService,
    SiweSignatureInvalid,
    SiweUserNotProvisioned,
)


# 400-level detail codes are the frozen wire contract: the facade forwards
# them verbatim and the frontend switches on them.
_BAD_REQUEST_ERRORS: tuple[type[SiweError], ...] = (
    SiweNonceInvalid,
    SiweNonceExpired,
    SiweSignatureInvalid,
    SiweDomainMismatch,
    SiweChainNotAllowed,
    SiweExpiredMessage,
    SiweMessageInvalid,
)


def register_siwe_routes(app: FastAPI, *, service: SiweService) -> None:
    @app.post(
        "/internal/v1/auth/siwe/nonce",
        response_model=SiweNonceResult,
        # Wallet sign-in entry ramp: caller has no session yet. The
        # service-token header is still required (set by the facade).
        dependencies=[Depends(public_route())],
    )
    def siwe_nonce(request: Request, payload: SiweNonceRequest) -> SiweNonceResult:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id="-", user_id="-"
        )
        try:
            return service.mint_nonce(
                address=payload.address,
                chain_id=payload.chain_id,
                ip=payload.ip,
                user_agent=payload.user_agent,
            )
        except SiweRateLimited as exc:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                exc.detail,
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc
        except SiweAddressInvalid as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, exc.detail
            ) from exc
        except SiweChainNotAllowed as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.detail) from exc

    @app.post(
        "/internal/v1/auth/siwe/verify",
        response_model=SiweVerifyResult,
        # Wallet sign-in exit ramp: verify mints the session itself.
        dependencies=[Depends(public_route())],
    )
    def siwe_verify(request: Request, payload: SiweVerifyRequest) -> SiweVerifyResult:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id="-", user_id="-"
        )
        try:
            return service.verify(
                message=payload.message,
                signature=payload.signature,
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
        except SiweSelfSignupDisabled as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail) from exc
        except SiweUserNotProvisioned as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, exc.detail) from exc
        except _BAD_REQUEST_ERRORS as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.detail) from exc


__all__ = ["register_siwe_routes"]
