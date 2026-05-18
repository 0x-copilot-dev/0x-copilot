"""Public ``/v1/todos`` routes — CRUD + bulk-action (Phase 3 P3-A1).

Routes are presentation-only; ACL + audit + subtask invariants live in
``todos.service``. The route layer is responsible for:

1. Identity scoping via :class:`BackendServiceAuthenticator.scoped_identity`.
2. Translating the service layer's exceptions to HTTP status codes
   (404 for ``TodoNotFound``, 403 for ``TodoForbidden``, 400 for
   ``TodoInvalidRequest``).
3. Marshalling request / response bodies to / from the wire shapes
   declared in ``packages/api-types/src/todos.ts``.

The wire shape uses an explicit ``filter[<axis>]=<value>`` repeatable
query pattern (cross-audit §1.5, multi-value OR by default). The
helper :func:`_parse_repeatable_filter` extracts it without dropping
empty axes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.todos.service import (
    TodoForbidden,
    TodoInvalidRequest,
    TodoNotFound,
    TodosService,
)
from backend_app.todos.store import TodoRecord


# ---------------------------------------------------------------------------
# Request / response models (Python mirrors of api-types/src/todos.ts)
# ---------------------------------------------------------------------------


class _RecurrenceWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str
    spec: str
    next_materialize_at: str | None = None
    series_id: str | None = None


class CreateTodoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=2000)
    priority: str | None = None
    due: str | None = None
    project_id: str | None = None
    parent_id: str | None = None
    recurrence: _RecurrenceWireModel | None = None


class UpdateTodoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=2000)
    status: str | None = None
    priority: str | None = None
    due: str | None = None
    project_id: str | None = None
    sort_index_within_parent: float | None = None
    recurrence: _RecurrenceWireModel | None = None


class BulkUpdateTodosRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    ids: list[str] = Field(..., min_length=1, max_length=500)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    payload: dict[str, Any] | None = None


class TodoResponseModel(BaseModel):
    """Wire mirror of ``Todo`` (packages/api-types/src/todos.ts)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    owner_user_id: str
    project_id: str | None = None
    text: str
    status: str
    priority: str
    due: str | None = None
    source: dict[str, Any]
    parent_id: str | None = None
    sort_index_within_parent: float | None = None
    recurrence: dict[str, Any] | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


class TodoListResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[TodoResponseModel]
    next_cursor: str | None = None


class BulkUpdateTodosResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    affected: int
    correlation_id: str


class MaterializeDueSeriesRequest(BaseModel):
    """Body for ``POST /internal/v1/todos/series/materialize-due``.

    ``now`` is the materializer's wall clock — an ISO-8601 instant. The
    worker (``todo_recurrence_materializer``) injects ``datetime.now(UTC)``
    on each tick; tests inject a fixed instant for deterministic claim
    behaviour.
    """

    model_config = ConfigDict(extra="forbid")

    now: str = Field(min_length=1)


