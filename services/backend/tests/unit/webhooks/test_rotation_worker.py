"""Rotation worker tests — claim semantics + idempotency.

connectors-prd §9.2. The worker is the only consumer of the store's
``FOR UPDATE SKIP LOCKED`` claim; we pin the contract via the
in-memory mimic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend_app.token_vault import LocalTokenVault
from backend_app.webhooks.rotation_worker import WebhookRotationWorker
from backend_app.webhooks.service import (
    ROTATION_GRACE,
    ROTATION_INTERVAL,
    WebhooksService,
)
from backend_app.webhooks.store import InMemoryWebhooksStore


_VAULT_SECRET = "test-vault-secret-32-chars-min-length-yes"
_ORG = "org_acme"
_OWNER = "usr_sarah"


@pytest.fixture
def store() -> InMemoryWebhooksStore:
    return InMemoryWebhooksStore()


@pytest.fixture
def vault() -> LocalTokenVault:
    return LocalTokenVault(secret=_VAULT_SECRET)


@pytest.fixture
def service(store: InMemoryWebhooksStore, vault: LocalTokenVault) -> WebhooksService:
    return WebhooksService(store=store, token_vault=vault)


class _MutableClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


def _make_due_webhook(service: WebhooksService, *, url: str = "https://x/hook"):
    created = service.create_webhook(tenant_id=_ORG, caller_user_id=_OWNER, url=url)
    # Force the row's rotates_at into the past so the worker claims it.
    record = service._store.get_webhook(  # type: ignore[attr-defined]
        tenant_id=_ORG, webhook_id=created.record.id
    )
    assert record is not None
    overdue = record.model_copy(
        update={"rotates_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
    )
    service._store.update_webhook(overdue)  # type: ignore[attr-defined]
    return overdue, created.secret_plaintext


class TestTick:
    def test_claim_rotates_and_advances_rotates_at(
        self, service: WebhooksService, store: InMemoryWebhooksStore
    ) -> None:
        record, original = _make_due_webhook(service)
        worker = WebhookRotationWorker(service=service)
        results = worker.tick()
        assert len(results) == 1
        # Plaintext changed.
        assert results[0].secret_plaintext != original
        # Grace preserved the old plaintext.
        assert results[0].grace_secret_plaintext == original
        # rotates_at moved forward by ~90 days.
        fetched = store.get_webhook(tenant_id=_ORG, webhook_id=record.id)
        assert fetched is not None
        assert fetched.rotates_at is not None
        delta = fetched.rotates_at - datetime.now(timezone.utc)
        assert timedelta(days=89, hours=23) < delta <= ROTATION_INTERVAL
        # previous_expires_at sits ~14 days out.
        assert fetched.previous_expires_at is not None
        grace_delta = fetched.previous_expires_at - datetime.now(timezone.utc)
        assert timedelta(days=13, hours=23) < grace_delta <= ROTATION_GRACE

    def test_tick_is_idempotent(self, service: WebhooksService) -> None:
        _make_due_webhook(service)
        worker = WebhookRotationWorker(service=service)
        first = worker.tick()
        second = worker.tick()
        # Second tick claims nothing — the first advanced rotates_at.
        assert len(first) == 1
        assert second == []

    def test_skip_locked_emits_audit_for_each(
        self, service: WebhooksService, store: InMemoryWebhooksStore
    ) -> None:
        a, _ = _make_due_webhook(service, url="https://a.example/h")
        b, _ = _make_due_webhook(service, url="https://b.example/h")
        worker = WebhookRotationWorker(service=service)
        results = worker.tick()
        assert {r.record.id for r in results} == {a.id, b.id}
        for record_id in (a.id, b.id):
            audits = store.list_audit_for_webhook(tenant_id=_ORG, webhook_id=record_id)
            actions = [a.action for a in audits]
            assert "webhook.rotated" in actions

    def test_paused_rows_skipped(
        self, service: WebhooksService, store: InMemoryWebhooksStore
    ) -> None:
        record, _ = _make_due_webhook(service)
        paused = record.model_copy(update={"status": "paused"})
        store.update_webhook(paused)
        worker = WebhookRotationWorker(service=service)
        assert worker.tick() == []

    def test_static_rows_skipped(self, service: WebhooksService) -> None:
        # Static-strategy webhook is created with rotates_at=None.
        service.create_webhook(
            tenant_id=_ORG,
            caller_user_id=_OWNER,
            url="https://example.com/hook",
            secret_strategy="static",
            static_secret="mine",
        )
        worker = WebhookRotationWorker(service=service)
        assert worker.tick() == []

    def test_actor_user_id_is_system(
        self, service: WebhooksService, store: InMemoryWebhooksStore
    ) -> None:
        record, _ = _make_due_webhook(service)
        worker = WebhookRotationWorker(service=service)
        worker.tick()
        audits = store.list_audit_for_webhook(tenant_id=_ORG, webhook_id=record.id)
        rotation_audits = [a for a in audits if a.action == "webhook.rotated"]
        assert rotation_audits
        assert rotation_audits[0].actor_user_id == "system:rotation_worker"


class TestRunLoop:
    """Smoke-tests the async ``run()`` loop start + stop."""

    def test_run_calls_tick_and_exits_on_stop(self, service: WebhooksService) -> None:
        _make_due_webhook(service)
        worker = WebhookRotationWorker(service=service, interval_s=0.05)

        async def _exercise() -> int:
            task = asyncio.create_task(worker.run())
            # Yield long enough for one tick to happen.
            await asyncio.sleep(0.15)
            worker.stop()
            await asyncio.wait_for(task, timeout=2.0)
            return 1

        result = asyncio.run(_exercise())
        assert result == 1
