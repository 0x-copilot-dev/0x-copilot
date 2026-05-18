"""Public ``/v1/agents`` routes — Phase 8 P8-A1 CRUD + ACL.

Routes are presentation-only; ACL + audit + state-machine + invariants
live in :class:`AgentsService`. The route layer is responsible for:

1. Identity scoping via :class:`BackendServiceAuthenticator.scoped_identity`.
2. Translating the service layer's exceptions to HTTP status codes:
   * :class:`AgentNotFound`        → 404 (cross-audit §1.3: 404-not-403)
   * :class:`AgentForbidden`       → 403
   * :class:`AgentInvalidRequest`  → 400
   * :class:`AgentConflict`        → 409 (slug duplication / origin-immutable)
3. Marshalling request / response bodies to / from the wire shapes
   declared in ``packages/api-types/src/agents.ts``.
4. Enforcing the ``filter[owner_user_id]`` admin-only guard (parallel to
   projects-prd §4.4 — prevents harvesting other users' custom agents).

P8-A1 ships the CRUD surface. Operational endpoints — install / uninstall
(POST + DELETE), version snapshot (POST /versions), duplicate (POST
/duplicate), and usage projection (GET /usage) — land in P8-A2 / P8-A3 /
P8-A4 and slot in alongside this file via additional `@app.<verb>`
decorators registered by the same `register_agents_routes` function.
"""

from __future__ import annotations

from typing import Any

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict

from backend_app.agents.service import (
    AgentConflict,
    AgentForbidden,
    AgentInvalidRequest,
    AgentNotFound,
    AgentsService,
)
from backend_app.agents.store import AgentInstallRecord, AgentRecord
from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes


# ---------------------------------------------------------------------------
# Request / response models (Python mirrors of api-types/src/agents.ts)
# ---------------------------------------------------------------------------


class AgentModelDefaultModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str
    reasoning_depth: str


class AgentPermissionsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    autonomy: str
    max_tool_calls_per_run: int = 20
    max_output_tokens: int = 8000
    read_only: bool = False
    allowed_skill_ids: list[str] | None = None
    blocked_tool_families: list[str] | None = None


class CreateAgentRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    slug: str | None = None
    description: str | None = None
    icon_emoji: str | None = None
    color_hue: int | None = None
    instructions: str | None = None
    model_default: AgentModelDefaultModel | None = None
    connectors_default: list[str] | None = None
    skills: list[str] | None = None
    permissions: AgentPermissionsModel | None = None
    memory_ref: dict[str, Any] | None = None


class UpdateAgentRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    icon_emoji: str | None = None
    color_hue: int | None = None
    instructions: str | None = None
    model_default: AgentModelDefaultModel | None = None
    connectors_default: list[str] | None = None
    skills: list[str] | None = None
    permissions: AgentPermissionsModel | None = None
    memory_ref: dict[str, Any] | None = None
    status: str | None = None


