"""``/internal/v1/workspace/invitations`` + ``/internal/v1/auth/invitations/{token}/accept``
(PR 4.2).

Four endpoints:

  - ``POST   /internal/v1/workspace/invitations``         — admin mint.
  - ``GET    /internal/v1/workspace/invitations``         — admin list pending.
  - ``DELETE /internal/v1/workspace/invitations/{id}``    — admin revoke.
  - ``POST   /internal/v1/auth/invitations/{token}/accept`` — unauthenticated.

The accept endpoint runs without identity headers; it dispatches by
sha256(token). Rate limiting belongs at the facade ingress / WAF.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from enterprise_service_contracts.scopes import ADMIN_USERS, RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import (
    InvitationRecord,
)
from backend_app.identity.invitations import (
    InvitationsService,
    InvitationBadRequest,
    InvitationConflict,
    InvitationGone,
    InvitationNotFound,
    design_role_alias_for,
)
from backend_app.identity.rbac import RequireScopes, public_route
from backend_app.identity.store import IdentityStore


class CreateInvitationRequest(BaseModel):
    email: str = Field(..., min_length=3)
    role: str = Field(..., min_length=1)
    ttl_seconds: int | None = None


class InvitationProjection(BaseModel):
    invite_id: str
    email: str
    role: str
    token_prefix: str
    created_by: dict[str, Any]
    created_at: str
    expires_at: str


class CreateInvitationResponse(InvitationProjection):
    token: str
    accept_url: str | None = None


class InvitationListResponse(BaseModel):
    invitations: list[InvitationProjection]


class AcceptInvitationResponse(BaseModel):
    invite_id: str
    org_id: str
    org_display_name: str
    user_id: str
    role: str
    accept_redirect: str


def register_invitation_routes(app: FastAPI) -> None:
    @app.post(
        "/internal/v1/workspace/invitations",
        response_model=CreateInvitationResponse,
        dependencies=[Depends(RequireScopes(ADMIN_USERS, RUNTIME_USE))],
    )
    def create_invitation(
        request: Request,
        body: CreateInvitationRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> CreateInvitationResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        service: InvitationsService = app.state.invitations_service
        try:
            mint = service.create(
                org_id=identity.org_id,
                email=body.email,
                role_name=body.role,
                created_by_user_id=identity.user_id,
                ttl_seconds=body.ttl_seconds,
            )
        except InvitationConflict as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, str(exc) or "conflict"
            ) from exc
        except InvitationBadRequest as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc) or "invalid_request"
            ) from exc
        # Look up the row we just created to reuse the projector.
        store: IdentityStore = app.state.identity_store
        creator = store.get_user(org_id=identity.org_id, user_id=identity.user_id)
        invitation = next(
            (
                r
                for r in service.list_pending(org_id=identity.org_id)
                if r.invite_id == mint.invite_id
            ),
            None,
        )
        if invitation is None:
            # Should be impossible because we just created it, but guard.
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "invitation_lookup_failed"
            )
        projection = _project(
            invitation,
            store=store,
            creator_display_name=creator.display_name if creator is not None else None,
        )
        return CreateInvitationResponse(
            **projection.model_dump(),
            token=mint.token_plaintext,
            accept_url=None,
        )

    @app.get(
        "/internal/v1/workspace/invitations",
        response_model=InvitationListResponse,
        dependencies=[Depends(RequireScopes(ADMIN_USERS, RUNTIME_USE))],
    )
    def list_invitations(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InvitationListResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        service: InvitationsService = app.state.invitations_service
        store: IdentityStore = app.state.identity_store
        pending = service.list_pending(org_id=identity.org_id)
        creators_by_id: dict[str, str | None] = {}
        for inv in pending:
            if inv.created_by_user_id not in creators_by_id:
                u = store.get_user(
                    org_id=identity.org_id, user_id=inv.created_by_user_id
                )
                creators_by_id[inv.created_by_user_id] = (
                    u.display_name if u is not None else None
                )
        return InvitationListResponse(
            invitations=[
                _project(
                    inv,
                    store=store,
                    creator_display_name=creators_by_id.get(inv.created_by_user_id),
                )
                for inv in pending
            ]
        )

    @app.delete(
        "/internal/v1/workspace/invitations/{invite_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(ADMIN_USERS, RUNTIME_USE))],
    )
    def revoke_invitation(
        request: Request,
        invite_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> None:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        service: InvitationsService = app.state.invitations_service
        ok = service.revoke(
            org_id=identity.org_id,
            invite_id=invite_id,
            actor_user_id=identity.user_id,
        )
        if not ok:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "invitation_not_found")

    @app.post(
        "/internal/v1/auth/invitations/{token}/accept",
        response_model=AcceptInvitationResponse,
        dependencies=[Depends(public_route())],
    )
    def accept_invitation(
        request: Request,
        token: str,
    ) -> AcceptInvitationResponse:
        service: InvitationsService = app.state.invitations_service
        try:
            outcome = service.accept(
                token_plaintext=token,
                request_ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        except InvitationNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except InvitationGone as exc:
            raise HTTPException(status.HTTP_410_GONE, str(exc)) from exc
        except InvitationConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except InvitationBadRequest as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        return AcceptInvitationResponse(
            invite_id=outcome.invite_id,
            org_id=outcome.org_id,
            org_display_name=outcome.org_display_name,
            user_id=outcome.user_id,
            role=design_role_alias_for(system_role_name=outcome.role_name),
            accept_redirect=f"/login?accepted_invite={outcome.invite_id}",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project(
    invitation: InvitationRecord,
    *,
    store: IdentityStore,
    creator_display_name: str | None,
) -> InvitationProjection:
    role_record = store.get_role(role_id=invitation.role_id)
    role_alias = (
        design_role_alias_for(system_role_name=role_record.name)
        if role_record is not None
        else "member"
    )
    return InvitationProjection(
        invite_id=invitation.invite_id,
        email=invitation.email,
        role=role_alias,
        token_prefix=invitation.token_prefix,
        created_by={
            "user_id": invitation.created_by_user_id,
            "display_name": creator_display_name,
        },
        created_at=_iso(invitation.created_at),
        expires_at=_iso(invitation.expires_at),
    )


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


__all__ = ["register_invitation_routes"]
