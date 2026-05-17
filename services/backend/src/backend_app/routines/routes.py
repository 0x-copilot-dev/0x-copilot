"""Public ``/v1/routines`` routes — Phase 5 P5-A1 CRUD + manual fire.

Routes are presentation-only; ACL + audit + state-machine + quota
invariants live in ``routines.service``. The route layer is
responsible for:

1. Identity scoping via :class:`BackendServiceAuthenticator.scoped_identity`.
2. Translating the service layer's exceptions to HTTP status codes:
   * :class:`RoutineNotFound`            → 404
   * :class:`RoutineForbidden`           → 403
   * :class:`RoutineInvalidRequest`      → 400
   * :class:`RoutineInvalidTransition`   → 409 (state machine refusal)
   * :class:`RoutineQuotaExceeded`       → 409 (active-routines cap)
3. Marshalling request / response bodies to / from the wire shapes
   declared in ``packages/api-types/src/routines.ts``.

The wire shape uses an explicit ``filter[<axis>]=<value>`` repeatable
query pattern (cross-audit §1.5, multi-value OR by default). The
helper :func:`_parse_repeatable_filter` extracts it without dropping
empty axes — matches the inbox / todos route convention.
"""

from __future__ import annotations

from typing import Any

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.routines.service import (
    RoutineForbidden,
    RoutineInvalidRequest,
    RoutineInvalidTransition,
    RoutineNotFound,
    RoutineQuotaExceeded,
    RoutinesService,
)
from backend_app.routines.store import RoutineRecord


# ---------------------------------------------------------------------------
# Request / response models (Python mirrors of api-types/src/routines.ts)
# ---------------------------------------------------------------------------


class CreateRoutineRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    instructions: str = ""
    agent_id: str
    project_id: str | None = None
    triggers: list[dict[str, Any]] = Field(default_factory=list)
    connectors_scope: dict[str, list[str]] | None = None
    behavior: dict[str, Any] | None = None
    permissions: dict[str, Any] | None = None
    agent_version_pin: str | None = None
    code: dict[str, Any] | None = None
    missed_fire_policy: str | None = None
    status: str | None = None


class UpdateRoutineRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    instructions: str | None = None
    agent_id: str | None = None
    project_id: str | None = None
    triggers: list[dict[str, Any]] | None = None
    connectors_scope: dict[str, list[str]] | None = None
    behavior: dict[str, Any] | None = None
    permissions: dict[str, Any] | None = None
    agent_version_pin: str | None = None
    code: dict[str, Any] | None = None
    missed_fire_policy: str | None = None
    status: str | None = None
    pause_reason: str | None = None