class AgentResponseModel(BaseModel):
    """Wire mirror of ``Agent`` (packages/api-types/src/agents.ts)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    name: str
    slug: str
    description: str
    icon_emoji: str
    color_hue: int
    version: int
    status: str
    origin: str
    owner_user_id: str | None = None
    instructions: str
    model_default: AgentModelDefaultModel
    connectors_default: list[str]
    skills: list[str]
    permissions: dict[str, Any]
    forked_from_agent_id: str | None = None
    memory_ref: dict[str, Any] | None = None
    created_at: str
    updated_at: str
    viewer_install_status: str


class AgentListResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[AgentResponseModel]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_agents_routes(
    app: FastAPI,
    *,
    service: AgentsService,
) -> None:
    """Attach ``/v1/agents`` CRUD routes to ``app``.

    P8-A1 ships:

    * ``GET    /v1/agents``        — list (filter[origin/status/skill_id/
      connector_id/owner_user_id], q, sort, cursor pagination)
    * ``GET    /v1/agents/{id}``   — detail (merged-overrides view)
    * ``POST   /v1/agents``        — create custom agent
    * ``PATCH  /v1/agents/{id}``   — owner-only edit on live record
    * ``DELETE /v1/agents/{id}``   — owner-only soft-delete

    Operational endpoints (install / uninstall / versions / duplicate /
    usage / stream) land in P8-A2 / P8-A3 / P8-A4 / P8-A5 and call
    ``register_<sub>_routes`` against the same app instance.
    """

    @app.get(
        "/v1/agents",
        response_model=AgentListResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_agents(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        q: str | None = Query(default=None, max_length=200),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        sort: str = Query(default="updated_at:desc"),
    ) -> AgentListResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        origins = _parse_repeatable_filter(request, "origin") or None
        statuses = _parse_repeatable_filter(request, "status") or None
        skill_ids = _parse_repeatable_filter(request, "skill_id") or None
        connector_ids = _parse_repeatable_filter(request, "connector_id") or None
        owner_filter = _parse_repeatable_filter(request, "owner_user_id")

        # Allowlist enforcement — any unknown ``filter[<axis>]`` axis is
        # rejected up front (cross-audit §1.5). The check is done against
        # the full query string so a typo doesn't silently no-op.
        _enforce_filter_allowlist(request)

        # Enum validation — bail early on a bad value rather than letting
        # the store's empty-result-set hide the typo.
        if origins is not None:
            for o in origins:
                if o not in {"system", "community", "custom"}:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, "filter_origin_invalid"
                    )
        if statuses is not None:
            for s in statuses:
                if s not in {"installed", "available", "disabled", "draft"}:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, "filter_status_invalid"
                    )
        if sort not in {
            "updated_at:desc",
            "updated_at:asc",
            "name:asc",
            "name:desc",
            "created_at:desc",
            "created_at:asc",
        }:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "sort_invalid")

        # owner_user_id filter — admin-only (membership-graph harvesting
        # protection; same shape as projects-prd §4.4). Non-admin caller
        # may still filter to themselves (or "me").
        admin = any(role in {"admin", "owner"} for role in identity.roles)
        scoped_owner_user_id: str | None = None
        if owner_filter:
            target = owner_filter[0]
            if target == "me":
                target = identity.user_id
            if not admin and target != identity.user_id:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "filter_owner_user_id_admin_only",
                )
            scoped_owner_user_id = target

        enriched, next_cursor = service.list_agents(
            tenant_id=identity.org_id,
            caller_user_id=identity.user_id,
            caller_roles=identity.roles,
            origins=origins,
            statuses=statuses,
            skill_ids=skill_ids,
            connector_ids=connector_ids,
            owner_user_id=scoped_owner_user_id,
            q=q,
            cursor=cursor,
            limit=limit,
            sort=sort,
        )

        items = [
            _to_wire(record, install, identity.user_id, service)
            for (record, install) in enriched
        ]
        return AgentListResponseModel(items=items, next_cursor=next_cursor)

    @app.get(
        "/v1/agents/{agent_id}",
        response_model=AgentResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_agent(
        request: Request,
        agent_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> AgentResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record, install = service.get_agent(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                agent_id=agent_id,
            )
        except AgentNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent_not_found") from exc
        return _to_wire(record, install, identity.user_id, service)

    @app.post(
        "/v1/agents",
        response_model=AgentResponseModel,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def create_agent(
        request: Request,
        payload: CreateAgentRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> AgentResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.create_custom_agent(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                payload=payload.model_dump(exclude_none=True),
            )
        except AgentConflict as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, exc.code or "conflict"
            ) from exc
        except AgentInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        # Fresh agent — install hasn't been created (status='draft').
        return _to_wire(record, None, identity.user_id, service)

    @app.patch(
        "/v1/agents/{agent_id}",
        response_model=AgentResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def update_agent(
        request: Request,
        agent_id: str,
        payload: UpdateAgentRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> AgentResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        patch_dict = payload.model_dump(exclude_unset=True)
        try:
            record = service.update_agent(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                agent_id=agent_id,
                patch=patch_dict,
            )
        except AgentNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent_not_found") from exc
        except AgentForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        except AgentConflict as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, exc.code or "conflict"
            ) from exc
        except AgentInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        install = service._store.get_install(  # noqa: SLF001
            tenant_id=identity.org_id,
            agent_id=record.id,
            user_id=identity.user_id,
        )
        return _to_wire(record, install, identity.user_id, service)

    @app.delete(
        "/v1/agents/{agent_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def delete_agent(
        request: Request,
        agent_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            service.delete_agent(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                agent_id=agent_id,
            )
        except AgentNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "agent_not_found") from exc
        except AgentForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        except AgentConflict as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, exc.code or "conflict"
            ) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ALLOWED_FILTER_AXES = frozenset(
    {"origin", "status", "skill_id", "connector_id", "owner_user_id"}
)


def _parse_repeatable_filter(request: Request, axis: str) -> tuple[str, ...]:
    """Extract the OR-multi-value ``filter[<axis>]`` query params."""

    key = f"filter[{axis}]"
    return tuple(v for v in request.query_params.getlist(key) if v)


def _enforce_filter_allowlist(request: Request) -> None:
    """Reject any ``filter[<axis>]`` whose axis isn't in the allowlist.

    Cross-audit §1.5 binding: unknown filter axes must 400 with a clear
    error code so typos don't silently no-op into "list everything".
    """

    for key in request.query_params.keys():
        if not key.startswith("filter["):
            continue
        if not key.endswith("]"):
            continue
        axis = key[len("filter[") : -1]
        if axis not in _ALLOWED_FILTER_AXES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                {"error": "filter_not_allowed", "axis": axis},
            )


def _to_wire(
    record: AgentRecord,
    install: AgentInstallRecord | None,
    caller_user_id: str,
    service: AgentsService,
) -> AgentResponseModel:
    """Marshal an :class:`AgentRecord` into the wire response shape.

    The ``viewer_install_status`` field is computed caller-relative; the
    underlying ``record.status`` is the row's own state and may differ
    when the caller hasn't installed the agent.
    """

    viewer_status = service.viewer_install_status_for(record, install, caller_user_id)
    model_default = AgentModelDefaultModel(
        model_id=record.model_id, reasoning_depth=record.reasoning_depth
    )
    return AgentResponseModel(
        id=record.id,
        tenant_id=record.tenant_id,
        name=record.name,
        slug=record.slug,
        description=record.description,
        icon_emoji=record.icon_emoji,
        color_hue=record.color_hue,
        version=record.version,
        status=record.status,
        origin=record.origin,
        owner_user_id=record.owner_user_id,
        instructions=record.instructions,
        model_default=model_default,
        connectors_default=list(record.connectors_default),
        skills=list(record.skills),
        permissions=dict(record.permissions),
        forked_from_agent_id=record.forked_from_agent_id,
        memory_ref=record.memory_ref,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
        viewer_install_status=viewer_status,
    )


__all__ = [
    "AgentListResponseModel",
    "AgentModelDefaultModel",
    "AgentPermissionsModel",
    "AgentResponseModel",
    "CreateAgentRequestModel",
    "UpdateAgentRequestModel",
    "register_agents_routes",
]
