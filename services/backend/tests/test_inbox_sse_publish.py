"""Tests for the bus.publish wiring at the inbox mutation sites.

This module owns the producer-side claim: when a mutation lands at the
canonical sites (``POST /internal/v1/inbox/items`` producer endpoint,
``PATCH /v1/inbox/{item_id}`` user mutation, ``POST /v1/inbox/bulk`` user
bulk action), the corresponding ``item_added`` / ``item_updated`` event
is published on the SSE bus stashed at ``app.state.inbox_activity_bus``.

The complementary consumer-side claim (the SSE stream serialises the
event into the wire frame the FE consumes) lives in ``test_inbox_sse.py``;
this file deliberately does not exercise the stream path so the two
tests stay narrow to their respective concerns.

Test discipline:

* Use ``create_app`` so the route + service + bus wiring matches
  production exactly (no test-only shortcuts that might mask drift).
* Drain the bus via ``list_after`` rather than the SSE stream to keep
  the test synchronous + deterministic.
* Assert tenant isolation: a cross-tenant subscriber sees zero frames.
* Assert PII discipline: the published payload carries ``body_ref`` but
  never ``body_markdown`` content.

The brief's three hard correctness rules:

1. Publish happens AFTER the durable write (post-commit). We verify by
   checking the store reflects the mutation AND the bus has the event.
2. Tenant-isolation in the publish — channel key is the recipient's
   ``(tenant_id, owner_user_id)``.
3. No body content in the published payload — only ``body_ref``.
"""

from __future__ import annotations

import asyncio

from copilot_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.inbox.service import InboxService
from backend_app.inbox.sse import InMemoryInboxActivityBus
from backend_app.inbox.store import InMemoryInboxStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seeded_identity() -> InMemoryIdentityStore:
    """Tenant + recipient + admin caller seeded across two orgs.

    Two orgs share the same user_id (``usr_sarah``) so a cross-tenant
    isolation assertion has somewhere to land.
    """

    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    store.create_user(
        UserRecord(
            user_id="usr_sarah",
            org_id="org_acme",
            primary_email="sarah@acme.com",
            display_name="Sarah",
        )
    )
    store.create_organization(
        OrganizationRecord(org_id="org_zeta", display_name="Zeta", slug="zeta")
    )
    store.create_user(
        UserRecord(
            user_id="usr_sarah_zeta",
            org_id="org_zeta",
            primary_email="sarah@zeta.com",
            display_name="Sarah Z",
        )
    )
    return store


def _client(
    *, inbox_store: InMemoryInboxStore | None = None
) -> tuple[TestClient, InMemoryInboxStore, InMemoryInboxActivityBus]:
    """Build a full ``create_app`` test client with a fresh inbox bus.

    Resetting the bus singleton between tests keeps frame counts
    deterministic — otherwise the singleton accumulates ``sequence_no``
    across tests in the same process.
    """

    InMemoryInboxActivityBus.reset_default_for_tests()
    store = inbox_store or InMemoryInboxStore()
    identity = _seeded_identity()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        inbox_store=store,
    )
    bus = app.state.inbox_activity_bus
    return TestClient(app), store, bus


def _seed_item(
    store: InMemoryInboxStore,
    *,
    tenant_id: str = "org_acme",
    owner_user_id: str = "usr_sarah",
    body_markdown: str | None = "secret body that must NEVER leak on the bus",
) -> str:
    """Seed one inbox item bypassing the route layer (no pre-test bus event).

    We don't go through the internal route because that fires an
    ``item_added`` event we don't want polluting the assertion. The
    canonical service insert path writes the audit row + record and
    returns — perfectly fine for fixture setup.
    """

    service = InboxService(store=store, identity_store=InMemoryIdentityStore())
    record = service.insert_item_with_body(
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        kind="approval_request",
        title="Approve this",
        sender={"kind": "agent", "id": "agent_atlas", "display_name": "Atlas"},
        body_markdown=body_markdown,
    )
    return record.id


