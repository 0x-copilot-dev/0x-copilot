"""Tests for the internal Inbox producer endpoint — ``POST /internal/v1/inbox/items``.

Coverage:

* Service-token required (401 in production without; 200 with).
* Identity headers required (401 without ``x-enterprise-org-id`` or
  ``x-enterprise-user-id``).
* Cross-tenant rejection: header ``org_id`` ≠ payload ``tenant_id`` → 403.
* Idempotency: a second POST with the same ``(producer_id, external_ref)``
  returns the existing row, not a duplicate (``deduped: true``).
* Audit row written with ``actor_user_id`` = the service-token caller's
  user (the agent's owner), not the recipient.
* Invalid kind → 400.

The route delegates to the canonical :class:`InboxService` for the durable
write + audit row; the store is the canonical in-memory adapter.
"""

from __future__ import annotations

from copilot_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend_app.identity.store import InMemoryIdentityStore
from backend_app.inbox.internal_routes import register_inbox_internal_routes
from backend_app.inbox.service import InboxService
from backend_app.inbox.store import InMemoryInboxStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _client() -> tuple[TestClient, InMemoryInboxStore]:
    """Build a minimal FastAPI app with the internal route + canonical service.

    Avoids the full ``create_app`` so the test runs without the rest of
    the backend's wiring (sessions, identity, etc.) — the CRUD route test
    exercises that integration; this test scope is the internal producer
    surface only.
    """
    app = FastAPI()
    store = InMemoryInboxStore()
    app.state.inbox_store = store
    app.state.inbox_service = InboxService(
        store=store,
        identity_store=InMemoryIdentityStore(),
    )
    register_inbox_internal_routes(app)
    return TestClient(app), store


def _payload(**overrides) -> dict:
    base = {
        "recipient_user_id": "user_a",
        "tenant_id": "org_acme",
        "kind": "approval_request",
        "subject": "Approval needed",
        "preview": "Atlas drafted an edit you need to review.",
        "body": "Open the thread to review.",
        "approval_id": "approval_001",
        "thread_id": "conv_001",
        "run_id": "run_001",
        "sender_agent_id": "atlas",
        "sender_agent_name": "Atlas",
        "producer_id": "ai-backend",
        "external_ref": "approval-approval_001",
    }
    base.update(overrides)
    return base


def _service_headers(*, org: str = "org_acme", user: str = "user_a") -> dict[str, str]:
    return {
        SERVICE_TOKEN_HEADER: "tok-test",
        ORG_HEADER: org,
        USER_HEADER: user,
    }


# ---------------------------------------------------------------------------
# Auth + identity headers
# ---------------------------------------------------------------------------


class TestAuth:
    def test_rejects_without_service_token_in_production(self, monkeypatch) -> None:
        """Production fails closed without the service token."""
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "production")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, _ = _client()
        response = client.post(
            "/internal/v1/inbox/items",
            json=_payload(),
            headers={ORG_HEADER: "org_acme", USER_HEADER: "user_a"},
        )
        # 401 because the service-token header is missing.
        assert response.status_code == 401

    def test_rejects_wrong_service_token(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "production")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, _ = _client()
        response = client.post(
            "/internal/v1/inbox/items",
            json=_payload(),
            headers={
                SERVICE_TOKEN_HEADER: "wrong-token",
                ORG_HEADER: "org_acme",
                USER_HEADER: "user_a",
            },
        )
        assert response.status_code == 401

    def test_rejects_without_org_header(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "production")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, _ = _client()
        response = client.post(
            "/internal/v1/inbox/items",
            json=_payload(),
            headers={SERVICE_TOKEN_HEADER: "tok-test", USER_HEADER: "user_a"},
        )
        assert response.status_code == 401

    def test_rejects_without_user_header(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "production")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, _ = _client()
        response = client.post(
            "/internal/v1/inbox/items",
            json=_payload(),
            headers={SERVICE_TOKEN_HEADER: "tok-test", ORG_HEADER: "org_acme"},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_cross_tenant_rejected(self, monkeypatch) -> None:
        """Header ``org_id`` must match payload ``tenant_id`` — 403 otherwise."""
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, store = _client()
        response = client.post(
            "/internal/v1/inbox/items",
            json=_payload(tenant_id="org_acme"),
            headers=_service_headers(org="org_zeta", user="user_alien"),
        )
        assert response.status_code == 403
        # Nothing inserted.
        assert store.items == {}


# ---------------------------------------------------------------------------
# Happy path + idempotency
# ---------------------------------------------------------------------------


class TestProduce:
    def test_inserts_item_with_links_and_sender(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, store = _client()
        response = client.post(
            "/internal/v1/inbox/items",
            json=_payload(),
            headers=_service_headers(),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["tenant_id"] == "org_acme"
        assert body["recipient_user_id"] == "user_a"
        assert body["kind"] == "approval_request"
        assert body["external_ref"] == "approval-approval_001"
        assert body["deduped"] is False

        # Store reflects the insert.
        assert len(store.items) == 1
        record = next(iter(store.items.values()))
        assert record.owner_user_id == "user_a"
        assert record.kind == "approval_request"
        assert record.external_ref == "approval-approval_001"
        assert record.producer_id == "ai-backend"
        # Body row created and referenced.
        assert record.body_ref is not None
        assert record.body_ref in store.bodies
        assert store.bodies[record.body_ref].body_markdown == (
            "Open the thread to review."
        )
        # Links include approval/chat/run per §3.5.
        link_kinds = {link["kind"] for link in record.links}
        assert link_kinds == {"approval", "chat", "run"}
        # Sender denormalises to agent display.
        assert record.sender == {
            "kind": "agent",
            "id": "atlas",
            "display_name": "Atlas",
        }

    def test_idempotent_on_duplicate_post(self, monkeypatch) -> None:
        """Two posts with the same external_ref → same row, deduped flag set."""
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, store = _client()
        first = client.post(
            "/internal/v1/inbox/items",
            json=_payload(),
            headers=_service_headers(),
        )
        second = client.post(
            "/internal/v1/inbox/items",
            json=_payload(),
            headers=_service_headers(),
        )
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert second.json()["deduped"] is True
        # Only one row.
        assert len(store.items) == 1

    def test_idempotency_uses_header_when_body_omits_ref(self, monkeypatch) -> None:
        """``idempotency-key`` header substitutes for an absent ``external_ref``."""
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, store = _client()
        payload = _payload()
        payload.pop("external_ref")
        headers = {**_service_headers(), "idempotency-key": "header-approval-1"}
        response = client.post(
            "/internal/v1/inbox/items", json=payload, headers=headers
        )
        assert response.status_code == 201
        assert response.json()["external_ref"] == "header-approval-1"
        assert len(store.items) == 1

    def test_audit_row_written_with_agent_owner_as_actor(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, store = _client()
        # Service token's user header identifies the agent's owner.
        client.post(
            "/internal/v1/inbox/items",
            json=_payload(),
            headers=_service_headers(user="user_owner"),
        )
        actions = [row.action for row in store.audits]
        assert "inbox.item_created" in actions
        created = next(
            row for row in store.audits if row.action == "inbox.item_created"
        )
        # §6.1 audit taxonomy: actor = service-token's user_id (agent's
        # owner), NOT the recipient.
        assert created.actor_user_id == "user_owner"
        assert created.tenant_id == "org_acme"

    def test_invalid_kind_returns_400(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, _ = _client()
        response = client.post(
            "/internal/v1/inbox/items",
            json=_payload(kind="not_a_real_kind"),
            headers=_service_headers(),
        )
        assert response.status_code == 400
