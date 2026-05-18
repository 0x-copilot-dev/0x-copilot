"""Webhook store — Protocol + in-memory adapter.

Storage shape mirrors ``schema.sql``. Authorization is NOT enforced
here; the service layer composes :class:`WebhooksStore` with the ACL
gate. The store exposes raw queries scoped to ``tenant_id`` plus the
``claim_due_for_rotation`` helper the rotation worker calls inside its
``transaction()`` block (matches the Postgres ``FOR UPDATE SKIP
LOCKED`` semantics; the in-memory adapter mimics the contract so the
worker tests can run without Postgres).

The Webhook id is a ``trig_<ulid>`` — same brand as Phase 5 routine
trigger ids (``TriggerId`` in ``packages/api-types/src/brands.ts``).
Webhooks ARE triggers from the Routines wire shape's point of view;
this destination just owns the management UI per connectors-prd §1.2.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _webhook_id() -> str:
    # `trig_<ulid>` — ULIDs aren't natively in the stdlib; ``uuid4().hex``
    # gives us a 128-bit random id that round-trips through the
    # ``TriggerId`` brand without changing the wire shape. Production
    # postgres adapter can re-mint as a true ULID without changing this
    # interface — the prefix + length are the only consumer guarantees.
    return f"trig_{uuid4().hex}"


def _audit_id() -> str:
    return f"audwh_{uuid4().hex}"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class WebhookRecord(BaseModel):
    """One row in the ``webhooks`` table.

    Plaintext secrets NEVER live on this record. The current secret is
    fetched via :class:`backend_app.token_vault.TokenVault` using
    ``vault_ref``; the previous secret (during the 14-day rotation
    grace per connectors-prd §9.2) is at ``previous_vault_ref`` and
    expires at ``previous_expires_at``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_webhook_id)
    tenant_id: str
    owner_user_id: str
    url: str
    secret_strategy: str = "rotating"  # "rotating" | "static"
    hmac_algo: str = "hmac-sha256"
    ip_allowlist: tuple[str, ...] = ()
    status: str = "active"  # "active" | "paused"
    last_fire_at: datetime | None = None
    last_status_code: int | None = None
    routine_id: str | None = None
    vault_ref: str
    previous_vault_ref: str | None = None
    previous_expires_at: datetime | None = None
    rotates_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    deleted_at: datetime | None = None