def _producer_payload(**overrides) -> dict:
    """Body for ``POST /internal/v1/inbox/items``."""

    base = {
        "recipient_user_id": "usr_sarah",
        "tenant_id": "org_acme",
        "kind": "approval_request",
        "subject": "Approval needed",
        "preview": "Atlas drafted an edit you need to review.",
        "body": "secret body that must NEVER leak on the bus",
        "sender_agent_id": "agent_atlas",
        "sender_agent_name": "Atlas",
        "producer_id": "ai-backend",
        "external_ref": "approval-001",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# PATCH /v1/inbox/{id} publishes item_updated
# ---------------------------------------------------------------------------


class TestPatchPublishes:
    def test_mark_read_emits_item_updated(self) -> None:
        """PATCH state=read → one ``item_updated`` frame, sequence_no=1."""

        client, store, bus = _client()
        item_id = _seed_item(store)

        resp = client.patch(
            f"/v1/inbox/{item_id}",
            params={"org_id": "org_acme", "user_id": "usr_sarah"},
            json={"state": "read"},
        )
        assert resp.status_code == 200, resp.text

        events = list(
            bus.list_after(org_id="org_acme", user_id="usr_sarah", after_sequence=0)
        )
        assert len(events) == 1, [
            (e.event_type, e.item.get("id") if e.item else None) for e in events
        ]
        envelope = events[0]
        assert envelope.event_type == "item_updated"
        assert envelope.sequence_no == 1
        assert envelope.item is not None
        assert envelope.item["id"] == item_id
        # New state reflected in the published payload (FE renders the
        # rail badge off this field).
        assert envelope.item["state"] == "read"
        # PII discipline (brief rule 3) — no body bytes on the bus.
        assert "body_markdown" not in envelope.item
        assert "body" not in envelope.item
        # body_ref pointer is fine; FE lazy-loads bytes via GET /v1/inbox/{id}.
        assert envelope.item["body_ref"] is not None

    def test_snooze_emits_item_updated(self) -> None:
        """PATCH state=snoozed with a future ``snoozed_until`` → frame."""

        from datetime import datetime, timedelta, timezone

        client, store, bus = _client()
        item_id = _seed_item(store)

        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        resp = client.patch(
            f"/v1/inbox/{item_id}",
            params={"org_id": "org_acme", "user_id": "usr_sarah"},
            json={"state": "snoozed", "snoozed_until": future},
        )
        assert resp.status_code == 200, resp.text

        events = list(
            bus.list_after(org_id="org_acme", user_id="usr_sarah", after_sequence=0)
        )
        assert len(events) == 1
        assert events[0].event_type == "item_updated"
        assert events[0].item is not None
        assert events[0].item["state"] == "snoozed"
        assert events[0].item["snoozed_until"] is not None

    def test_dismiss_emits_item_updated(self) -> None:
        client, store, bus = _client()
        item_id = _seed_item(store)

        resp = client.patch(
            f"/v1/inbox/{item_id}",
            params={"org_id": "org_acme", "user_id": "usr_sarah"},
            json={"state": "dismissed"},
        )
        assert resp.status_code == 200, resp.text

        events = list(
            bus.list_after(org_id="org_acme", user_id="usr_sarah", after_sequence=0)
        )
        assert len(events) == 1
        assert events[0].event_type == "item_updated"
        assert events[0].item is not None
        assert events[0].item["state"] == "dismissed"

    def test_failed_patch_does_not_publish(self) -> None:
        """A 4xx PATCH must not leak a phantom bus event (brief rule 1).

        The PATCH layer returns 4xx for invalid state transitions before
        the service touches the store; the bus must reflect that.
        """

        client, store, bus = _client()
        item_id = _seed_item(store)
        # Dismiss first so the row is terminal.
        client.patch(
            f"/v1/inbox/{item_id}",
            params={"org_id": "org_acme", "user_id": "usr_sarah"},
            json={"state": "dismissed"},
        )
        before = bus.latest_sequence_no(org_id="org_acme", user_id="usr_sarah")

        # Now try to mark-read the dismissed (terminal) row — 400.
        resp = client.patch(
            f"/v1/inbox/{item_id}",
            params={"org_id": "org_acme", "user_id": "usr_sarah"},
            json={"state": "read"},
        )
        assert resp.status_code == 400

        after = bus.latest_sequence_no(org_id="org_acme", user_id="usr_sarah")
        # No new publish past the terminal dismiss.
        assert after == before


# ---------------------------------------------------------------------------
# Internal POST /internal/v1/inbox/items publishes item_added
# ---------------------------------------------------------------------------


class TestInternalPostPublishes:
    def test_producer_emits_item_added(self, monkeypatch) -> None:
        """POST /internal/v1/inbox/items → ``item_added`` on the recipient's channel."""

        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, store, bus = _client()

        resp = client.post(
            "/internal/v1/inbox/items",
            json=_producer_payload(),
            headers={
                SERVICE_TOKEN_HEADER: "tok-test",
                ORG_HEADER: "org_acme",
                USER_HEADER: "usr_sarah",
            },
        )
        assert resp.status_code == 201, resp.text
        # Store committed the row.
        assert len(store.items) == 1
        # Bus has exactly one ``item_added`` frame on the recipient's
        # channel.
        events = list(
            bus.list_after(org_id="org_acme", user_id="usr_sarah", after_sequence=0)
        )
        assert len(events) == 1
        envelope = events[0]
        assert envelope.event_type == "item_added"
        assert envelope.sequence_no == 1
        assert envelope.item is not None
        assert envelope.item["owner_user_id"] == "usr_sarah"
        assert envelope.item["tenant_id"] == "org_acme"
        assert envelope.item["kind"] == "approval_request"
        # PII discipline — body bytes never leave the body table.
        assert "body_markdown" not in envelope.item
        assert "body" not in envelope.item
        assert envelope.item["body_ref"] is not None

    def test_dedupe_does_not_double_publish(self, monkeypatch) -> None:
        """Idempotent retry returns ``deduped: true`` and must NOT re-publish.

        The first POST fired ``item_added`` already; a second POST with
        the same ``(producer_id, external_ref)`` is a network retry and
        must not surface as a second frame to the FE.
        """

        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, _, bus = _client()
        headers = {
            SERVICE_TOKEN_HEADER: "tok-test",
            ORG_HEADER: "org_acme",
            USER_HEADER: "usr_sarah",
        }

        first = client.post(
            "/internal/v1/inbox/items",
            json=_producer_payload(),
            headers=headers,
        )
        second = client.post(
            "/internal/v1/inbox/items",
            json=_producer_payload(),
            headers=headers,
        )
        assert first.status_code == 201
        assert second.status_code == 201
        assert second.json()["deduped"] is True

        events = list(
            bus.list_after(org_id="org_acme", user_id="usr_sarah", after_sequence=0)
        )
        assert len(events) == 1, [(e.event_type, e.sequence_no) for e in events]

    def test_cross_tenant_subscriber_sees_zero_frames(self, monkeypatch) -> None:
        """Tenant isolation (brief rule 2): org_zeta sees nothing from org_acme.

        The bus channel key is the recipient's ``(tenant_id,
        owner_user_id)`` — a subscriber for a different tenant or a
        different user under the same tenant must not see another
        channel's frames.
        """

        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, _, bus = _client()
        client.post(
            "/internal/v1/inbox/items",
            json=_producer_payload(),
            headers={
                SERVICE_TOKEN_HEADER: "tok-test",
                ORG_HEADER: "org_acme",
                USER_HEADER: "usr_sarah",
            },
        )

        # Different org, same user_id surface.
        other_org = list(
            bus.list_after(org_id="org_zeta", user_id="usr_sarah", after_sequence=0)
        )
        assert other_org == []

        # Same org, different user_id surface.
        other_user = list(
            bus.list_after(org_id="org_acme", user_id="usr_other", after_sequence=0)
        )
        assert other_user == []


# ---------------------------------------------------------------------------
# Service-level publish helper — direct unit test
# ---------------------------------------------------------------------------


class TestServicePublishHelper:
    def test_publish_event_is_noop_without_bus(self) -> None:
        """Without an injected bus, ``publish_event`` is a no-op.

        Lets tests + dev configurations that disable streaming wire the
        service without a stream-side dependency.
        """

        store = InMemoryInboxStore()
        service = InboxService(store=store, identity_store=InMemoryIdentityStore())
        record = service.insert_item_with_body(
            tenant_id="org_acme",
            owner_user_id="usr_sarah",
            kind="approval_request",
            title="t",
            sender={"kind": "agent"},
        )
        # Should not raise — the bus is None, so the helper returns
        # immediately.
        asyncio.run(service.publish_event(record=record, event_type="item_added"))

    def test_publish_event_uses_recipient_channel(self) -> None:
        """Channel key derives from the record's ``(tenant_id,
        owner_user_id)`` — not from any caller-supplied id (brief rule 2).
        """

        store = InMemoryInboxStore()
        bus = InMemoryInboxActivityBus()
        service = InboxService(
            store=store,
            identity_store=InMemoryIdentityStore(),
            activity_bus=bus,
        )
        record = service.insert_item_with_body(
            tenant_id="org_acme",
            owner_user_id="usr_sarah",
            kind="mention",
            title="t",
            sender={"kind": "agent"},
        )
        asyncio.run(service.publish_event(record=record, event_type="item_added"))

        events = list(
            bus.list_after(org_id="org_acme", user_id="usr_sarah", after_sequence=0)
        )
        assert len(events) == 1
        # Cross-channel: another tenant gets nothing.
        assert (
            list(
                bus.list_after(
                    org_id="org_other", user_id="usr_sarah", after_sequence=0
                )
            )
            == []
        )

    def test_event_payload_omits_body_bytes(self) -> None:
        """``to_event_payload`` strips body content (brief rule 3)."""

        store = InMemoryInboxStore()
        service = InboxService(store=store, identity_store=InMemoryIdentityStore())
        record = service.insert_item_with_body(
            tenant_id="org_acme",
            owner_user_id="usr_sarah",
            kind="approval_request",
            title="t",
            sender={"kind": "agent"},
            body_markdown="never leak this",
        )
        payload = InboxService.to_event_payload(record)
        assert "body_markdown" not in payload
        assert "body" not in payload
        assert payload["body_ref"] == record.body_ref
        assert payload["state"] == "unread"
