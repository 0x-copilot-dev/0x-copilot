"""Webhook service — ACL + copy-once secret reveal + audit.

connectors-prd §4.10 + §9. The service composes:

* :class:`backend_app.webhooks.store.WebhooksStore` for the row write
  path.
* :class:`backend_app.token_vault.TokenVault` for plaintext storage at
  rest (Routines uses the same vault adapter — DRY, no parallel secret
  store).
* The audit append always runs INSIDE the store's ``transaction()``
  block so a partial failure rolls back both rows (C3 atomicity rule;
  matches the routines / inbox / projects services).

Copy-once reveal:

The plaintext secret is returned EXACTLY ONCE — on create and on
rotate. Subsequent reads via ``get_webhook`` return the row without
the plaintext (callers see ``vault_ref`` only). The wizard pattern
matches Phase 5 Routines' `RevealOnce` component on the frontend.

Rotation grace:

When ``secret_strategy == "rotating"``, the rotation worker (and the
manual ``rotate`` path) preserves the OLD ciphertext in
``previous_vault_ref`` with a 14-day expiry per connectors-prd §9.2.
The receiver must accept either the new or the grace secret during
that window so deployments can roll the receiver-side secret without
a hard cutover.

Disconnect / delete:

``delete_webhook`` cascades: soft-deletes the row and emits a
``webhook.deleted`` audit row. Routines that referenced the webhook
are NOT mutated here (the routines service owns that read-side
projection); the routine detail page renders the ``errored`` state
when the referenced webhook is no longer ``active`` per
connectors-prd §4.10.
"""

from __future__ import annotations