class WebhookAuditRecord(BaseModel):
    """Append-only audit row written on every webhook state change.

    Schema mirrors :class:`backend_app.routines.store.RoutineAuditRecord`
    so the audit-chain signer reuses the same column layout (production
    adapter writes through the same chain). connectors-prd §6.2.
    """

    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(default_factory=_audit_id)
    tenant_id: str
    actor_user_id: str
    action: str
    target_kind: str = "webhook"
    target_id: str
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    correlation_id: str | None = None
    ts: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class WebhooksStore(Protocol):
    """Adapter contract for the Postgres + in-memory webhook stores."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # -- webhooks ------------------------------------------------------

    def insert_webhook(self, record: WebhookRecord) -> WebhookRecord: ...

    def get_webhook(
        self, *, tenant_id: str, webhook_id: str, include_deleted: bool = False
    ) -> WebhookRecord | None: ...

    def list_webhooks(
        self,
        *,
        tenant_id: str,
        owner_user_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[WebhookRecord, ...], str | None]: ...

    def update_webhook(self, record: WebhookRecord) -> WebhookRecord: ...

    def soft_delete_webhook(self, *, tenant_id: str, webhook_id: str) -> bool: ...

    def claim_due_for_rotation(
        self, *, now: datetime, limit: int = 50
    ) -> tuple[WebhookRecord, ...]:
        """Rotation-worker primitive.

        Postgres adapter: ``SELECT ... WHERE rotates_at <= now AND
        secret_strategy='rotating' AND status='active' AND deleted_at IS
        NULL FOR UPDATE SKIP LOCKED LIMIT N``. The skip-locked semantics
        let multiple workers run concurrently without double-rotating
        the same row. The in-memory adapter mimics the contract: rows
        returned by one call inside an open transaction are invisible
        to a concurrent call until the transaction closes.
        """

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: WebhookAuditRecord) -> WebhookAuditRecord: ...

    def list_audit_for_webhook(
        self, *, tenant_id: str, webhook_id: str
    ) -> tuple[WebhookAuditRecord, ...]: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryWebhooksStore:
    """Dict-backed adapter for tests + the default dev wiring.

    Tenant scoping is a filter on every query; soft-delete (``deleted_at``)
    hides rows from the default list / get paths but leaves them visible
    to compliance reads via ``include_deleted=True``.

    The claim-queue mimic for the rotation worker uses an in-memory
    ``_claimed`` set that is cleared on transaction exit — that's the
    only path the worker uses; tests exercise it via the public
    ``claim_due_for_rotation`` contract.
    """

    webhooks: dict[str, WebhookRecord] = field(default_factory=dict)
    audits: list[WebhookAuditRecord] = field(default_factory=list)
    _claimed: set[str] = field(default_factory=set)
    _in_transaction: int = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self._in_transaction += 1
        try:
            yield
        finally:
            self._in_transaction -= 1
            if self._in_transaction == 0:
                # Claims survive only across the transaction's lifetime —
                # mirrors `FOR UPDATE SKIP LOCKED`'s row-level locks that
                # release on commit / rollback.
                self._claimed.clear()

    # -- webhooks ------------------------------------------------------

    def insert_webhook(self, record: WebhookRecord) -> WebhookRecord:
        self.webhooks[record.id] = record
        return record

    def get_webhook(
        self, *, tenant_id: str, webhook_id: str, include_deleted: bool = False
    ) -> WebhookRecord | None:
        record = self.webhooks.get(webhook_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        if record.deleted_at is not None and not include_deleted:
            return None
        return record

    def list_webhooks(
        self,
        *,
        tenant_id: str,
        owner_user_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[WebhookRecord, ...], str | None]:
        candidates: list[WebhookRecord] = []
        for record in self.webhooks.values():
            if record.tenant_id != tenant_id:
                continue
            if record.deleted_at is not None:
                continue
            if owner_user_id is not None and record.owner_user_id != owner_user_id:
                continue
            if statuses is not None and record.status not in statuses:
                continue
            candidates.append(record)
        candidates.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        start = int(cursor) if cursor else 0
        page = candidates[start : start + limit]
        next_cursor = str(start + limit) if start + limit < len(candidates) else None
        return tuple(page), next_cursor

    def update_webhook(self, record: WebhookRecord) -> WebhookRecord:
        self.webhooks[record.id] = record
        return record

    def soft_delete_webhook(self, *, tenant_id: str, webhook_id: str) -> bool:
        record = self.webhooks.get(webhook_id)
        if record is None or record.tenant_id != tenant_id:
            return False
        if record.deleted_at is not None:
            return True
        self.webhooks[webhook_id] = record.model_copy(update={"deleted_at": _now()})
        return True

    def claim_due_for_rotation(
        self, *, now: datetime, limit: int = 50
    ) -> tuple[WebhookRecord, ...]:
        if self._in_transaction == 0:
            raise RuntimeError(
                "claim_due_for_rotation must be called inside store.transaction()"
            )
        claimed: list[WebhookRecord] = []
        for record in sorted(self.webhooks.values(), key=lambda r: r.id):
            if record.id in self._claimed:
                continue
            if record.deleted_at is not None:
                continue
            if record.status != "active":
                continue
            if record.secret_strategy != "rotating":
                continue
            if record.rotates_at is None or record.rotates_at > now:
                continue
            self._claimed.add(record.id)
            claimed.append(record)
            if len(claimed) >= limit:
                break
        return tuple(claimed)

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: WebhookAuditRecord) -> WebhookAuditRecord:
        self.audits.append(record)
        return record

    def list_audit_for_webhook(
        self, *, tenant_id: str, webhook_id: str
    ) -> tuple[WebhookAuditRecord, ...]:
        return tuple(
            r
            for r in self.audits
            if r.tenant_id == tenant_id and r.target_id == webhook_id
        )


def iter_audit_rows_for_bulk(
    records: Iterable[WebhookAuditRecord],
    *,
    correlation_id: str,
) -> Iterator[WebhookAuditRecord]:
    """Stamp ``correlation_id`` on every audit row in a bulk write."""

    for record in records:
        yield record.model_copy(update={"correlation_id": correlation_id})


__all__ = [
    "InMemoryWebhooksStore",
    "WebhookAuditRecord",
    "WebhookRecord",
    "WebhooksStore",
    "iter_audit_rows_for_bulk",
]
