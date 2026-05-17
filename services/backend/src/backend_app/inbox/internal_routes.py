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

Parallel-wave coordination: this module uses the local store stub at
``backend_app.inbox._local_store`` until P4-A1's canonical store lands;
the orchestrator rewires the imports at merge. The route surface is
stable so callers don't need to change.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.inbox._local_store import (
    InboxAuditRecord,
    InboxBodyRecord,
    InboxItemRecord,
    InMemoryInboxStore,
)


# Producer payload's ``kind`` field is allowlisted server-side per
# inbox-prd §4.5 + §4.4. Keep this in sync with P4-A1's allowlist; the
# orchestrator merges to one source of truth.
_VALID_KINDS = frozenset(
    {
        "approval_request",
        "mention",
        "error",
        "agent_question",
        "share_invite",
        "system_announcement",
        "system",
    }
)


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


def _store_for(request: Request) -> InMemoryInboxStore:
    """Resolve the configured inbox store off ``app.state``.

    Tests + production wiring both attach the store as
    ``app.state.inbox_store``. Absent → 503 so deployments missing the
    wiring fail loudly rather than silently dropping inbox writes.
    """
    store: InMemoryInboxStore | None = getattr(request.app.state, "inbox_store", None)
    if store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "inbox store not configured",
        )
    return store


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
    )
    def create_inbox_item(
        payload: CreateInternalInboxItemRequest,
        request: Request,
        store: InMemoryInboxStore = Depends(_store_for),
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
        # audit-export layer by P4-A1's redaction config.

        external_ref = (
            payload.external_ref
            or request.headers.get("idempotency-key", "").strip()
            or None
        )

        if payload.kind not in _VALID_KINDS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_kind")

        # §7.4 idempotency: a retry returns the existing row.
        if external_ref is not None and payload.producer_id is not None:
            existing = store.find_by_external_ref(
                tenant_id=identity.org_id,
                producer_id=payload.producer_id,
                external_ref=external_ref,
            )
            if existing is not None:
                return CreateInternalInboxItemResponse(
                    id=existing.id,
                    tenant_id=existing.tenant_id,
                    recipient_user_id=existing.owner_user_id,
                    kind=existing.kind,
                    state=existing.state,
                    external_ref=existing.external_ref,
                    producer_id=existing.producer_id,
                    deduped=True,
                )

        sender = _build_sender(payload)
        links = _build_links(payload)

        with store.transaction():
            body_record = store.insert_body(
                InboxBodyRecord(
                    tenant_id=identity.org_id,
                    body_markdown=payload.body,
                )
            )
            record = store.insert_item(
                InboxItemRecord(
                    tenant_id=identity.org_id,
                    owner_user_id=payload.recipient_user_id,
                    kind=payload.kind,
                    title=payload.subject,
                    sender=sender,
                    links=links,
                    body_ref=body_record.body_ref,
                    project_id=payload.project_id,
                    producer_id=payload.producer_id,
                    external_ref=external_ref,
                )
            )
            store.append_audit(
                InboxAuditRecord(
                    tenant_id=identity.org_id,
                    # ``actor_user_id`` for producer writes is the agent's
                    # owner per inbox-prd §6.1 — the verified service-token
                    # caller's user_id header, not the recipient.
                    actor_user_id=identity.user_id,
                    action="inbox.item_created",
                    target_id=record.id,
                    after_state={
                        "id": record.id,
                        "owner_user_id": record.owner_user_id,
                        "kind": record.kind,
                        "state": record.state,
                        "producer_id": record.producer_id,
                        "external_ref": record.external_ref,
                    },
                )
            )

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


__all__ = [
    "CreateInternalInboxItemRequest",
    "CreateInternalInboxItemResponse",
    "register_inbox_internal_routes",
]
