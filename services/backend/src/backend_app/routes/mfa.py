"""Backend internal MFA endpoints (A6).

All routes mount under ``/internal/v1/auth/mfa/*`` and require the
service token. The facade re-exposes a subset under ``/v1/auth/mfa/*``
so the user-facing app can drive enrollment + verify.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, HTTPException, Query, Request, status

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import (
    MfaChallengeKind,
    MfaChallengeRequest,
    MfaChallengeResult,
    MfaFactorListResponse,
    MfaFactorSummary,
    MfaRecoveryConsumeRequest,
    MfaVerifyRequest,
    MfaVerifyResult,
    TotpConfirmRequest,
    TotpEnrollRequest,
    TotpEnrollResult,
    WebAuthnRegisterFinishRequest,
    WebAuthnRegisterStartRequest,
    WebAuthnRegisterStartResult,
)
from backend_app.identity.mfa import (
    MfaChallengeInvalid,
    MfaCodeRejected,
    MfaError,
    MfaFactorDisabled,
    MfaFactorNotFound,
    MfaService,
    MfaWebAuthnRejected,
)
from backend_app.identity.sessions import SessionService


def register_mfa_routes(
    app: FastAPI,
    *,
    service: MfaService,
    sessions: SessionService,
) -> None:
    @app.get(
        "/internal/v1/auth/mfa/factors",
        response_model=MfaFactorListResponse,
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
        "/internal/v1/auth/mfa/factors/totp/enroll",
        response_model=TotpEnrollResult,
    )
    def enroll_totp(request: Request, payload: TotpEnrollRequest) -> TotpEnrollResult:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        return service.enroll_totp(
            org_id=identity.org_id,
            user_id=identity.user_id,
            display_name=payload.display_name,
        )

    @app.post(
        "/internal/v1/auth/mfa/factors/totp/confirm",
        status_code=status.HTTP_200_OK,
    )
    def confirm_totp(
        request: Request, payload: TotpConfirmRequest
    ) -> dict[str, object]:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
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
        "/internal/v1/auth/mfa/factors/{factor_id}",
        status_code=status.HTTP_204_NO_CONTENT,
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

    @app.post(
        "/internal/v1/auth/mfa/factors/webauthn/register/start",
        response_model=WebAuthnRegisterStartResult,
    )
    def webauthn_register_start(
        request: Request, payload: WebAuthnRegisterStartRequest
    ) -> WebAuthnRegisterStartResult:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        factor, challenge, options = service.webauthn_register_options(
            org_id=identity.org_id,
            user_id=identity.user_id,
            display_name=payload.display_name,
            rp_id=payload.rp_id,
            rp_name=payload.rp_name,
            user_name=payload.user_name,
            user_display_name=payload.user_display_name,
        )
        return WebAuthnRegisterStartResult(
            factor_id=factor.factor_id,
            challenge_id=challenge.challenge_id,
            options=options,
        )

    @app.post(
        "/internal/v1/auth/mfa/factors/webauthn/register/finish",
        status_code=status.HTTP_200_OK,
    )
    def webauthn_register_finish(
        request: Request, payload: WebAuthnRegisterFinishRequest
    ) -> dict[str, object]:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        try:
            record = service.webauthn_register_finish(
                org_id=identity.org_id,
                user_id=identity.user_id,
                factor_id=payload.factor_id,
                challenge_id=payload.challenge_id,
                rp_id=payload.rp_id,
                expected_origin=payload.expected_origin,
                attestation=payload.attestation,
            )
        except MfaChallengeInvalid as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except MfaWebAuthnRejected as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except MfaFactorNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return {"credential_id": record.credential_id}

    @app.post(
        "/internal/v1/auth/mfa/challenge",
        response_model=MfaChallengeResult,
    )
    def issue_challenge(
        request: Request, payload: MfaChallengeRequest
    ) -> MfaChallengeResult:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        try:
            challenge, options = service.issue_challenge(
                org_id=identity.org_id,
                user_id=identity.user_id,
                kind=payload.kind,
                factor_id=payload.factor_id,
            )
        except MfaFactorNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return MfaChallengeResult(
            challenge_id=challenge.challenge_id,
            nonce=challenge.nonce,
            kind=challenge.kind,
            expected_factor_id=challenge.expected_factor_id,
            expires_at=challenge.expires_at,
            webauthn_options=options,
        )

    @app.post(
        "/internal/v1/auth/mfa/verify",
        response_model=MfaVerifyResult,
    )
    def verify(
        request: Request,
        payload: MfaVerifyRequest,
        session_id: str = Query(..., min_length=1),
        expected_origin: str | None = Query(None),
    ) -> MfaVerifyResult:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        try:
            kind, factor = _verify_challenge(
                service=service,
                org_id=identity.org_id,
                user_id=identity.user_id,
                payload=payload,
                expected_origin=expected_origin,
            )
        except MfaChallengeInvalid as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except MfaWebAuthnRejected as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
        except MfaCodeRejected as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
        except MfaFactorNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except MfaFactorDisabled as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except MfaError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        # Promote the session out of ``mfa:pending`` by stamping
        # mfa_satisfied_at + (optionally) the real scopes. We don't
        # know the real scopes here without re-querying the role
        # assignments, so let the session keep whatever scopes it had
        # and rely on the route-level RBAC check. A future PR can
        # enrich the swap.
        sessions.mark_mfa_satisfied(session_id=session_id)
        return MfaVerifyResult(
            factor_id=factor.factor_id,
            kind=kind,
            mfa_satisfied_at=datetime_now_utc(),
        )

    @app.post(
        "/internal/v1/auth/mfa/recovery/consume",
        status_code=status.HTTP_200_OK,
    )
    def consume_recovery(
        request: Request,
        payload: MfaRecoveryConsumeRequest,
        session_id: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        try:
            record = service.consume_recovery_code(
                org_id=identity.org_id,
                user_id=identity.user_id,
                code=payload.code,
            )
        except MfaCodeRejected as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
        sessions.mark_mfa_satisfied(session_id=session_id)
        return {"code_id": record.code_id, "consumed_at": record.consumed_at}


def _verify_challenge(
    *,
    service: MfaService,
    org_id: str,
    user_id: str,
    payload: MfaVerifyRequest,
    expected_origin: str | None,
):
    if payload.code is not None:
        factor = service.verify_totp_challenge(
            org_id=org_id,
            user_id=user_id,
            challenge_id=payload.challenge_id,
            code=payload.code,
        )
        return MfaChallengeKind.TOTP, factor
    if payload.assertion is not None:
        if not expected_origin:
            raise MfaChallengeInvalid("expected_origin query param required")
        factor = service.verify_webauthn_challenge(
            org_id=org_id,
            user_id=user_id,
            challenge_id=payload.challenge_id,
            assertion=payload.assertion,
            expected_origin=expected_origin,
        )
        return MfaChallengeKind.WEBAUTHN, factor
    raise MfaChallengeInvalid("either ``code`` or ``assertion`` is required")


def datetime_now_utc() -> datetime:
    from datetime import timezone as _tz

    return datetime.now(_tz.utc)


__all__ = ["register_mfa_routes"]
