"""Public ``/v1/memory`` routes — Phase 12 P12-A3.

Routes are presentation-only; ACL + audit + state-machine invariants
live in :class:`MemoryService`. The route layer is responsible for:

1. Identity scoping via :class:`BackendServiceAuthenticator.scoped_identity`.
2. Translating service exceptions to HTTP status codes:
   * :class:`MemoryNotFound`       → 404 (cross-audit §1.3 404-not-403)
   * :class:`MemoryForbidden`      → 403
   * :class:`MemoryInvalidRequest` → 400
3. Marshalling request / response bodies to / from the wire shapes
   declared in ``packages/api-types/src/memory.ts``.
4. Parsing repeatable ``filter[<axis>]=<value>`` query params per
   cross-audit §1.5 multi-value OR semantics.

Endpoints (sub-PRD §4.2):

* ``GET    /v1/memory``                          — list
* ``GET    /v1/memory/{id}``                     — detail
* ``POST   /v1/memory``                          — create
* ``PATCH  /v1/memory/{id}``                     — update
* ``DELETE /v1/memory/{id}``                     — soft-delete
* ``POST   /internal/v1/memory/{id}/touch``      — runtime bumps last_used_at
* ``GET    /v1/memory/search``                   — hybrid BM25 + vector
* ``GET    /v1/memory/proposals``                — pending proposals
* ``POST   /v1/memory/proposals/{id}/accept``    — accept + create item
* ``POST   /v1/memory/proposals/{id}/reject``    — reject (terminal)
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.memory.search import MemorySearchEngine
from backend_app.memory.service import (
    MemoryForbidden,
    MemoryInvalidRequest,
    MemoryNotFound,
    MemoryService,
)
from backend_app.memory.store import (
    MemoryItemRecord,
    MemoryProposalRecord,
    is_valid_sort_token,
)


# ---------------------------------------------------------------------------
# Wire models (Python mirrors of packages/api-types/src/memory.ts)
# ---------------------------------------------------------------------------


class CreateMemoryRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str = "user"
    kind: str
    title: str = Field(..., min_length=1, max_length=200)
    body: str = ""
    tags: list[str] | None = None
    project_id: str | None = None


class UpdateMemoryRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str | None = None
    kind: str | None = None
    title: str | None = None
    body: str | None = None
    tags: list[str] | None = None
    project_id: str | None = None


class AcceptProposalRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_override: str | None = None
    body_override: str | None = None
    scope_override: str | None = None
    tags: list[str] | None = None
    project_id: str | None = None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_memory_routes(
    app: FastAPI,
    *,
    service: MemoryService,
    search_engine: MemorySearchEngine | None = None,
) -> None:
    """Attach ``/v1/memory/*`` routes to ``app``."""

    # ------------------------------------------------------------------
    # SEARCH — register BEFORE the catch-all ``/v1/memory/{item_id}`` so
    # ``/v1/memory/search`` is not captured as an item_id lookup. Same
    # ordering trap the Library routes documented (see
    # ``register_library_search_routes``).
    # ------------------------------------------------------------------

    if search_engine is not None:

        @app.get(
            "/v1/memory/search",
            dependencies=[Depends(RequireScopes(RUNTIME_USE))],
        )
        def memory_search(
            request: Request,
            q: str = Query(..., min_length=1, max_length=200),
            org_id: str = Query(..., min_length=1),
            user_id: str = Query(..., min_length=1),
            limit: int = Query(default=20, ge=1, le=100),
        ) -> dict[str, Any]:
            identity = BackendServiceAuthenticator.scoped_identity(
                request, org_id=org_id, user_id=user_id
            )
            start_ns = time.perf_counter_ns()
            envelope = search_engine.search(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                query=q,
                top_k=limit,
            )
            took_ms = max((time.perf_counter_ns() - start_ns) // 1_000_000, 1)
            return {
                "hits": [
                    {
                        "item": _to_wire(hit.record),
                        "score": hit.score,
                        "snippet": hit.snippet,
                    }
                    for hit in envelope.hits
                ],
                "took_ms": took_ms,
            }

    # ------------------------------------------------------------------
    # PROPOSALS — register before the item-id catch-all too.
    # ------------------------------------------------------------------

    @app.get(
        "/v1/memory/proposals",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_memory_proposals(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        statuses = _parse_repeatable_filter(request, "status") or ("pending",)
        rows, next_cursor = service.list_proposals(
            tenant_id=identity.org_id,
            caller_user_id=identity.user_id,
            statuses=statuses,
            cursor=cursor,
            limit=limit,
        )
        return {
            "proposals": [_proposal_to_wire(r) for r in rows],
            "next_cursor": next_cursor,
        }

    @app.post(
        "/v1/memory/proposals/{proposal_id}/accept",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    async def accept_memory_proposal(
        request: Request,
        proposal_id: str,
        payload: AcceptProposalRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            proposal, memory = service.accept_proposal(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                proposal_id=proposal_id,
                title_override=payload.title_override,
                body_override=payload.body_override,
                scope_override=payload.scope_override,
                tags=payload.tags,
                project_id=payload.project_id,
            )
        except MemoryNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "memory_proposal_not_found"
            ) from exc
        except MemoryInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        await service.publish_event(event_type="memory.created", record=memory)
        await service.publish_event(
            event_type="memory.proposal_decided", proposal=proposal
        )
        return {
            "proposal": _proposal_to_wire(proposal),
            "item": _to_wire(memory),
        }

    @app.post(
        "/v1/memory/proposals/{proposal_id}/reject",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    async def reject_memory_proposal(
        request: Request,
        proposal_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            proposal = service.reject_proposal(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                proposal_id=proposal_id,
            )
        except MemoryNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "memory_proposal_not_found"
            ) from exc
        except MemoryInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        await service.publish_event(
            event_type="memory.proposal_decided", proposal=proposal
        )
        return {"proposal": _proposal_to_wire(proposal)}

    # ------------------------------------------------------------------
    # ITEMS — CRUD.
    # ------------------------------------------------------------------

    @app.get(
        "/v1/memory",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_memory(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        sort: str = Query(default="last_used:desc"),
        q: str | None = Query(default=None, max_length=200),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        if not is_valid_sort_token(sort):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_sort")
        scopes = _parse_repeatable_filter(request, "scope") or None
        kinds = _parse_repeatable_filter(request, "kind") or None
        tags = _parse_repeatable_filter(request, "tag") or None
        raw_projects = _parse_repeatable_filter(request, "project_id")
        if raw_projects:
            project_filter: tuple[str | None, ...] | None = tuple(
                None if v == "unfiled" else v for v in raw_projects
            )
        else:
            project_filter = None
        try:
            rows, next_cursor = service.list_items(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                scopes=scopes,
                kinds=kinds,
                project_ids=project_filter,
                tags=tags,
                q=q,
                cursor=cursor,
                limit=limit,
                sort=sort,
            )
        except MemoryInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return {
            "items": [_to_wire(r) for r in rows],
            "next_cursor": next_cursor,
        }

    @app.get(
        "/v1/memory/{item_id}",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_memory(
        request: Request,
        item_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.get_item(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                item_id=item_id,
            )
        except MemoryNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "memory_item_not_found"
            ) from exc
        return _to_wire(record)

    @app.post(
        "/v1/memory",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    async def create_memory(
        request: Request,
        payload: CreateMemoryRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.create_item(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                creator={"kind": "user", "id": identity.user_id},
                scope=payload.scope,
                kind=payload.kind,
                title=payload.title,
                body=payload.body,
                tags=list(payload.tags or []),
                project_id=payload.project_id,
            )
        except MemoryInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        await service.publish_event(event_type="memory.created", record=record)
        return _to_wire(record)

    @app.patch(
        "/v1/memory/{item_id}",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    async def update_memory(
        request: Request,
        item_id: str,
        payload: UpdateMemoryRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        patch_dict = payload.model_dump(exclude_unset=True)
        try:
            record = service.update_item(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                item_id=item_id,
                patch=patch_dict,
            )
        except MemoryNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "memory_item_not_found"
            ) from exc
        except MemoryForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        except MemoryInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        await service.publish_event(event_type="memory.updated", record=record)
        return _to_wire(record)

    @app.delete(
        "/v1/memory/{item_id}",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    async def delete_memory(
        request: Request,
        item_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            deleted = service.delete_item(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                item_id=item_id,
            )
        except MemoryNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "memory_item_not_found"
            ) from exc
        except MemoryForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        await service.publish_event(event_type="memory.deleted", deleted_id=deleted.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # INTERNAL — runtime touch bumps ``last_used_at`` on retrieval.
    # Lives under ``/internal/v1/*`` so the facade does NOT expose it.
    # ------------------------------------------------------------------

    @app.post(
        "/internal/v1/memory/{item_id}/touch",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def touch_memory(
        request: Request,
        item_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        # ``internal_scoped_identity`` enforces the service token in
        # production but tolerates the dev fallback. The runtime is the
        # only legitimate caller here — touch is not a user-surface
        # mutation, no audit on the caller; the audit row is stamped
        # with the row owner per the service layer.
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            touched = service.touch_item(tenant_id=identity.org_id, item_id=item_id)
        except MemoryNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "memory_item_not_found"
            ) from exc
        return _to_wire(touched)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_repeatable_filter(request: Request, axis: str) -> tuple[str, ...]:
    """Extract OR-multi-value ``filter[<axis>]`` params (cross-audit §1.5)."""

    key = f"filter[{axis}]"
    return tuple(v for v in request.query_params.getlist(key) if v)


def _to_wire(record: MemoryItemRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "scope": record.scope,
        "kind": record.kind,
        "title": record.title,
        "body": record.body,
        "tags": list(record.tags),
        "created_by": dict(record.created_by),
        "last_used_at": (_iso(record.last_used_at) if record.last_used_at else None),
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
        "project_id": record.project_id,
    }


def _proposal_to_wire(record: MemoryProposalRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "tenant_id": record.tenant_id,
        "user_id": record.user_id,
        "proposed_at": _iso(record.proposed_at),
        "proposed_kind": record.proposed_kind,
        "proposed_title": record.proposed_title,
        "proposed_body": record.proposed_body,
        "source": dict(record.source),
        "status": record.status,
        "decided_at": _iso(record.decided_at) if record.decided_at else None,
    }


def _iso(value: datetime) -> str:
    return value.isoformat()


__all__ = [
    "AcceptProposalRequestModel",
    "CreateMemoryRequestModel",
    "UpdateMemoryRequestModel",
    "register_memory_routes",
]
