"""Internal Inbox producer endpoint — ``POST /internal/v1/inbox/items``.

Service-token-gated. ai-backend posts here via
``services/ai-backend/src/agent_runtime/api/inbox_producer.py``.

Authorization (per ``docs/atlas-new-design/destinations/inbox-prd.md`` §7.3):

1. ``ENTERPRISE_SERVICE_TOKEN`` required — header ``x-enterprise-service-token``.
2. ``x-enterprise-org-id`` + ``x-enterprise-user-id`` required.
3. The verified ``org_id`` MUST match the producer payload's ``tenant_id``
   (cross-tenant inserts → 403).
4. Recipient validation (``recipient_user_id`` belongs to that tenant) is a
   schema-level precondition; production wiring tightens this via the
   identity store lookup.

Idempotency: ``(producer_id, external_ref)`` is unique per tenant; a retry
with the same key returns the existing row instead of duplicating. The
``idempotency-key`` HTTP header is treated as the canonical external_ref
when the JSON body omits one (defensive default).

Audit: every insert writes one ``inbox.item_created`` row. ``actor_user_id``
is the service-token caller's ``x-enterprise-user-id`` (the agent's owner).

Streaming: a successful insert publishes one ``item_added`` frame on the
SSE bus (``app.state.inbox_activity_bus``) so the recipient's open
``GET /v1/inbox/stream`` connections see the new row without a poll. The
publish runs *after* the canonical :class:`InboxService` write returns,
so a rollback never leaks a phantom event (brief rule 1).

Wiring: the route resolves the canonical ``InboxService`` off
``app.state.inbox_service`` (set by ``backend_app.app.create_app``). The
service composes the canonical ``InboxStore`` + audit + bus, so the
producer surface stays presentation-only and DRY against the PATCH path.
"""

from __future__ import annotations

from typing import Any

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.inbox.service import (
    InboxInvalidRequest,
    InboxService,
    _VALID_KINDS as _CANONICAL_VALID_KINDS,
)
from backend_app.inbox.store import InboxItemRecord


# Re-export the canonical allowlist so the producer pre-check and the
# service write share one source of truth (inbox-prd §4.5 + §4.4). A
# mismatch would let the route accept a kind the service then rejects.
_VALID_KINDS = _CANONICAL_VALID_KINDS


# ---------------------------------------------------------------------------
# Wire shapes — mirrors inbox-prd §4.5 ProducerInboxItem
# ---------------------------------------------------------------------------


class CreateInternalInboxItemRequest(BaseModel):
    """``POST /internal/v1/inbox/items`` body.

    Server-trusted ``tenant_id`` is taken from the verified header; the
    request still echoes ``tenant_id`` for cross-check (mismatch → 403)
    matching §7.3 producer-authorization rule (3).
    """

    model_config = ConfigDict(extra="forbid")

    recipient_user_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)

    kind: str = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=200)
    preview: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)

    sender_agent_id: str | None = None
    sender_agent_name: str | None = None
    sender_system_origin: str | None = None

    thread_id: str | None = None
    run_id: str | None = None
    approval_id: str | None = None
    project_id: str | None = None

    priority: str | None = "med"
    labels: list[str] = Field(default_factory=list)

    producer_id: str | None = None
    external_ref: str | None = None


class CreateInternalInboxItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    recipient_user_id: str
    kind: str
    state: str
    external_ref: str | None = None
    producer_id: str | None = None
    deduped: bool = False


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def _service_for(request: Request) -> InboxService:
    """Resolve the canonical :class:`InboxService` off ``app.state``.

    The service composes the canonical ``InboxStore`` + identity store +
    activity bus — the producer route delegates to it so ACL, audit, and
    SSE-publish stay in one place (DRY with the PATCH path). Absent the
    service → 503 so misconfigured deployments fail loudly rather than
    silently dropping inbox writes.
    """
    service: InboxService | None = getattr(request.app.state, "inbox_service", None)
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "inbox service not configured",
        )
    return service


def _build_sender(payload: CreateInternalInboxItemRequest) -> dict[str, Any]:
    """Compose the JSONB ``sender`` blob from producer fields.

    Defaults to ``kind=agent`` when an agent id is supplied; otherwise to
    ``kind=system`` with the supplied origin. The internal route is
    agent/system only — a producer never claims a ``user`` sender here.
    """
    if payload.sender_agent_id:
        return {
            "kind": "agent",
            "id": payload.sender_agent_id,
            "display_name": payload.sender_agent_name or "Atlas",
        }
    if payload.sender_system_origin:
        return {"kind": "system", "origin": payload.sender_system_origin}
    return {"kind": "agent", "id": "atlas", "display_name": "Atlas"}


