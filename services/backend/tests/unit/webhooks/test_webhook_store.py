"""WebhooksStore tests — CRUD + tenant isolation + claim semantics.

The in-memory adapter mirrors the Postgres shape (RLS for tenant
scoping, ``FOR UPDATE SKIP LOCKED`` for the rotation claim queue).
The unit tests pin the contract; the postgres adapter ships alongside
the migration and reuses these tests against a live DB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend_app.webhooks.store import (
    InMemoryWebhooksStore,
    WebhookAuditRecord,
    WebhookRecord,
)


_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def _record(**overrides) -> WebhookRecord:
    defaults: dict = dict(
        tenant_id="org_acme",
        owner_user_id="usr_sarah",
        url="https://example.com/hook",
        secret_strategy="rotating",
        vault_ref="ct_abc",
        rotates_at=_NOW + timedelta(days=90),
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(overrides)
    return WebhookRecord(**defaults)


class TestCrud:
    def test_insert_then_get(self) -> None:
        store = InMemoryWebhooksStore()
        record = _record()
        store.insert_webhook(record)
        fetched = store.get_webhook(tenant_id="org_acme", webhook_id=record.id)
        assert fetched is not None
        assert fetched.url == "https://example.com/hook"

    def test_id_prefix(self) -> None:
        record = _record()
        assert record.id.startswith("trig_")

    def test_update_writes_through(self) -> None:
        store = InMemoryWebhooksStore()
        record = store.insert_webhook(_record())
        updated = record.model_copy(update={"status": "paused"})
        store.update_webhook(updated)
        fetched = store.get_webhook(tenant_id="org_acme", webhook_id=record.id)
        assert fetched is not None
        assert fetched.status == "paused"

    def test_soft_delete_hides_from_default_reads(self) -> None:
        store = InMemoryWebhooksStore()
        record = store.insert_webhook(_record())
        assert store.soft_delete_webhook(tenant_id="org_acme", webhook_id=record.id)
        assert store.get_webhook(tenant_id="org_acme", webhook_id=record.id) is None
        # Compliance read with include_deleted=True still surfaces it.
        kept = store.get_webhook(
            tenant_id="org_acme", webhook_id=record.id, include_deleted=True
        )
        assert kept is not None
        assert kept.deleted_at is not None

    def test_soft_delete_is_idempotent(self) -> None:
        store = InMemoryWebhooksStore()
        record = store.insert_webhook(_record())
        assert store.soft_delete_webhook(tenant_id="org_acme", webhook_id=record.id)
        assert store.soft_delete_webhook(tenant_id="org_acme", webhook_id=record.id)


class TestTenantIsolation:
    def test_get_refuses_cross_tenant(self) -> None:
        store = InMemoryWebhooksStore()
        record = store.insert_webhook(_record(tenant_id="org_a"))
        # Same id, different tenant lookup → must return None (mimics RLS).
        assert store.get_webhook(tenant_id="org_b", webhook_id=record.id) is None

    def test_list_scopes_to_tenant(self) -> None:
        store = InMemoryWebhooksStore()
        store.insert_webhook(_record(tenant_id="org_a"))
        store.insert_webhook(_record(tenant_id="org_b"))
        rows_a, _ = store.list_webhooks(tenant_id="org_a")
        rows_b, _ = store.list_webhooks(tenant_id="org_b")
        assert len(rows_a) == 1
        assert len(rows_b) == 1
        assert rows_a[0].tenant_id == "org_a"
        assert rows_b[0].tenant_id == "org_b"

    def test_list_filters_by_owner(self) -> None:
        store = InMemoryWebhooksStore()
        store.insert_webhook(_record(owner_user_id="usr_a"))
        store.insert_webhook(_record(owner_user_id="usr_b"))
        rows, _ = store.list_webhooks(tenant_id="org_acme", owner_user_id="usr_a")
        assert len(rows) == 1
        assert rows[0].owner_user_id == "usr_a"

    def test_list_filters_by_status(self) -> None:
        store = InMemoryWebhooksStore()
        store.insert_webhook(_record(status="active"))
        store.insert_webhook(_record(status="paused"))
        rows, _ = store.list_webhooks(tenant_id="org_acme", statuses=("active",))
        assert len(rows) == 1
        assert rows[0].status == "active"


class TestClaimQueue:
    """``claim_due_for_rotation`` mimics ``FOR UPDATE SKIP LOCKED``."""

    def test_requires_transaction(self) -> None:
        store = InMemoryWebhooksStore()
        store.insert_webhook(_record(rotates_at=_NOW - timedelta(seconds=1)))
        with pytest.raises(RuntimeError, match="transaction"):
            store.claim_due_for_rotation(now=_NOW)

    def test_only_due_rows_returned(self) -> None:
        store = InMemoryWebhooksStore()
        # Due:
        store.insert_webhook(_record(rotates_at=_NOW - timedelta(seconds=1)))
        # Not due:
        store.insert_webhook(_record(rotates_at=_NOW + timedelta(days=1)))
        with store.transaction():
            claimed = store.claim_due_for_rotation(now=_NOW)
        assert len(claimed) == 1

    def test_paused_rows_skipped(self) -> None:
        store = InMemoryWebhooksStore()
        store.insert_webhook(
            _record(status="paused", rotates_at=_NOW - timedelta(seconds=1))
        )
        with store.transaction():
            claimed = store.claim_due_for_rotation(now=_NOW)
        assert claimed == ()

    def test_static_rows_skipped(self) -> None:
        store = InMemoryWebhooksStore()
        store.insert_webhook(
            _record(
                secret_strategy="static",
                rotates_at=_NOW - timedelta(seconds=1),
            )
        )
        with store.transaction():
            claimed = store.claim_due_for_rotation(now=_NOW)
        assert claimed == ()

    def test_deleted_rows_skipped(self) -> None:
        store = InMemoryWebhooksStore()
        record = store.insert_webhook(_record(rotates_at=_NOW - timedelta(seconds=1)))
        store.soft_delete_webhook(tenant_id="org_acme", webhook_id=record.id)
        with store.transaction():
            claimed = store.claim_due_for_rotation(now=_NOW)
        assert claimed == ()

    def test_skip_locked_within_one_transaction(self) -> None:
        """A second claim_due_for_rotation inside the same transaction
        does not re-emit the same row (mimics row-level locks held
        until the txn closes)."""

        store = InMemoryWebhooksStore()
        record = store.insert_webhook(_record(rotates_at=_NOW - timedelta(seconds=1)))
        with store.transaction():
            first = store.claim_due_for_rotation(now=_NOW)
            second = store.claim_due_for_rotation(now=_NOW)
        assert len(first) == 1
        assert first[0].id == record.id
        assert second == ()

    def test_lock_releases_after_transaction(self) -> None:
        """After the transaction exits, a fresh claim re-acquires the
        row (we never updated rotates_at, so it's still due)."""

        store = InMemoryWebhooksStore()
        record = store.insert_webhook(_record(rotates_at=_NOW - timedelta(seconds=1)))
        with store.transaction():
            first = store.claim_due_for_rotation(now=_NOW)
        with store.transaction():
            second = store.claim_due_for_rotation(now=_NOW)
        assert first[0].id == record.id
        assert second[0].id == record.id


class TestAudit:
    def test_append_then_list_scopes_to_target(self) -> None:
        store = InMemoryWebhooksStore()
        a = store.insert_webhook(_record())
        b = store.insert_webhook(_record(url="https://other.example.com/hook"))
        store.append_audit(
            WebhookAuditRecord(
                tenant_id="org_acme",
                actor_user_id="usr_sarah",
                action="webhook.created",
                target_id=a.id,
            )
        )
        store.append_audit(
            WebhookAuditRecord(
                tenant_id="org_acme",
                actor_user_id="usr_sarah",
                action="webhook.created",
                target_id=b.id,
            )
        )
        rows = store.list_audit_for_webhook(tenant_id="org_acme", webhook_id=a.id)
        assert len(rows) == 1
        assert rows[0].target_id == a.id

    def test_audit_id_prefix(self) -> None:
        record = WebhookAuditRecord(
            tenant_id="org_acme",
            actor_user_id="usr_sarah",
            action="webhook.created",
            target_id="trig_x",
        )
        assert record.audit_id.startswith("audwh_")
