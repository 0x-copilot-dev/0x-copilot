"""Tests for the PR B2 / 8.0.3f privacy settings routes."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.privacy.store import InMemoryPrivacySettingsStore


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    store.create_user(
        UserRecord(
            user_id="usr_sarah",
            org_id="org_acme",
            primary_email="sarah@acme.com",
            display_name="Sarah Chen",
            email_verified_at=datetime(2026, 1, 12, 9, 1, 24, tzinfo=timezone.utc),
        )
    )
    return store


def _client(
    *,
    identity_store: InMemoryIdentityStore | None = None,
    privacy_store: InMemoryPrivacySettingsStore | None = None,
) -> tuple[TestClient, InMemoryIdentityStore, InMemoryPrivacySettingsStore]:
    identity = identity_store or _seeded_identity()
    privacy = privacy_store or InMemoryPrivacySettingsStore()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        privacy_settings_store=privacy,
    )
    return TestClient(app), identity, privacy


def _params() -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": "usr_sarah"}


class TestGetPrivacySettings:
    def test_user_scope_hydrates_defaults(self) -> None:
        client, _i, _p = _client()
        response = client.get(
            "/internal/v1/policies/privacy",
            params={**_params(), "scope_user_id": "usr_sarah"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["scope"] == "user"
        assert body["user_id"] == "usr_sarah"
        assert body["training_opt_out"] is True
        assert body["region"] is None
        assert body["retention_days"] is None
        assert body["share_metadata"] is True
        assert body["memory_enabled"] is True

    def test_workspace_scope_hydrates_defaults(self) -> None:
        client, _i, _p = _client()
        response = client.get("/internal/v1/policies/privacy", params=_params())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["scope"] == "workspace"
        assert body["user_id"] is None

    def test_cross_user_scope_read_is_403(self) -> None:
        client, _i, _p = _client()
        response = client.get(
            "/internal/v1/policies/privacy",
            params={**_params(), "scope_user_id": "usr_someone_else"},
        )
        assert response.status_code == 403


class TestPutPrivacySettings:
    def test_partial_update_only_changes_specified_fields(self) -> None:
        client, identity, privacy = _client()
        response = client.put(
            "/internal/v1/policies/privacy",
            params={**_params(), "scope_user_id": "usr_sarah"},
            json={"memory_enabled": False, "retention_days": 30},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        # Specified fields applied.
        assert body["memory_enabled"] is False
        assert body["retention_days"] == 30
        # Unspecified fields fell through to defaults.
        assert body["training_opt_out"] is True
        assert body["share_metadata"] is True
        # One row written under the user scope.
        row = privacy.get_for_scope(org_id="org_acme", user_id="usr_sarah")
        assert row is not None
        assert row.memory_enabled is False
        assert row.retention_days == 30
        # Audit row.
        events = identity.list_identity_audit(org_id="org_acme")
        privacy_events = [e for e in events if e.action == "policy.privacy.update"]
        assert len(privacy_events) == 1
        meta = privacy_events[0].metadata or {}
        assert meta["scope"] == "usr_sarah"
        assert sorted(meta["diff_paths"]) == ["memory_enabled", "retention_days"]

    def test_region_round_trip(self) -> None:
        client, _i, _p = _client()
        response = client.put(
            "/internal/v1/policies/privacy",
            params={**_params(), "scope_user_id": "usr_sarah"},
            json={"region": "eu-west-1"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["region"] == "eu-west-1"

    def test_rejects_invalid_region(self) -> None:
        client, _i, _p = _client()
        response = client.put(
            "/internal/v1/policies/privacy",
            params={**_params(), "scope_user_id": "usr_sarah"},
            json={"region": "antarctica-1"},
        )
        assert response.status_code == 422

    def test_rejects_zero_retention_days(self) -> None:
        client, _i, _p = _client()
        response = client.put(
            "/internal/v1/policies/privacy",
            params={**_params(), "scope_user_id": "usr_sarah"},
            json={"retention_days": 0},
        )
        assert response.status_code == 422

    def test_workspace_scope_put_without_admin_scope_is_403(self) -> None:
        client, _i, _p = _client()
        response = client.put(
            "/internal/v1/policies/privacy",
            params=_params(),
            json={"memory_enabled": False},
        )
        assert response.status_code == 403


class TestUserOverrideCoexistsWithWorkspace:
    def test_separate_rows_per_scope(self) -> None:
        privacy = InMemoryPrivacySettingsStore()
        client, _i, _p = _client(privacy_store=privacy)
        # Seed a workspace default directly.
        from backend_app.privacy.store import (
            DataResidencyRegion,
            PrivacySettingsRow,
        )

        privacy.upsert(
            PrivacySettingsRow(
                org_id="org_acme",
                user_id=None,
                training_opt_out=False,
                region=DataResidencyRegion.US_EAST_1,
                retention_days=180,
                share_metadata=True,
                memory_enabled=True,
                updated_by_user_id="usr_admin",
            )
        )
        # User overrides retention_days only.
        response = client.put(
            "/internal/v1/policies/privacy",
            params={**_params(), "scope_user_id": "usr_sarah"},
            json={"retention_days": 30},
        )
        assert response.status_code == 200, response.text
        ws = privacy.get_for_scope(org_id="org_acme", user_id=None)
        usr = privacy.get_for_scope(org_id="org_acme", user_id="usr_sarah")
        assert ws is not None and ws.retention_days == 180
        assert usr is not None and usr.retention_days == 30
        # The user row inherited deployment defaults for fields the
        # caller didn't specify (workspace-row values are NOT cascaded
        # into a brand-new user-override row by this endpoint).
        assert usr.training_opt_out is True
