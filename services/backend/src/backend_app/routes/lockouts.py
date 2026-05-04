"""Backend internal lockout + login-attempt admin endpoints (A8).

All routes mount under ``/internal/v1/auth/*`` and require the service
token. The facade re-exposes ``/v1/auth/me/login-attempts`` for the
caller's own history; admin unlock + listing stays internal until A10
RBAC enforces the ``admin:users`` scope on the public side.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, HTTPException, Query, Request, status

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import (
    AccountLockoutListResponse,
    AccountUnlockRequest,
    LoginAttemptListResponse,
)
from backend_app.identity import (
    IdentityStore,
    LockoutService,
)


def register_lockout_routes(
    app: FastAPI,
    *,
    identity_store: IdentityStore,
    lockout_service: LockoutService,
) -> None:
    @app.post(
        "/internal/v1/auth/lockouts/{user_id}/unlock",
        status_code=status.HTTP_200_OK,
    )
    def force_unlock(
        request: Request, user_id: str, payload: AccountUnlockRequest
    ) -> dict[str, object]:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id="-"
        )
        unlocked = lockout_service.force_unlock(
            org_id=identity.org_id,
            user_id=user_id,
            unlocked_by_user_id=identity.user_id,
            reason=payload.reason,
        )
        return {
            "ok": unlocked is not None,
            "lockout_id": unlocked.lockout_id if unlocked else None,
        }

    @app.get(
        "/internal/v1/auth/lockouts",
        response_model=AccountLockoutListResponse,
    )
    def list_lockouts(
        request: Request,
        org_id: str = Query(..., min_length=1),
        active: bool = Query(False),
        limit: int = Query(100, ge=1, le=500),
    ) -> AccountLockoutListResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id="-"
        )
        # ``identity.org_id`` is the verified-from-headers value; using it
        # rather than the query string prevents a service-token caller from
        # bypassing the scoped check by lying about org_id in the URL.
        records = identity_service_lockouts(
            identity_store=identity_store,
            lockout_service=lockout_service,
            org_id=identity.org_id,
            active=active,
            limit=limit,
        )
        return AccountLockoutListResponse(lockouts=records)

    @app.get(
        "/internal/v1/auth/login-attempts",
        response_model=LoginAttemptListResponse,
    )
    def list_login_attempts(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str | None = Query(None),
        email: str | None = Query(None),
        since: datetime | None = Query(None),
        limit: int = Query(100, ge=1, le=500),
    ) -> LoginAttemptListResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id or "-"
        )
        attempts = identity_store.list_login_attempts(
            org_id=identity.org_id,
            user_id=user_id,
            email=email,
            limit=limit,
        )
        if since is not None:
            attempts = tuple(a for a in attempts if a.created_at >= since)
        return LoginAttemptListResponse(attempts=attempts)

    # Self-service "my login attempts" endpoint (A8 §2.3). Backend exposes
    # internally; facade proxies it as ``/v1/auth/me/login-attempts``.
    @app.get(
        "/internal/v1/auth/me/login-attempts",
        response_model=LoginAttemptListResponse,
    )
    def list_my_login_attempts(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        limit: int = Query(20, ge=1, le=200),
    ) -> LoginAttemptListResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        attempts = identity_store.list_login_attempts(
            org_id=identity.org_id,
            user_id=identity.user_id,
            limit=limit,
        )
        return LoginAttemptListResponse(attempts=attempts)

    # Surfaces 404 when called with a non-existent user_id so admin tools
    # can distinguish "user has no lockouts" from "user does not exist".
    @app.get(
        "/internal/v1/auth/lockouts/{user_id}",
        status_code=status.HTTP_200_OK,
    )
    def get_user_lockout(
        request: Request,
        user_id: str,
        org_id: str = Query(..., min_length=1),
    ) -> dict[str, object]:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id="-"
        )
        if identity_store.get_user(org_id=identity.org_id, user_id=user_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        active = lockout_service.policy_for(org_id=identity.org_id)
        return {
            "policy": active.model_dump(mode="json"),
            "active_lockout": _active_lockout_payload(
                lockout_service=lockout_service,
                org_id=identity.org_id,
                user_id=user_id,
            ),
        }


def identity_service_lockouts(
    *,
    identity_store: IdentityStore,
    lockout_service: LockoutService,
    org_id: str,
    active: bool,
    limit: int,
) -> tuple:
    # Stable seam so a future PR can replace the call with a paginated
    # store fetch without re-touching the route handler.
    del identity_store
    return lockout_service._lockout_store.list_lockouts(  # noqa: SLF001
        org_id=org_id, active_only=active, limit=limit
    )


def _active_lockout_payload(
    *, lockout_service: LockoutService, org_id: str, user_id: str
) -> dict[str, object] | None:
    active = lockout_service._lockout_store.get_active_lockout(  # noqa: SLF001
        org_id=org_id, user_id=user_id
    )
    return active.model_dump(mode="json") if active is not None else None


__all__ = ["register_lockout_routes"]