import ipaddress
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from backend_app.token_vault import TokenVault
from backend_app.webhooks.store import (
    WebhookAuditRecord,
    WebhookRecord,
    WebhooksStore,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: Rotation cadence per connectors-prd §9.2. The rotation worker
#: advances ``rotates_at`` by this value after every successful
#: rotation.
ROTATION_INTERVAL = timedelta(days=90)

#: How long the previous secret remains accepted after rotation. Per
#: connectors-prd §9.2 — gives receivers a deploy window.
ROTATION_GRACE = timedelta(days=14)

#: Plaintext secret entropy. 32 raw bytes → 64-char base64url; matches
#: the Routines webhook secret strength so both ingress + egress
#: surfaces share one "no birthday paradox" floor (HMAC-SHA256 needs
#: ≥128-bit keys).
_SECRET_BYTES = 32


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Errors (mirror the routines / projects service error taxonomy)
# ---------------------------------------------------------------------------


class WebhookNotFound(LookupError):
    """The webhook does not exist OR the caller lacks read rights.

    404-not-403 rule (cross-audit §1.3): non-readers see "not found" so
    cross-tenant existence isn't leaked.
    """

    def __init__(self, webhook_id: str) -> None:
        super().__init__(webhook_id)
        self.webhook_id = webhook_id


class WebhookForbidden(PermissionError):
    """Read rights established but the caller cannot write."""


class WebhookInvalidRequest(ValueError):
    """Wire-shape validation failure (bad URL, invalid CIDR, etc.)."""


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WebhookCreated:
    """Returned by :meth:`WebhooksService.create_webhook`.

    The plaintext secret is included EXACTLY ONCE. Callers (the route)
    must hand it to the wizard's copy-once-reveal step and discard it
    immediately — never log, never persist outside the response body.
    """

    record: WebhookRecord
    secret_plaintext: str


@dataclass(frozen=True)
class WebhookRotated:
    """Returned by :meth:`WebhooksService.rotate_webhook`.

    The new plaintext is included exactly once; the row now has both
    ``vault_ref`` (the new ciphertext) and ``previous_vault_ref`` (the
    pre-rotation ciphertext, valid through ``previous_expires_at``).
    """

    record: WebhookRecord
    secret_plaintext: str
    grace_secret_plaintext: str | None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class WebhooksService:
    """Composition of the webhook store + token vault with ACL + audit."""

    def __init__(
        self,
        *,
        store: WebhooksStore,
        token_vault: TokenVault,
        rotation_interval: timedelta = ROTATION_INTERVAL,
        rotation_grace: timedelta = ROTATION_GRACE,
    ) -> None:
        self._store = store
        self._vault = token_vault
        self._rotation_interval = rotation_interval
        self._rotation_grace = rotation_grace

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_webhooks(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        statuses: tuple[str, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[WebhookRecord, ...], str | None]:
        """List webhooks the caller can see.

        Admins see all tenant rows; non-admins see only rows they own
        (connectors-prd §6.1 — webhooks are tenant-admin OR routine-owner).
        """

        if self._is_admin(caller_roles):
            return self._store.list_webhooks(
                tenant_id=tenant_id,
                statuses=statuses,
                cursor=cursor,
                limit=limit,
            )
        return self._store.list_webhooks(
            tenant_id=tenant_id,
            owner_user_id=caller_user_id,
            statuses=statuses,
            cursor=cursor,
            limit=limit,
        )

    def get_webhook(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        webhook_id: str,
    ) -> WebhookRecord:
        record = self._store.get_webhook(tenant_id=tenant_id, webhook_id=webhook_id)
        if record is None or not self._can_read(record, caller_user_id, caller_roles):
            raise WebhookNotFound(webhook_id)
        return record

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def create_webhook(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        url: str,
        secret_strategy: str = "rotating",
        ip_allowlist: tuple[str, ...] = (),
        routine_id: str | None = None,
        static_secret: str | None = None,
    ) -> WebhookCreated:
        """Create a webhook + mint the initial secret.

        For ``secret_strategy="static"`` the caller MUST provide
        ``static_secret`` (the user is responsible for it; Atlas never
        rotates static secrets). For ``rotating``, Atlas generates a
        fresh secret and schedules ``rotates_at`` 90 days out.
        """

        self._validate_url(url)
        self._validate_cidrs(ip_allowlist)
        self._validate_strategy(secret_strategy)

        if secret_strategy == "static":
            if not static_secret:
                raise WebhookInvalidRequest("static_secret_required")
            plaintext = static_secret
            rotates_at: datetime | None = None
        else:
            plaintext = _mint_secret()
            rotates_at = _now() + self._rotation_interval

        vault_ref = self._vault.encrypt(plaintext)
        record = WebhookRecord(
            tenant_id=tenant_id,
            owner_user_id=caller_user_id,
            url=url,
            secret_strategy=secret_strategy,
            ip_allowlist=tuple(ip_allowlist),
            routine_id=routine_id,
            vault_ref=vault_ref,
            rotates_at=rotates_at,
        )
        with self._store.transaction():
            stored = self._store.insert_webhook(record)
            self._store.append_audit(
                WebhookAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="webhook.created",
                    target_id=stored.id,
                    after_state=_safe_dump(stored),
                )
            )
        return WebhookCreated(record=stored, secret_plaintext=plaintext)

    def update_webhook(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        webhook_id: str,
        url: str | None = None,
        ip_allowlist: tuple[str, ...] | None = None,
        status: str | None = None,
    ) -> WebhookRecord:
        existing = self._authorize_write(
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
            webhook_id=webhook_id,
        )
        updates: dict[str, Any] = {}
        if url is not None:
            self._validate_url(url)
            updates["url"] = url
        if ip_allowlist is not None:
            self._validate_cidrs(ip_allowlist)
            updates["ip_allowlist"] = tuple(ip_allowlist)
        if status is not None:
            if status not in ("active", "paused"):
                raise WebhookInvalidRequest(f"invalid_status:{status}")
            updates["status"] = status
        if not updates:
            return existing
        new_record = existing.model_copy(update={**updates, "updated_at": _now()})
        before = _safe_dump(existing)
        after = _safe_dump(new_record)
        with self._store.transaction():
            stored = self._store.update_webhook(new_record)
            self._store.append_audit(
                WebhookAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="webhook.updated",
                    target_id=stored.id,
                    before_state=before,
                    after_state=after,
                )
            )
        return stored

    def rotate_webhook(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        webhook_id: str,
    ) -> WebhookRotated:
        """Generate a new secret; preserve the old one in the 14-day grace.

        Static-secret webhooks reject rotate — the user owns those.
        """

        existing = self._authorize_write(
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
            webhook_id=webhook_id,
        )
        if existing.secret_strategy == "static":
            raise WebhookInvalidRequest("rotate_unsupported_for_static_strategy")
        return self._rotate_locked(
            tenant_id=tenant_id,
            actor_user_id=caller_user_id,
            existing=existing,
        )

    def delete_webhook(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        webhook_id: str,
    ) -> None:
        existing = self._authorize_write(
            tenant_id=tenant_id,
            caller_user_id=caller_user_id,
            caller_roles=caller_roles,
            webhook_id=webhook_id,
        )
        before = _safe_dump(existing)
        with self._store.transaction():
            self._store.soft_delete_webhook(tenant_id=tenant_id, webhook_id=existing.id)
            self._store.append_audit(
                WebhookAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=caller_user_id,
                    action="webhook.deleted",
                    target_id=existing.id,
                    before_state=before,
                )
            )

    # ------------------------------------------------------------------
    # Plaintext access for outbound delivery (test-fire + scheduler)
    # ------------------------------------------------------------------

    def reveal_secret_for_signing(
        self, *, record: WebhookRecord
    ) -> tuple[str, str | None]:
        """Decrypt the current + (still-valid) grace secret.

        Returns ``(current, previous_or_none)``. Callers — the
        test-fire route and the routine-fire dispatcher — sign with
        the *current* secret; the grace secret exists so receivers can
        verify deliveries that arrived before they rolled their own
        secret.
        """

        current = self._vault.decrypt(record.vault_ref)
        previous: str | None = None
        if (
            record.previous_vault_ref is not None
            and record.previous_expires_at is not None
            and record.previous_expires_at >= _now()
        ):
            previous = self._vault.decrypt(record.previous_vault_ref)
        return current, previous

    # ------------------------------------------------------------------
    # Rotation worker integration
    # ------------------------------------------------------------------

    def rotate_due(self, *, now: datetime, limit: int = 50) -> list[WebhookRotated]:
        """Rotation-worker entry point.

        Claims a batch of due rotating webhooks via the store's
        ``FOR UPDATE SKIP LOCKED`` semantics; for each claim, mints a
        new secret, preserves the old one for the grace window, and
        advances ``rotates_at`` by ``ROTATION_INTERVAL``. Returns one
        :class:`WebhookRotated` per row so the worker can emit metrics
        and audit hooks; the worker MUST drop the plaintexts after
        emitting the audit row (they're never stored on disk in
        plaintext form).
        """

        rotations: list[WebhookRotated] = []
        with self._store.transaction():
            due = self._store.claim_due_for_rotation(now=now, limit=limit)
            for existing in due:
                rotations.append(
                    self._rotate_locked(
                        tenant_id=existing.tenant_id,
                        actor_user_id="system:rotation_worker",
                        existing=existing,
                        action="webhook.rotated",
                        rotated_at=now,
                    )
                )
        return rotations

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _rotate_locked(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        existing: WebhookRecord,
        action: str = "webhook.rotated",
        rotated_at: datetime | None = None,
    ) -> WebhookRotated:
        """Rotate one webhook's secret + emit the audit row atomically.

        Always runs inside ``with self._store.transaction()`` so the
        primary write + audit append commit or roll back together (C3
        atomicity rule). The in-memory adapter supports nested
        ``transaction()`` blocks; the Postgres adapter wraps the inner
        block in a SAVEPOINT — both shapes preserve the all-or-nothing
        contract when ``rotate_due`` claims a batch and rotates each
        row inside the same outer transaction.
        """

        now = rotated_at or _now()
        plaintext = _mint_secret()
        new_vault_ref = self._vault.encrypt(plaintext)
        grace_plaintext: str | None = None
        if existing.vault_ref:
            try:
                grace_plaintext = self._vault.decrypt(existing.vault_ref)
            except Exception:
                # Defensive: if the previous ciphertext can't be decrypted
                # we still rotate (don't leak the failure into the audit
                # row's plaintext channel).
                grace_plaintext = None
        new_record = existing.model_copy(
            update={
                "vault_ref": new_vault_ref,
                "previous_vault_ref": existing.vault_ref,
                "previous_expires_at": now + self._rotation_grace,
                "rotates_at": now + self._rotation_interval,
                "updated_at": now,
            }
        )
        before = _safe_dump(existing)
        after = _safe_dump(new_record)
        with self._store.transaction():
            stored = self._store.update_webhook(new_record)
            self._store.append_audit(
                WebhookAuditRecord(
                    tenant_id=tenant_id,
                    actor_user_id=actor_user_id,
                    action=action,
                    target_id=stored.id,
                    before_state=before,
                    after_state=after,
                )
            )
        return WebhookRotated(
            record=stored,
            secret_plaintext=plaintext,
            grace_secret_plaintext=grace_plaintext,
        )

    def _authorize_write(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        caller_roles: Iterable[str],
        webhook_id: str,
    ) -> WebhookRecord:
        record = self._store.get_webhook(tenant_id=tenant_id, webhook_id=webhook_id)
        if record is None or not self._can_read(record, caller_user_id, caller_roles):
            raise WebhookNotFound(webhook_id)
        if record.owner_user_id != caller_user_id and not self._is_admin(caller_roles):
            raise WebhookForbidden(webhook_id)
        return record

    @staticmethod
    def _can_read(
        record: WebhookRecord, caller_user_id: str, caller_roles: Iterable[str]
    ) -> bool:
        if record.owner_user_id == caller_user_id:
            return True
        return WebhooksService._is_admin(caller_roles)

    @staticmethod
    def _is_admin(caller_roles: Iterable[str]) -> bool:
        return any(role in {"admin", "tenant_admin"} for role in caller_roles)

    @staticmethod
    def _validate_url(url: str) -> None:
        if not url.startswith("https://"):
            raise WebhookInvalidRequest("url_must_be_https")
        if len(url) > 2048:
            raise WebhookInvalidRequest("url_too_long")

    @staticmethod
    def _validate_strategy(strategy: str) -> None:
        if strategy not in ("rotating", "static"):
            raise WebhookInvalidRequest(f"invalid_secret_strategy:{strategy}")

    @staticmethod
    def _validate_cidrs(allowlist: Iterable[str]) -> None:
        for entry in allowlist:
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError as exc:
                raise WebhookInvalidRequest(f"invalid_cidr:{entry}") from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mint_secret() -> str:
    return secrets.token_urlsafe(_SECRET_BYTES)


def _safe_dump(record: WebhookRecord) -> dict[str, Any]:
    """Audit-safe dump of a webhook row.

    ``vault_ref`` / ``previous_vault_ref`` are OPAQUE pointers — they
    encode the ciphertext envelope but not the plaintext, so they're
    safe to ship into the audit channel. We still redact them so the
    audit row carries metadata only; if a deployment ever changes the
    envelope shape we don't want the new shape to land in audit
    history without a deliberate review.
    """

    dump = record.model_dump(mode="json")
    if "vault_ref" in dump:
        dump["vault_ref"] = "<redacted>"
    if dump.get("previous_vault_ref") is not None:
        dump["previous_vault_ref"] = "<redacted>"
    return dump


__all__ = [
    "ROTATION_GRACE",
    "ROTATION_INTERVAL",
    "WebhookCreated",
    "WebhookForbidden",
    "WebhookInvalidRequest",
    "WebhookNotFound",
    "WebhookRotated",
    "WebhooksService",
]
