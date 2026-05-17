"""Integration tests for the tier-2 adapter registry HTTP routes."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend_app.adapter_registry import (
    AdapterRegistryService,
    InMemoryAdapterRegistryStore,
)
from backend_app.adapter_registry.storage import InMemorySourceStorage
from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore


def _identity_store() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    store.create_organization(
        OrganizationRecord(org_id="org_globex", display_name="Globex", slug="globex")
    )
    store.create_user(
        UserRecord(
            user_id="usr_alice",
            org_id="org_acme",
            primary_email="alice@acme.com",
            display_name="Alice",
            email_verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    store.create_user(
        UserRecord(
            user_id="usr_admin",
            org_id="org_platform",
            primary_email="admin@platform.com",
            display_name="Admin",
            email_verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    return store


@pytest.fixture
def client() -> tuple[TestClient, AdapterRegistryService]:
    service = AdapterRegistryService(
        store=InMemoryAdapterRegistryStore(),
        source_storage=InMemorySourceStorage(),
    )
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=_identity_store(),
        adapter_registry_service=service,
    )
    return TestClient(app), service


def _params(*, org_id: str = "org_acme", user_id: str = "usr_alice") -> dict[str, str]:
    return {"org_id": org_id, "user_id": user_id}


def _submission(*, scheme: str = "saas:salesforce") -> dict[str, object]:
    return {
        "scheme": scheme,
        "version": 1,
        "layout": "form",
        "source": "// adapter source",
        "harvest_metrics": {
            "zero_error_sessions": 10,
            "total_sessions": 10,
            "user_reported_issues": 0,
            "generator_model": "claude-opus-4-7",
        },
    }


class TestSubmitCandidate:
    def test_201_on_valid_payload(
        self, client: tuple[TestClient, AdapterRegistryService]
    ) -> None:
        c, _ = client
        response = c.post(
            "/internal/v1/adapter_registry/candidates",
            params=_params(),
            json=_submission(),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["scheme"] == "saas:salesforce"
        assert body["status"] == "submitted"
        assert body["tenant_id"] == "org_acme"

    def test_422_on_invalid_layout(
        self, client: tuple[TestClient, AdapterRegistryService]
    ) -> None:
        c, _ = client
        bad = _submission()
        bad["layout"] = "grid"
        response = c.post(
            "/internal/v1/adapter_registry/candidates",
            params=_params(),
            json=bad,
        )
        assert response.status_code == 422


class TestPromotionFlow:
    def test_submit_approve_promoted_visible_to_other_tenant(
        self, client: tuple[TestClient, AdapterRegistryService]
    ) -> None:
        c, _ = client
        submitted = c.post(
            "/internal/v1/adapter_registry/candidates",
            params=_params(),
            json=_submission(),
        )
        candidate_id = submitted.json()["candidate_id"]

        decision = c.post(
            f"/internal/v1/adapter_registry/candidates/{candidate_id}/decisions",
            params=_params(org_id="org_platform", user_id="usr_admin"),
            json={"action": "approve", "notes": "LGTM"},
        )
        assert decision.status_code == 200, decision.text
        assert decision.json()["status"] == "approved"

        listed = c.get(
            "/internal/v1/adapter_registry/promoted",
            params=_params(org_id="org_globex", user_id="usr_alice"),
        )
        assert listed.status_code == 200, listed.text
        adapters = listed.json()["adapters"]
        assert len(adapters) == 1
        assert adapters[0]["scheme"] == "saas:salesforce"
        assert adapters[0]["origin"] == "community"

    def test_request_changes_keeps_candidate_open(
        self, client: tuple[TestClient, AdapterRegistryService]
    ) -> None:
        c, _ = client
        submitted = c.post(
            "/internal/v1/adapter_registry/candidates",
            params=_params(),
            json=_submission(),
        )
        candidate_id = submitted.json()["candidate_id"]
        decision = c.post(
            f"/internal/v1/adapter_registry/candidates/{candidate_id}/decisions",
            params=_params(org_id="org_platform", user_id="usr_admin"),
            json={"action": "request-changes", "notes": "tighten"},
        )
        assert decision.status_code == 200
        assert decision.json()["status"] == "changes-requested"


class TestTenantOptOut:
    def test_opted_out_tenant_sees_empty_promoted(
        self, client: tuple[TestClient, AdapterRegistryService]
    ) -> None:
        c, _ = client
        submitted = c.post(
            "/internal/v1/adapter_registry/candidates",
            params=_params(),
            json=_submission(),
        )
        candidate_id = submitted.json()["candidate_id"]
        c.post(
            f"/internal/v1/adapter_registry/candidates/{candidate_id}/decisions",
            params=_params(org_id="org_platform", user_id="usr_admin"),
            json={"action": "approve", "notes": None},
        )
        toggled = c.put(
            "/internal/v1/adapter_registry/opt-out",
            params=_params(org_id="org_globex", user_id="usr_alice"),
            json={"opted_out": True},
        )
        assert toggled.status_code == 200
        assert toggled.json()["opted_out"] is True

        listed = c.get(
            "/internal/v1/adapter_registry/promoted",
            params=_params(org_id="org_globex", user_id="usr_alice"),
        )
        assert listed.status_code == 200
        assert listed.json()["adapters"] == []

        opt_out_view = c.get(
            "/internal/v1/adapter_registry/opt-out",
            params=_params(org_id="org_globex", user_id="usr_alice"),
        )
        assert opt_out_view.status_code == 200
        assert opt_out_view.json()["opted_out"] is True

    def test_opt_out_default_is_opted_in(
        self, client: tuple[TestClient, AdapterRegistryService]
    ) -> None:
        c, _ = client
        response = c.get(
            "/internal/v1/adapter_registry/opt-out",
            params=_params(),
        )
        assert response.status_code == 200
        assert response.json()["opted_out"] is False


class TestAdminListCandidates:
    def test_returns_all_tenants_for_admin(
        self, client: tuple[TestClient, AdapterRegistryService]
    ) -> None:
        c, _ = client
        c.post(
            "/internal/v1/adapter_registry/candidates",
            params=_params(),
            json=_submission(scheme="saas:slack"),
        )
        c.post(
            "/internal/v1/adapter_registry/candidates",
            params=_params(org_id="org_globex", user_id="usr_alice"),
            json=_submission(scheme="saas:notion"),
        )
        listed = c.get(
            "/internal/v1/adapter_registry/candidates",
            params=_params(org_id="org_platform", user_id="usr_admin"),
        )
        assert listed.status_code == 200, listed.text
        schemes = {row["scheme"] for row in listed.json()["candidates"]}
        assert schemes == {"saas:slack", "saas:notion"}