class MaterializeDueSeriesResponse(BaseModel):
    """Response shape consumed by ``MaterializeOutcome`` on the worker."""

    model_config = ConfigDict(extra="forbid")

    materialized: int = Field(ge=0)
    skipped_duplicates: int = Field(ge=0)
    series_processed: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_todos_routes(app: FastAPI, *, service: TodosService) -> None:
    """Attach ``/v1/todos`` CRUD + bulk-action routes to ``app``."""

    @app.get(
        "/v1/todos",
        response_model=TodoListResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_todos(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> TodoListResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        statuses = _parse_repeatable_filter(request, "status") or None
        # Multi-value filter[project_id]: a literal "unfiled" maps to
        # ``None`` (matches NULL project_id rows).
        raw_projects = _parse_repeatable_filter(request, "project_id")
        if raw_projects:
            project_filter: tuple[str | None, ...] | None = tuple(
                None if v == "unfiled" else v for v in raw_projects
            )
        else:
            project_filter = None
        parent_filter = request.query_params.get("filter[parent_id]")
        if parent_filter == "":
            parent_filter = None

        records, next_cursor = service.list_todos(
            tenant_id=identity.org_id,
            caller_user_id=identity.user_id,
            caller_roles=identity.roles,
            statuses=statuses,
            project_ids=project_filter,
            parent_id=parent_filter,
            cursor=cursor,
            limit=limit,
        )
        return TodoListResponseModel(
            items=[_to_wire(record) for record in records],
            next_cursor=next_cursor,
        )

    @app.post(
        "/v1/todos",
        response_model=TodoResponseModel,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def create_todo(
        request: Request,
        payload: CreateTodoRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> TodoResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.create_todo(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                text=payload.text,
                priority=payload.priority or "med",
                due=payload.due,
                project_id=payload.project_id,
                parent_id=payload.parent_id,
                recurrence=payload.recurrence.model_dump()
                if payload.recurrence
                else None,
            )
        except TodoInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return _to_wire(record)

    @app.patch(
        "/v1/todos/{todo_id}",
        response_model=TodoResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def update_todo(
        request: Request,
        todo_id: str,
        payload: UpdateTodoRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> TodoResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        patch_dict = payload.model_dump(exclude_unset=True)
        # Translate the ``recurrence`` nested model back to a plain dict
        # for the service layer (so it round-trips through the JSONB
        # column unchanged).
        if "recurrence" in patch_dict and patch_dict["recurrence"] is not None:
            patch_dict["recurrence"] = payload.recurrence.model_dump()  # type: ignore[union-attr]
        try:
            record = service.update_todo(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                todo_id=todo_id,
                patch=patch_dict,
            )
        except TodoNotFound as exc:
            # 404-not-403 per cross-audit §1.3 — same response for
            # missing or unreadable.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "todo_not_found") from exc
        except TodoForbidden as exc:
            # Read-but-not-write (project member, admin): explicit 403.
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        except TodoInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return _to_wire(record)

    @app.delete(
        "/v1/todos/{todo_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def delete_todo(
        request: Request,
        todo_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            service.delete_todo(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                todo_id=todo_id,
            )
        except TodoNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "todo_not_found") from exc
        except TodoForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/v1/todos/bulk",
        response_model=BulkUpdateTodosResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def bulk_update_todos(
        request: Request,
        payload: BulkUpdateTodosRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> BulkUpdateTodosResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            affected = service.bulk_update(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                action=payload.action,
                ids=tuple(payload.ids),
                correlation_id=payload.correlation_id,
                payload=payload.payload,
            )
        except TodoInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return BulkUpdateTodosResponseModel(
            affected=affected, correlation_id=payload.correlation_id
        )

    @app.post(
        "/internal/v1/todos/series/materialize-due",
        response_model=MaterializeDueSeriesResponse,
    )
    def materialize_due_series(
        request: Request,
        payload: MaterializeDueSeriesRequest,
    ) -> MaterializeDueSeriesResponse:
        """System-level recurrence materialization tick.

        Called only by the ai-backend ``todo_recurrence_materializer``
        worker. Service-token gated (``internal_scoped_identity`` raises
        401 in production without ``x-enterprise-service-token`` +
        ``x-enterprise-org-id`` + ``x-enterprise-user-id``). The verified
        identity is recorded for audit ("actor=system" producer), but
        materialised rows derive ``tenant_id`` + ``owner_user_id`` from
        the per-series record — caller-supplied identity cannot influence
        which tenant gets a new Todo.
        """

        # Gate: service-token + identity headers required. We do NOT
        # use this org/user to pick a tenant — the rows are written
        # under each series's stored tenant_id (see service comments).
        BackendServiceAuthenticator.internal_scoped_identity(
            request,
            org_id="system",
            user_id="system",
        )

        try:
            now = _parse_iso_instant(payload.now)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_now_iso") from exc

        outcome = service.materialize_due_series(now=now)
        return MaterializeDueSeriesResponse(
            materialized=outcome.materialized,
            skipped_duplicates=outcome.skipped_duplicates,
            series_processed=outcome.series_processed,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_iso_instant(raw: str) -> datetime:
    """Parse an ISO-8601 instant, coercing naive to UTC.

    Accepts both ``...Z`` and ``+00:00`` zone suffixes. Naive datetimes
    are treated as UTC (the worker always sends UTC; this keeps
    deterministic-test setups simple). Raises ``ValueError`` on any
    other shape so callers can surface 400.
    """

    cleaned = raw.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_repeatable_filter(request: Request, axis: str) -> tuple[str, ...]:
    """Extract the OR-multi-value ``filter[<axis>]`` query params.

    cross-audit §1.5: each axis is a repeatable query parameter with OR
    semantics. ``filter[status]=open&filter[status]=done`` →
    ``("open", "done")``. Empty / absent axes return an empty tuple
    which the caller interprets as "no filter on this axis".
    """

    key = f"filter[{axis}]"
    return tuple(v for v in request.query_params.getlist(key) if v)


def _to_wire(record: TodoRecord) -> TodoResponseModel:
    """Marshal a :class:`TodoRecord` into the wire response shape."""

    return TodoResponseModel(
        id=record.id,
        tenant_id=record.tenant_id,
        owner_user_id=record.owner_user_id,
        project_id=record.project_id,
        text=record.text,
        status=record.status,
        priority=record.priority,
        due=record.due,
        source=record.source,
        parent_id=record.parent_id,
        sort_index_within_parent=record.sort_index_within_parent,
        recurrence=record.recurrence,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
        completed_at=(record.completed_at.isoformat() if record.completed_at else None),
    )


__all__ = [
    "BulkUpdateTodosRequest",
    "BulkUpdateTodosResponseModel",
    "CreateTodoRequest",
    "MaterializeDueSeriesRequest",
    "MaterializeDueSeriesResponse",
    "TodoListResponseModel",
    "TodoResponseModel",
    "UpdateTodoRequest",
    "register_todos_routes",
]
