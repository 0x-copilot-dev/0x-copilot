"""Internal /internal/v1/auth/{discover,magic-link/*,sessions/select} routes.

Public ``/v1/auth/*`` lives on the facade. These backend routes are the
service-token-gated implementations the facade proxies to.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, status

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import (
    AuthDiscoverRequest,
    AuthDiscoverResponse,
    MagicLinkCallbackRequest,
    MagicLinkCallbackResult,
    MagicLinkStartRequest,
    MagicLinkStartResponse,
    SessionSelectRequest,
    SessionSelectResult,
)
from backend_app.identity.login_email_first import (
    DiscoveryRateLimited,
    DiscoveryService,
    MagicLinkInvalidToken,
    MagicLinkRateLimited,
    MagicLinkService,
    PickTokenInvalid,
    SessionSelectService,
    WorkspaceMembershipDenied,
)
from backend_app.identity.rbac import public_route


def register_login_email_first_routes(
    app: FastAPI,
    *,
    discovery: DiscoveryService,
    magic_link: MagicLinkService,
    session_select: SessionSelectService,
) -> None:
    @app.post(
        "/internal/v1/auth/discover",
        response_model=AuthDiscoverResponse,
        # Anonymous: caller is the unauthenticated browser via the facade.
        # The service-token header is still required (set by the facade).
        dependencies=[Depends(public_route())],
    )
    def discover(
        request: Request, payload: AuthDiscoverRequest
    ) -> AuthDiscoverResponse:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id="-", user_id="-"
        )
        try:
            return discovery.discover(payload)
        except DiscoveryRateLimited as exc:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "rate_limited",
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc

    @app.post(
        "/internal/v1/auth/magic-link/start",
        response_model=MagicLinkStartResponse,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(public_route())],
    )
    def magic_link_start(
        request: Request, payload: MagicLinkStartRequest
    ) -> MagicLinkStartResponse:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id="-", user_id="-"
        )
        try:
            return magic_link.request(payload)
        except MagicLinkRateLimited as exc:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "rate_limited",
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc

    @app.post(
        "/internal/v1/auth/magic-link/callback",
        response_model=MagicLinkCallbackResult,
        dependencies=[Depends(public_route())],
    )
    def magic_link_callback(
        request: Request, payload: MagicLinkCallbackRequest
    ) -> MagicLinkCallbackResult:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id="-", user_id="-"
        )
        try:
            return magic_link.consume(payload)
        except MagicLinkRateLimited as exc:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "rate_limited",
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc
        except MagicLinkInvalidToken as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, exc.reason) from exc

    @app.post(
        "/internal/v1/auth/sessions/select",
        response_model=SessionSelectResult,
        dependencies=[Depends(public_route())],
    )
    def session_select_route(
        request: Request, payload: SessionSelectRequest
    ) -> SessionSelectResult:
        BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id="-", user_id="-"
        )
        try:
            return session_select.select(payload)
        except MagicLinkRateLimited as exc:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "rate_limited",
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc
        except PickTokenInvalid as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, exc.reason) from exc
        except WorkspaceMembershipDenied as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not_a_member") from exc


__all__ = ["register_login_email_first_routes"]