def _build_links(payload: CreateInternalInboxItemRequest) -> list[dict[str, Any]]:
    """Compose ``links`` from the discrete approval/run/thread/project ids.

    Mirrors §3.5's producer flow's documented link kinds — approval, chat,
    run, project — so the panel/detail UI can resolve back to the source.
    """
    links: list[dict[str, Any]] = []
    if payload.approval_id is not None:
        links.append({"kind": "approval", "id": payload.approval_id})
    if payload.thread_id is not None:
        links.append({"kind": "chat", "id": payload.thread_id})
    if payload.run_id is not None:
        links.append({"kind": "run", "id": payload.run_id})
    if payload.project_id is not None:
        links.append({"kind": "project", "id": payload.project_id})
    return links


def register_inbox_internal_routes(app: FastAPI) -> None:
    """Mount the internal producer endpoint on ``app``.

    Idempotent — re-registration is safe in tests that build multiple apps.
    """

    @app.post(
        "/internal/v1/inbox/items",
        response_model=CreateInternalInboxItemResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    async def create_inbox_item(
        payload: CreateInternalInboxItemRequest,
        request: Request,
        service: InboxService = Depends(_service_for),
    ) -> CreateInternalInboxItemResponse:
        # Service-token verification + identity headers (raises 401 / 503
        # when env is misconfigured). The ``internal_scoped_identity`` path
        # requires the headers in production, matching §7.3 rule (1) + (2).
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request,
            org_id=payload.tenant_id,
            user_id=payload.recipient_user_id,
        )

        # §7.3 rule (3): cross-tenant rejected.
        if identity.org_id != payload.tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "cross_tenant_rejected")

        # PII discipline (inbox-prd §6 + brief rule 5): the route never
        # logs subject / preview / body. The audit row captures the
        # after_state by id, not by content — content is redacted at the
        # audit-export layer by the canonical service's redaction config.

        external_ref = (
            payload.external_ref
            or request.headers.get("idempotency-key", "").strip()
            or None
        )

        if payload.kind not in _VALID_KINDS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_kind")

        # §7.4 idempotency: a retry returns the existing row.
        store = service._store  # noqa: SLF001 — same package, intentional reach.
        deduped_record: InboxItemRecord | None = None
        if external_ref is not None and payload.producer_id is not None:
            existing = _find_by_external_ref(
                store,
                tenant_id=identity.org_id,
                producer_id=payload.producer_id,
                external_ref=external_ref,
            )
            if existing is not None:
                deduped_record = existing

        if deduped_record is not None:
            # Dedupe path: no insert, no audit row, no SSE event — the
            # original ``item_added`` already fired on the first POST.
            return CreateInternalInboxItemResponse(
                id=deduped_record.id,
                tenant_id=deduped_record.tenant_id,
                recipient_user_id=deduped_record.owner_user_id,
                kind=deduped_record.kind,
                state=deduped_record.state,
                external_ref=deduped_record.external_ref,
                producer_id=deduped_record.producer_id,
                deduped=True,
            )

        sender = _build_sender(payload)
        links = _build_links(payload)

        # Delegate the durable write + audit row to the canonical
        # service. Validation errors (invalid kind / empty title) raise
        # InboxInvalidRequest → 400; service writes the
        # ``inbox.item_created`` audit row inside the same transaction
        # and returns the persisted record.
        try:
            record = service.insert_item_with_body(
                tenant_id=identity.org_id,
                owner_user_id=payload.recipient_user_id,
                kind=payload.kind,
                title=payload.subject,
                sender=sender,
                links=links,
                project_id=payload.project_id,
                body_markdown=payload.body,
                producer_id=payload.producer_id,
                external_ref=external_ref,
                actor_user_id=identity.user_id,
            )
        except InboxInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc

        # Post-commit publish. ``insert_item_with_body`` returned, so the
        # transaction committed and the audit row landed — only now is it
        # safe to stream the change out to subscribers. brief rule 1.
        await service.publish_event(record=record, event_type="item_added")

        return CreateInternalInboxItemResponse(
            id=record.id,
            tenant_id=record.tenant_id,
            recipient_user_id=record.owner_user_id,
            kind=record.kind,
            state=record.state,
            external_ref=record.external_ref,
            producer_id=record.producer_id,
            deduped=False,
        )


def _find_by_external_ref(
    store: Any,
    *,
    tenant_id: str,
    producer_id: str,
    external_ref: str,
) -> InboxItemRecord | None:
    """Idempotency lookup against the canonical store.

    The canonical Protocol doesn't define ``find_by_external_ref`` (it's
    a producer-specific concern). The in-memory adapter exposes raw
    ``items`` dict scanning; the postgres adapter will land its own
    optimised method (UNIQUE-index lookup) and this helper rewires to
    that method at merge.
    """

    items = getattr(store, "items", None)
    if not isinstance(items, dict):
        return None
    for record in items.values():
        if (
            getattr(record, "tenant_id", None) == tenant_id
            and getattr(record, "producer_id", None) == producer_id
            and getattr(record, "external_ref", None) == external_ref
        ):
            return record  # type: ignore[return-value]
    return None


__all__ = [
    "CreateInternalInboxItemRequest",
    "CreateInternalInboxItemResponse",
    "register_inbox_internal_routes",
]