class RoutineResponseModel(BaseModel):
    """Wire mirror of ``Routine`` (packages/api-types/src/routines.ts)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    owner_user_id: str
    project_id: str | None = None
    name: str
    instructions: str
    agent_id: str
    agent_version_pin: str | None = None
    triggers: list[dict[str, Any]] = Field(default_factory=list)
    connectors_scope: dict[str, list[str]] | None = None
    behavior: dict[str, Any] | None = None
    permissions: dict[str, Any]
    code: dict[str, Any] | None = None
    status: str
    pause_reason: str | None = None
    missed_fire_policy: str
    created_at: str
    updated_at: str


class RoutineListResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[RoutineResponseModel]
    next_cursor: str | None = None


class RunRoutineResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fire_id: str
    run_id: str | None = None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_routines_routes(app: FastAPI, *, service: RoutinesService) -> None:
    """Attach ``/v1/routines`` routes to ``app``."""

    @app.get(
        "/v1/routines",
        response_model=RoutineListResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_routines(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> RoutineListResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        statuses = _parse_repeatable_filter(request, "status") or None
        raw_projects = _parse_repeatable_filter(request, "project_id")
        if raw_projects:
            # Literal "unfiled" matches the NULL project_id rows
            # (mirrors the inbox / todos convention).
            project_filter: tuple[str | None, ...] | None = tuple(
                None if v == "unfiled" else v for v in raw_projects
            )
        else:
            project_filter = None

        records, next_cursor = service.list_routines(
            tenant_id=identity.org_id,
            caller_user_id=identity.user_id,
            caller_roles=identity.roles,
            statuses=statuses,
            project_ids=project_filter,
            cursor=cursor,
            limit=limit,
        )
        return RoutineListResponseModel(
            items=[_to_wire(record) for record in records],
            next_cursor=next_cursor,
        )

    @app.get(
        "/v1/routines/{routine_id}",
        response_model=RoutineResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_routine(
        request: Request,
        routine_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> RoutineResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.get_routine(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                routine_id=routine_id,
            )
        except RoutineNotFound as exc:
            # 404-not-403 per cross-audit §1.3.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "routine_not_found") from exc
        return _to_wire(record)

    @app.post(
        "/v1/routines",
        response_model=RoutineResponseModel,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def create_routine(
        request: Request,
        payload: CreateRoutineRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> RoutineResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.create_routine(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                payload=payload.model_dump(exclude_none=True),
            )
        except RoutineQuotaExceeded as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, str(exc) or "quota_exceeded"
            ) from exc
        except RoutineInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return _to_wire(record)

    @app.patch(
        "/v1/routines/{routine_id}",
        response_model=RoutineResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def update_routine(
        request: Request,
        routine_id: str,
        payload: UpdateRoutineRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> RoutineResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        patch_dict = payload.model_dump(exclude_unset=True)
        try:
            record = service.update_routine(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                routine_id=routine_id,
                patch=patch_dict,
            )
        except RoutineNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "routine_not_found") from exc
        except RoutineForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        except RoutineInvalidTransition as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, str(exc) or "invalid_transition"
            ) from exc
        except RoutineQuotaExceeded as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, str(exc) or "quota_exceeded"
            ) from exc
        except RoutineInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return _to_wire(record)

    @app.delete(
        "/v1/routines/{routine_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def delete_routine(
        request: Request,
        routine_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            service.delete_routine(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                routine_id=routine_id,
            )
        except RoutineNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "routine_not_found") from exc
        except RoutineForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/v1/routines/{routine_id}/run",
        response_model=RunRoutineResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def run_routine(
        request: Request,
        routine_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> RunRoutineResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            fire = service.manual_fire(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                routine_id=routine_id,
            )
        except RoutineNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "routine_not_found") from exc
        except RoutineForbidden as exc:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "manual_fire_forbidden"
            ) from exc
        except RoutineInvalidTransition as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, str(exc) or "invalid_transition"
            ) from exc
        return RunRoutineResponseModel(fire_id=fire.id, run_id=fire.run_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_repeatable_filter(request: Request, axis: str) -> tuple[str, ...]:
    """Extract the OR-multi-value ``filter[<axis>]`` query params.

    cross-audit §1.5: each axis is a repeatable query parameter with OR
    semantics. ``filter[status]=active&filter[status]=paused`` →
    ``("active", "paused")``. Empty / absent axes return an empty tuple
    which the caller interprets as "no filter on this axis".
    """

    key = f"filter[{axis}]"
    return tuple(v for v in request.query_params.getlist(key) if v)


def _to_wire(record: RoutineRecord) -> RoutineResponseModel:
    """Marshal a :class:`RoutineRecord` into the wire response shape."""

    return RoutineResponseModel(
        id=record.id,
        tenant_id=record.tenant_id,
        owner_user_id=record.owner_user_id,
        project_id=record.project_id,
        name=record.name,
        instructions=record.instructions,
        agent_id=record.agent_id,
        agent_version_pin=record.agent_version_pin,
        triggers=list(record.triggers),
        connectors_scope=dict(record.connectors_scope)
        if record.connectors_scope
        else None,
        behavior=dict(record.behavior) if record.behavior else None,
        permissions=dict(record.permissions),
        code=dict(record.code) if record.code else None,
        status=record.status,
        pause_reason=record.pause_reason,
        missed_fire_policy=record.missed_fire_policy,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )


__all__ = [
    "CreateRoutineRequestModel",
    "RoutineListResponseModel",
    "RoutineResponseModel",
    "RunRoutineResponseModel",
    "UpdateRoutineRequestModel",
    "register_routines_routes",
]
