"""HTTP routes for the todo-extraction proposal surface (P3-A2).

Three endpoints mounted on the ``/v1`` router:

- ``GET    /todo-extractions``               — list pending for caller
- ``POST   /todo-extractions/{id}/accept``    — accept (writes to backend)
- ``POST   /todo-extractions/{id}/reject``    — reject (state-only)

All handlers are thin shims over :class:`TodoExtractionsService`. The
service is published at ``request.app.state.todo_extractions_service``;
when absent we 503 (matches the opacity of other optional services).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from copilot_service_contracts.scopes import RUNTIME_USE

from agent_runtime.api.todo_extractions import (
    TodoExtractionApiError,
    TodoExtractionsService,
)
from agent_runtime.persistence.records import TodoExtractionRecord
from runtime_api.identity import Identity
from runtime_api.rbac import RequireScopes


class TodoExtractionResponse(BaseModel):
    """Wire shape for one proposal returned to the frontend."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    org_id: str
    owner_user_id: str
    run_id: str
    conversation_id: str
    proposed_text: str
    suggested_due: str | None = None
    suggested_project_id: str | None = None
    source_message_id: str | None = None
    confidence_score: float = Field(ge=0.0, le=1.0)
    state: str
    created_at: str
    resolved_at: str | None = None

    @classmethod
    def from_record(cls, record: TodoExtractionRecord) -> "TodoExtractionResponse":
        """Project a domain record onto the public response shape."""
        return cls(
            id=record.id,
            org_id=record.org_id,
            owner_user_id=record.owner_user_id,
            run_id=record.run_id,
            conversation_id=record.conversation_id,
            proposed_text=record.proposed_text,
            suggested_due=record.suggested_due,
            suggested_project_id=record.suggested_project_id,
            source_message_id=record.source_message_id,
            confidence_score=record.confidence_score,
            state=record.state.value,
            created_at=record.created_at.isoformat(),
            resolved_at=(
                record.resolved_at.isoformat() if record.resolved_at else None
            ),
        )


class TodoExtractionListResponse(BaseModel):
    """List wrapper response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[TodoExtractionResponse, ...] = ()


class TodoExtractionAcceptResponse(BaseModel):
    """Response from a successful accept."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    extraction_id: str
    todo: dict[str, Any]


class TodoExtractionRoutes:
    """Handlers for the three /v1/todo-extractions endpoints."""

    @classmethod
    async def list_pending(
        cls,
        request: Request,
        identity: Identity,
        limit: int | None = Query(None, ge=1, le=200),
    ) -> TodoExtractionListResponse:
        service = cls._service(request)
        records = await service.list_pending(
            org_id=identity.org_id,
            owner_user_id=identity.user_id,
            limit=limit,
        )
        return TodoExtractionListResponse(
            items=tuple(
                TodoExtractionResponse.from_record(record) for record in records
            )
        )

    @classmethod
    async def accept(
        cls,
        request: Request,
        extraction_id: str,
        identity: Identity,
    ) -> TodoExtractionAcceptResponse:
        service = cls._service(request)
        try:
            outcome = await service.accept(
                org_id=identity.org_id,
                owner_user_id=identity.user_id,
                extraction_id=extraction_id,
            )
        except TodoExtractionApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)
        return TodoExtractionAcceptResponse(
            extraction_id=outcome.extraction_id,
            todo=outcome.backend_todo,
        )

    @classmethod
    async def reject(
        cls,
        request: Request,
        extraction_id: str,
        identity: Identity,
    ) -> TodoExtractionResponse:
        service = cls._service(request)
        try:
            updated = await service.reject(
                org_id=identity.org_id,
                owner_user_id=identity.user_id,
                extraction_id=extraction_id,
            )
        except TodoExtractionApiError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)
        return TodoExtractionResponse.from_record(updated)

    @staticmethod
    def _service(request: Request) -> TodoExtractionsService:
        """Return the wired service or 503 if absent."""
        service = getattr(request.app.state, "todo_extractions_service", None)
        if service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Todo extractions service is not configured.",
            )
        return service


def register_todo_extractions_routes(router: APIRouter) -> None:
    """Attach the three /todo-extractions routes to a /v1 router."""

    router.add_api_route(
        "/todo-extractions",
        TodoExtractionRoutes.list_pending,
        methods=["GET"],
        response_model=TodoExtractionListResponse,
        name="list_todo_extractions",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    router.add_api_route(
        "/todo-extractions/{extraction_id}/accept",
        TodoExtractionRoutes.accept,
        methods=["POST"],
        response_model=TodoExtractionAcceptResponse,
        name="accept_todo_extraction",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    router.add_api_route(
        "/todo-extractions/{extraction_id}/reject",
        TodoExtractionRoutes.reject,
        methods=["POST"],
        response_model=TodoExtractionResponse,
        name="reject_todo_extraction",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
