"""``/internal/v1/me/mfa/*`` — caller-scoped MFA enrollment for the
Settings → Profile → Sign-in & security card.

The legacy ``/internal/v1/auth/mfa/*`` surface ([routes/mfa.py]) takes
``org_id`` / ``user_id`` in either query strings *or* request bodies,
which makes the facade's generic ``_forward_me`` helper awkward (bodies
would need rewriting). This file mirrors the four caller-scoped
operations the Settings UI needs (list / enroll TOTP / confirm /
disable) but reads identity from query params only — the same shape
``me_profile`` and ``me_preferences`` already use, so the facade just
forwards bodies verbatim.

We delegate every operation to ``MfaService``; auditing, RLS, and
replay-protection live there. The routes are 5–10 lines each.
"""

from __future__ import annotations

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import (
    MfaFactorListResponse,
    MfaFactorSummary,
    TotpEnrollResult,
)
from backend_app.identity.mfa import (
    MfaCodeRejected,
    MfaChallengeInvalid,
    MfaFactorNotFound,
    MfaService,
)
from backend_app.identity.rbac import RequireScopes


class _MeContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MeTotpEnrollRequest(_MeContract):
    display_name: str = Field(..., min_length=1, max_length=64)


class MeTotpConfirmRequest(_MeContract):
    factor_id: str = Field(..., min_length=1)
    code: str = Field(..., min_length=4, max_length=10)


def register_me_mfa_routes(app: FastAPI, *, service: MfaService) -> None:
    """Mount the caller-scoped MFA routes — facade-friendly shape."""

    @app.get(
        "/internal/v1/me/mfa/factors",
        response_model=MfaFactorListResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_factors(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> MfaFactorListResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        records = service.list_factors(org_id=identity.org_id, user_id=identity.user_id)
        return MfaFactorListResponse(
            factors=tuple(
                MfaFactorSummary(
                    factor_id=r.factor_id,
                    kind=r.kind,
                    display_name=r.display_name,
                    enabled=r.enabled,
                    enrolled_at=r.enrolled_at,
                    last_used_at=r.last_used_at,
                )
                for r in records
            )
        )

    @app.post(
        "/internal/v1/me/mfa/factors/totp/enroll",
        response_model=TotpEnrollResult,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def enroll_totp(
        request: Request,
        payload: MeTotpEnrollRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> TotpEnrollResult:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return service.enroll_totp(
            org_id=identity.org_id,
            user_id=identity.user_id,
            display_name=payload.display_name,
        )

    @app.post(
        "/internal/v1/me/mfa/factors/totp/confirm",
        status_code=status.HTTP_200_OK,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def confirm_totp(
        request: Request,
        payload: MeTotpConfirmRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            factor = service.confirm_totp(
                org_id=identity.org_id,
                user_id=identity.user_id,
                factor_id=payload.factor_id,
                code=payload.code,
            )
        except MfaFactorNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except (MfaCodeRejected, MfaChallengeInvalid) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return {"factor_id": factor.factor_id, "enabled": factor.enabled}

    @app.delete(
        "/internal/v1/me/mfa/factors/{factor_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def disable_factor(
        request: Request,
        factor_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> None:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            service.disable_factor(
                org_id=identity.org_id,
                user_id=identity.user_id,
                factor_id=factor_id,
            )
        except MfaFactorNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


__all__ = ["register_me_mfa_routes"]
