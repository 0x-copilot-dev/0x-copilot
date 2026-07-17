"""Public ``/v1/inbox`` routes — Phase 4 P4-A1 CRUD + bulk + unread-count.

Routes are presentation-only; ACL + audit + state-machine invariants
live in ``inbox.service``. The route layer is responsible for:

1. Identity scoping via :class:`BackendServiceAuthenticator.scoped_identity`.
2. Translating the service layer's exceptions to HTTP status codes
   (404 for ``InboxNotFound``, 403 for ``InboxForbidden``, 400 for
   ``InboxInvalidRequest``).
3. Marshalling request / response bodies to / from the wire shapes
   declared in ``packages/api-types/src/inbox.ts``.

The wire shape uses an explicit ``filter[<axis>]=<value>`` repeatable
query pattern (cross-audit §1.5, multi-value OR by default). The
helper :func:`_parse_repeatable_filter` extracts it without dropping
empty axes — matches the todos route convention.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.inbox.service import (
    InboxForbidden,
    InboxInvalidRequest,
    InboxNotFound,
    InboxService,
)
from backend_app.inbox.store import InboxItemRecord


# ---------------------------------------------------------------------------
# Request / response models (Python mirrors of api-types/src/inbox.ts)
# ---------------------------------------------------------------------------


class UpdateInboxItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str | None = None
    snoozed_until: str | None = None


class BulkUpdateInboxItemsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    ids: list[str] = Field(..., min_length=1, max_length=500)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    payload: dict[str, Any] | None = None


class InboxItemResponseModel(BaseModel):
    """Wire mirror of ``InboxItem`` (packages/api-types/src/inbox.ts)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    owner_user_id: str
    project_id: str | None = None
    kind: str
    title: str
    body_ref: str | None = None
    links: list[dict[str, Any]] = Field(default_factory=list)
    sender: dict[str, Any] = Field(default_factory=dict)
    state: str
    received_at: str
    read_at: str | None = None
    snoozed_until: str | None = None
    dismissed_at: str | None = None


class InboxItemDetailResponseModel(InboxItemResponseModel):
    """``GET /v1/inbox/{id}`` — item + lazy-loaded body markdown."""

    model_config = ConfigDict(extra="forbid")

    body_markdown: str | None = None


class InboxListResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[InboxItemResponseModel]
    next_cursor: str | None = None
    unread_count: int


class BulkUpdateInboxItemsResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    affected: int
    correlation_id: str


class InboxUnreadCountResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unread_count: int
    as_of: str


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_inbox_routes(app: FastAPI, *, service: InboxService) -> None:
    """Attach ``/v1/inbox`` routes to ``app``."""

    @app.get(
        "/v1/inbox",
        response_model=InboxListResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_inbox(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> InboxListResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        states = _parse_repeatable_filter(request, "state") or None
        kinds = _parse_repeatable_filter(request, "kind") or None
        raw_projects = _parse_repeatable_filter(request, "project_id")
        if raw_projects:
            # Literal "unfiled" matches the NULL project_id rows
            # (mirrors the todos convention).
            project_filter: tuple[str | None, ...] | None = tuple(
                None if v == "unfiled" else v for v in raw_projects
            )
        else:
            project_filter = None

        records, next_cursor, unread = service.list_items(
            tenant_id=identity.org_id,
            caller_user_id=identity.user_id,
            caller_roles=identity.roles,
            states=states,
            kinds=kinds,
            project_ids=project_filter,
            cursor=cursor,
            limit=limit,
        )
        return InboxListResponseModel(
            items=[_to_wire(record) for record in records],
            next_cursor=next_cursor,
            unread_count=unread,
        )

    @app.get(
        "/v1/inbox/unread_count",
        response_model=InboxUnreadCountResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def unread_count(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InboxUnreadCountResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        count = service.count_unread(
            tenant_id=identity.org_id, caller_user_id=identity.user_id
        )
        return InboxUnreadCountResponseModel(
            unread_count=count,
            as_of=datetime.now(timezone.utc).isoformat(),
        )

    @app.get(
        "/v1/inbox/{item_id}",
        response_model=InboxItemDetailResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_inbox_item(
        request: Request,
        item_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InboxItemDetailResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record, body = service.get_body_markdown(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                item_id=item_id,
            )
        except InboxNotFound as exc:
            # 404-not-403 per cross-audit §1.3 — same response for
            # missing or unreadable.
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "inbox_item_not_found"
            ) from exc
        wire = _to_wire(record)
        return InboxItemDetailResponseModel(
            **wire.model_dump(),
            body_markdown=body,
        )

    @app.patch(
        "/v1/inbox/{item_id}",
        response_model=InboxItemResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    async def update_inbox_item(
        request: Request,
        item_id: str,
        payload: UpdateInboxItemRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InboxItemResponseModel:
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
        except InboxNotFound as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "inbox_item_not_found"
            ) from exc
        except InboxForbidden as exc:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "recipient_only_writes"
            ) from exc
        except InboxInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        # Post-commit publish on the SSE bus. ``update_item`` returned the
        # canonical record, which means ``with store.transaction():``
        # exited cleanly *and* the audit row landed — only now is it safe
        # to stream the change out to subscribers. The bus.publish is a
        # no-op when the destination is wired without the SSE adapter
        # (tests, dev configurations that disable streaming).
        await service.publish_event(record=record, event_type="item_updated")
        return _to_wire(record)

    @app.post(
        "/v1/inbox/bulk",
        response_model=BulkUpdateInboxItemsResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    async def bulk_update_inbox(
        request: Request,
        payload: BulkUpdateInboxItemsRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> BulkUpdateInboxItemsResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            affected, updated = service.bulk_update(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                action=payload.action,
                ids=tuple(payload.ids),
                correlation_id=payload.correlation_id,
                payload=payload.payload,
            )
        except InboxInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        # One ``item_updated`` per mutated row — post-commit, audit row
        # already landed (the single-row ``update_item`` did the
        # transaction). Same tenant-scoped channel discipline as the PATCH
        # path. We publish serially rather than via ``asyncio.gather`` so
        # the per-channel sequence_no is deterministic (the bus increments
        # the cursor on each await).
        for record in updated:
            await service.publish_event(record=record, event_type="item_updated")
        return BulkUpdateInboxItemsResponseModel(
            affected=affected, correlation_id=payload.correlation_id
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_repeatable_filter(request: Request, axis: str) -> tuple[str, ...]:
    """Extract the OR-multi-value ``filter[<axis>]`` query params.

    cross-audit §1.5: each axis is a repeatable query parameter with OR
    semantics. ``filter[state]=unread&filter[state]=snoozed`` →
    ``("unread", "snoozed")``. Empty / absent axes return an empty tuple
    which the caller interprets as "no filter on this axis".
    """

    key = f"filter[{axis}]"
    return tuple(v for v in request.query_params.getlist(key) if v)


def _to_wire(record: InboxItemRecord) -> InboxItemResponseModel:
    """Marshal an :class:`InboxItemRecord` into the wire response shape."""

    return InboxItemResponseModel(
        id=record.id,
        tenant_id=record.tenant_id,
        owner_user_id=record.owner_user_id,
        project_id=record.project_id,
        kind=record.kind,
        title=record.title,
        body_ref=record.body_ref,
        links=list(record.links),
        sender=dict(record.sender),
        state=record.state,
        received_at=record.received_at.isoformat(),
        read_at=record.read_at.isoformat() if record.read_at else None,
        snoozed_until=(
            record.snoozed_until.isoformat() if record.snoozed_until else None
        ),
        dismissed_at=(record.dismissed_at.isoformat() if record.dismissed_at else None),
    )


__all__ = [
    "BulkUpdateInboxItemsRequest",
    "BulkUpdateInboxItemsResponseModel",
    "InboxItemDetailResponseModel",
    "InboxItemResponseModel",
    "InboxListResponseModel",
    "InboxUnreadCountResponseModel",
    "UpdateInboxItemRequest",
    "register_inbox_routes",
]
