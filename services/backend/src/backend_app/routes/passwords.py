"""Backend internal password endpoints (A4).

Mounted under ``/internal/v1/auth/local/*`` and ``/internal/v1/auth/password/*``.
The facade public ``/v1/auth/login`` + ``/v1/auth/password/*`` proxy here.

The dev-only ``include_token_in_response`` query gate on
``/password/reset/request`` exposes the plaintext reset token in the HTTP
response so test fixtures can complete the round trip without an email
worker. Production deployments leave the gate off and rely on the notify
event the service emits.
"""

from __future__ import annotations

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes, public_route
from backend_app.contracts import (
    BootstrapAdminRequest,
    LocalLoginRequest,
    LocalLoginResult,
    PasswordChangeRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequestRequest,
    PasswordResetRequestResult,
)
from backend_app.identity import (
    AccountLocked,
    BootstrapAdminService,
    BootstrapRefused,
    LocalAuthDisabled,
    LoginRejectedError,
    PasswordChangeRejected,
    PasswordService,
    ResetTokenRejected,
    WeakPasswordError,
)


def register_password_routes(
    app: FastAPI,
    *,
    service: PasswordService,
    bootstrap: BootstrapAdminService,
) -> None:
    @app.post(
        "/internal/v1/auth/local/verify",
        response_model=LocalLoginResult,
        # Login: caller has no session yet.
        dependencies=[Depends(public_route())],
    )
    def verify_local(request: Request, payload: LocalLoginRequest) -> LocalLoginResult:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id="-"
        )
        try:
            return service.login(
                org_id=payload.org_id,
                email=payload.email,
                password=payload.password,
                ip=payload.ip,
                user_agent=payload.user_agent,
            )
        except AccountLocked as exc:
            # Spec A8 §1.2: 423 LOCKED with ``Retry-After`` so the client
            # knows when to retry. Lockout supersedes the password check —
            # a locked user with the right credential still 423s (NIST SP
            # 800-63B §5.2.2).
            headers = (
                {"Retry-After": str(exc.retry_after_seconds)}
                if exc.retry_after_seconds > 0
                else {}
            )
            raise HTTPException(
                status.HTTP_423_LOCKED, str(exc), headers=headers
            ) from exc
        except LocalAuthDisabled as exc:
            # Spec A4 §1.2: when ``identity_policy.local_password_enabled``
            # is false the route must 404, not 401 — conveys "this IdP isn't
            # enabled for this org" instead of "wrong password".
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except LoginRejectedError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    @app.post(
        "/internal/v1/auth/password/change",
        status_code=status.HTTP_204_NO_CONTENT,
        # Authenticated user changing their own password.
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def change_password(request: Request, payload: PasswordChangeRequest) -> Response:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        try:
            service.change_password(
                org_id=payload.org_id,
                user_id=payload.user_id,
                current_password=payload.current_password,
                new_password=payload.new_password,
            )
        except PasswordChangeRejected as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
        except WeakPasswordError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/internal/v1/auth/password/reset/request",
        response_model=PasswordResetRequestResult,
        # Forgot-password flow: caller has no session.
        dependencies=[Depends(public_route())],
    )
    def request_reset(
        request: Request,
        payload: PasswordResetRequestRequest,
        include_token_in_response: bool = Query(False),
    ) -> PasswordResetRequestResult:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id="-"
        )
        accepted, plaintext = service.request_reset(
            org_id=payload.org_id, email=payload.email, ip=payload.ip
        )
        return PasswordResetRequestResult(
            accepted=accepted,
            token=plaintext if include_token_in_response else None,
        )

    @app.post(
        "/internal/v1/auth/password/reset/confirm",
        status_code=status.HTTP_204_NO_CONTENT,
        # Forgot-password flow: caller has the reset token, not a session.
        dependencies=[Depends(public_route())],
    )
    def confirm_reset(
        request: Request, payload: PasswordResetConfirmRequest
    ) -> Response:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id="-", user_id="-"
        )
        try:
            service.confirm_reset(
                token=payload.token, new_password=payload.new_password
            )
        except ResetTokenRejected as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except WeakPasswordError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/internal/v1/auth/local/bootstrap-admin",
        status_code=status.HTTP_201_CREATED,
        # First-run admin creation: trust comes from the BOOTSTRAP_ADMIN_TOKEN
        # the operator carries in payload.setup_token, NOT from a session.
        dependencies=[Depends(public_route())],
    )
    def bootstrap_admin(
        request: Request,
        payload: BootstrapAdminRequest,
        initial_password: str = Query(..., min_length=12),
    ) -> dict[str, str]:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id="-"
        )
        try:
            user_id = bootstrap.bootstrap(
                org_id=payload.org_id,
                email=payload.email,
                display_name=payload.display_name,
                setup_token=payload.setup_token,
                initial_password=initial_password,
            )
        except BootstrapRefused as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except WeakPasswordError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return {"user_id": user_id}


__all__ = ["register_password_routes"]
