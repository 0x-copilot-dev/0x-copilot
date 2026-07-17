"""``/v1/team/*`` — Team destination HTTP surface (sub-PRD §4.1).

Five endpoints:

* ``GET   /v1/team``                — tenant-member list with filter/sort/cursor.
* ``GET   /v1/team/{user_id}``      — tenant-member detail (admin gets ``recent_activity``).
* ``POST  /v1/team/invite``         — admin invite (delegates to InvitationsService).
* ``PATCH /v1/team/{user_id}/role`` — admin role change (self/sole-owner guards).
* ``POST  /v1/team/{user_id}/offboard`` — admin per-asset reassignment cascade.

All routes ride on the verified bearer; ``org_id`` / ``user_id`` query
params are the dev-mode fallback (cross-audit §A10).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from copilot_service_contracts.scopes import ADMIN_USERS, RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.team.service import (
    OffboardingResult,
    TeamConflict,
    TeamForbidden,
    TeamInvalidRequest,
    TeamNotFound,
    TeamService,
)
from backend_app.team.sse import TeamActivityBus
from backend_app.team.store import PersonRow, Presence, TeamRole


# ---------------------------------------------------------------------------
# Response / request models — Pydantic mirrors of api-types/team.ts.
# ---------------------------------------------------------------------------


class PersonModel(BaseModel):
    id: str
    tenant_id: str
    display_name: str
    email: str
    avatar_url: str | None = None
    role: str
    presence: str
    last_seen_at: str | None
    joined_at: str
    agents_count: int
    projects_count: int
    is_self: bool


class TeamListResponseModel(BaseModel):
    people: list[PersonModel]
    next_cursor: str | None = None


class ItemRefModel(BaseModel):
    """ItemRef mirror — narrowed via the ``kind`` discriminator at use."""

    kind: str
    id: str


class PersonActivityEntryModel(BaseModel):
    at: str
    summary: str
    target: ItemRefModel


class PersonDetailResponseModel(BaseModel):
    person: PersonModel
    agents: list[ItemRefModel]
    projects: list[ItemRefModel]
    recent_activity: list[PersonActivityEntryModel]


class InviteRequestModel(BaseModel):
    email: str = Field(..., min_length=3)
    role: Literal["owner", "admin", "member", "guest"]
    note: str | None = None


class InviteResponseModel(BaseModel):
    invite_id: str
    email: str
    role: str
    token_prefix: str
    expires_at: str


class UpdateTeamRoleRequestModel(BaseModel):
    role: Literal["owner", "admin", "member", "guest"]


class OffboardingReassignmentModel(BaseModel):
    asset: ItemRefModel
    new_owner_user_id: str


class OffboardingRequestModel(BaseModel):
    target_user_id: str
    reassignments: list[OffboardingReassignmentModel]


class OffboardingOutcomeModel(BaseModel):
    asset_kind: str
    asset_id: str
    new_owner_user_id: str
    ok: bool
    reason: str | None = None


class OffboardingResponseModel(BaseModel):
    target_user_id: str
    outcomes: list[OffboardingOutcomeModel]
    reassignments_count: int


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_team_routes(
    app: FastAPI,
    *,
    service: TeamService | None = None,
) -> None:
    """Attach ``/v1/team/*`` to a backend FastAPI app.

    ``service`` is optional — when absent, routes look up
    ``app.state.team_service`` at request time. This matches the
    inbox/projects convention so registration order in ``create_app``
    stays flexible.
    """

    def _service(request: Request) -> TeamService:
        resolved = service or getattr(request.app.state, "team_service", None)
        if resolved is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "team_service_unavailable"
            )
        return resolved

    def _bus(request: Request) -> TeamActivityBus | None:
        return getattr(request.app.state, "team_activity_bus", None)

    def _effective_roles(request: Request, identity) -> tuple[str, ...]:
        """Compose the caller's effective roles for the service layer.

        Production (service-token path): ``identity.roles`` is populated
        from the trusted upstream envelope (facade -> backend service
        headers). Dev (query-only): the envelope is empty, so we read
        the role assignments off the IdentityStore as a fallback. This
        mirrors how ``members.py`` resolves the same role — we just
        compose it into the tuple the service expects.
        """

        if identity.roles:
            return identity.roles
        store = getattr(request.app.state, "identity_store", None)
        if store is None:
            return ()
        try:
            assignments = store.list_role_assignments(
                org_id=identity.org_id, user_id=identity.user_id
            )
        except Exception:  # noqa: BLE001
            return ()
        names: list[str] = []
        for asn in assignments:
            role = store.get_role(role_id=asn.role_id)
            if role is None:
                continue
            names.append(role.name)
        return tuple(names)

    # ---- GET /v1/team -------------------------------------------------

    @app.get(
        "/v1/team",
        response_model=TeamListResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_team(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        role: str | None = Query(None, alias="filter[role]"),
        presence: str | None = Query(None, alias="filter[presence]"),
        q: str | None = Query(None, alias="filter[q]"),
        sort: str = Query("display_name:asc", alias="filter[sort]"),
        cursor: str | None = Query(None),
        limit: int = Query(50, ge=1, le=200),
    ) -> TeamListResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            people, next_cursor = _service(request).list_people(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=_effective_roles(request, identity),
                role=_coerce_role(role),
                presence=_coerce_presence(presence),
                q=q,
                sort=sort,
                cursor=cursor,
                limit=limit,
            )
        except TeamNotFound:
            # Caller is not a tenant member — return an empty list
            # rather than a 404. The list endpoint is the "what tenant
            # am I in?" lens; a 404 here breaks the FE shell.
            return TeamListResponseModel(people=[], next_cursor=None)

        return TeamListResponseModel(
            people=[
                _project_person(p, caller_user_id=identity.user_id) for p in people
            ],
            next_cursor=next_cursor,
        )

    # ---- GET /v1/team/{user_id} ---------------------------------------

    @app.get(
        "/v1/team/{target_user_id}",
        response_model=PersonDetailResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_person(
        request: Request,
        target_user_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> PersonDetailResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        svc = _service(request)
        try:
            row = svc.get_person(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=_effective_roles(request, identity),
                user_id=target_user_id,
            )
        except TeamNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "person_not_found") from exc

        # Agents + projects ItemRef projections — read from app.state
        # stores lazily so a host without the agents/projects stores
        # wired (early-bootstrap tests) still returns the person row.
        agents_refs = _agent_refs(
            request, tenant_id=identity.org_id, user_id=target_user_id
        )
        projects_refs = _project_refs(
            request, tenant_id=identity.org_id, user_id=target_user_id
        )
        caller_roles = _effective_roles(request, identity)
        recent_activity = (
            _recent_activity(request, tenant_id=identity.org_id, user_id=target_user_id)
            if any(r in {"admin", "owner"} for r in caller_roles)
            else []
        )

        return PersonDetailResponseModel(
            person=_project_person(row, caller_user_id=identity.user_id),
            agents=agents_refs,
            projects=projects_refs,
            recent_activity=recent_activity,
        )

    # ---- POST /v1/team/invite -----------------------------------------

    @app.post(
        "/v1/team/invite",
        response_model=InviteResponseModel,
        dependencies=[Depends(RequireScopes(ADMIN_USERS, RUNTIME_USE))],
    )
    def invite_team_member(
        request: Request,
        body: InviteRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InviteResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        svc = _service(request)
        try:
            mint = svc.invite(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=_effective_roles(request, identity),
                email=body.email,
                role=body.role,
                note=body.note,
            )
        except TeamForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except TeamConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except TeamInvalidRequest as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

        # ``mint`` is the ``InvitationMintResult`` (token + invite_id);
        # the projection mirrors :func:`identity.invitations.create`
        # but trims the surface to the Team wire contract.
        invitation = _find_invitation(svc, identity.org_id, mint.invite_id)
        return InviteResponseModel(
            invite_id=mint.invite_id,
            email=body.email.strip().lower(),
            role=body.role,
            token_prefix=invitation.token_prefix
            if invitation is not None
            else mint.token_plaintext[:8],
            expires_at=_iso(invitation.expires_at)
            if invitation is not None
            else _iso(datetime.now(timezone.utc)),
        )

    # ---- PATCH /v1/team/{user_id}/role --------------------------------

    @app.patch(
        "/v1/team/{target_user_id}/role",
        response_model=PersonModel,
        dependencies=[Depends(RequireScopes(ADMIN_USERS, RUNTIME_USE))],
    )
    async def patch_role(
        request: Request,
        target_user_id: str,
        body: UpdateTeamRoleRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> PersonModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        svc = _service(request)
        try:
            row = svc.update_role(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=_effective_roles(request, identity),
                target_user_id=target_user_id,
                new_role=body.role,
            )
        except TeamNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "person_not_found") from exc
        except TeamForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except TeamConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except TeamInvalidRequest as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

        person = _project_person(row, caller_user_id=identity.user_id)
        bus = _bus(request)
        if bus is not None:
            await bus.publish(
                tenant_id=identity.org_id,
                user_id=identity.user_id,
                event_type="team.role_changed",
                person=person.model_dump(),
            )
        return person

    # ---- POST /v1/team/{user_id}/offboard -----------------------------

    @app.post(
        "/v1/team/{target_user_id}/offboard",
        response_model=OffboardingResponseModel,
        dependencies=[Depends(RequireScopes(ADMIN_USERS, RUNTIME_USE))],
    )
    async def offboard(
        request: Request,
        target_user_id: str,
        body: OffboardingRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> OffboardingResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        svc = _service(request)
        # The body's target_user_id MUST match the route segment — defend
        # against a client crafting a body that targets a different row.
        if body.target_user_id != target_user_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "target_user_id_mismatch",
            )

        try:
            result: OffboardingResult = svc.offboard(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=_effective_roles(request, identity),
                target_user_id=target_user_id,
                reassignments=tuple(
                    (r.asset.kind, r.asset.id, r.new_owner_user_id)
                    for r in body.reassignments
                ),
            )
        except TeamForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except TeamNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "person_not_found") from exc

        bus = _bus(request)
        if bus is not None:
            # The post-offboard projection lets the FE patch the row
            # without a follow-up GET (sub-PRD §3.1 wire docstring).
            target_row = svc.store.get_person(
                tenant_id=identity.org_id, user_id=target_user_id
            )
            if target_row is not None:
                await bus.publish(
                    tenant_id=identity.org_id,
                    user_id=identity.user_id,
                    event_type="team.offboarded",
                    person=_project_person(
                        target_row, caller_user_id=identity.user_id
                    ).model_dump(),
                    offboarding={
                        "target_user_id": target_user_id,
                        "reassignments_count": result.reassignments_count,
                    },
                )

        return OffboardingResponseModel(
            target_user_id=result.target_user_id,
            outcomes=[
                OffboardingOutcomeModel(
                    asset_kind=o.asset_kind,
                    asset_id=o.asset_id,
                    new_owner_user_id=o.new_owner_user_id,
                    ok=o.ok,
                    reason=o.reason,
                )
                for o in result.outcomes
            ],
            reassignments_count=result.reassignments_count,
        )


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------


def _project_person(row: PersonRow, *, caller_user_id: str) -> PersonModel:
    return PersonModel(
        id=row.id,
        tenant_id=row.tenant_id,
        display_name=row.display_name,
        email=row.email,
        avatar_url=row.avatar_url,
        role=row.role,
        presence=row.presence,
        last_seen_at=_iso(row.last_seen_at) if row.last_seen_at else None,
        joined_at=_iso(row.joined_at),
        agents_count=row.agents_count,
        projects_count=row.projects_count,
        is_self=row.id == caller_user_id,
    )


def _coerce_role(value: str | None) -> TeamRole | None:
    if value is None or value == "":
        return None
    if value in {"owner", "admin", "member", "guest"}:
        return value  # type: ignore[return-value]
    return None


def _coerce_presence(value: str | None) -> Presence | None:
    if value is None or value == "":
        return None
    if value in {"active", "away", "in_meeting", "offline"}:
        return value  # type: ignore[return-value]
    return None


def _agent_refs(
    request: Request, *, tenant_id: str, user_id: str
) -> list[ItemRefModel]:
    """Build the agents-owned-by-user ItemRef list lazily."""

    store = getattr(request.app.state, "agents_store", None)
    if store is None:
        return []
    try:
        rows, _ = store.list_agents(
            tenant_id=tenant_id, owner_user_id=user_id, limit=200
        )
    except Exception:  # noqa: BLE001 — projection must never block detail
        return []
    return [ItemRefModel(kind="agent", id=getattr(r, "id", "")) for r in rows]


def _project_refs(
    request: Request, *, tenant_id: str, user_id: str
) -> list[ItemRefModel]:
    store = getattr(request.app.state, "projects_store", None)
    if store is None:
        return []
    try:
        rows, _ = store.list_projects(
            tenant_id=tenant_id, owner_user_id=user_id, limit=200
        )
    except Exception:  # noqa: BLE001
        return []
    return [ItemRefModel(kind="project", id=getattr(r, "id", "")) for r in rows]


def _recent_activity(
    request: Request, *, tenant_id: str, user_id: str
) -> list[PersonActivityEntryModel]:
    """Admin-only activity projection.

    P12-A2 ships the wire surface; the per-row join over
    ``runtime_tool_invocations`` + ``runtime_run_usage`` (sub-PRD §5.1)
    is plumbed by a follow-up wave (see destinations-master §5.9
    + Phase 8 agent_usage.py pattern). For now the field is the empty
    list so the wire shape stays locked.
    """

    _ = request, tenant_id, user_id
    return []


def _find_invitation(service: TeamService, org_id: str, invite_id: str) -> Any | None:
    try:
        rows = service.invitations_service.list_pending(org_id=org_id)
    except Exception:  # noqa: BLE001
        return None
    for row in rows:
        if row.invite_id == invite_id:
            return row
    return None


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


__all__ = ["register_team_routes"]
