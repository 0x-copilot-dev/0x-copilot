"""Tests for the PR B1 / 8.0.3d tool-use policy routes.

Both endpoints are caller-scoped — identity comes from the
``x-enterprise-org-id`` / ``x-enterprise-user-id`` headers when the
service token is configured, or from the dev-fallback query params
otherwise. The TestClient runs with ``ENTERPRISE_SERVICE_TOKEN`` unset
so the dev path applies; same setup the existing me/preferences tests
use.

Coverage:

* GET hydrates deployment defaults when no row exists for the scope.
* PUT round-trips three axes atomically (read/write/destructive).
* Cross-user reads are refused (403) — no leaking another user's
  policy through a shared route.
* Audit row lands once per privileged write into
  ``identity_audit_events``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.policies.store import InMemoryToolUsePolicyStore


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
    policy_store: InMemoryToolUsePolicyStore | None = None,
) -> tuple[TestClient, InMemoryIdentityStore, InMemoryToolUsePolicyStore]:
    identity = identity_store or _seeded_identity()
    policy = policy_store or InMemoryToolUsePolicyStore()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        tool_use_policy_store=policy,
    )
    return TestClient(app), identity, policy


def _params() -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": "usr_sarah"}


class TestGetToolUsePolicy:
    def test_hydrates_deployment_defaults_when_no_rows(self) -> None:
        client, _i, _p = _client()
        response = client.get(
            "/internal/v1/policies/tool-use",
            params={**_params(), "scope_user_id": "usr_sarah"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["scope"] == "user"
        assert body["user_id"] == "usr_sarah"
        # Default: read=auto, write=ask, destructive=require.
        modes = {entry["kind"]: entry["mode"] for entry in body["policies"]}
        assert modes == {
            "read": "auto",
            "write": "ask",
            "destructive": "require",
        }

    def test_workspace_scope_read_returns_workspace_label(self) -> None:
        client, _i, _p = _client()
        response = client.get(
            "/internal/v1/policies/tool-use",
            params=_params(),  # no scope_user_id => workspace default
        )
        assert response.status_code == 200, response.text
        assert response.json()["scope"] == "workspace"

    def test_cross_user_scope_read_is_403(self) -> None:
        client, _i, _p = _client()
        response = client.get(
            "/internal/v1/policies/tool-use",
            params={**_params(), "scope_user_id": "usr_someone_else"},
        )
        assert response.status_code == 403


class TestPutToolUsePolicy:
    def test_user_scope_replaces_three_axes_atomically(self) -> None:
        client, identity, policy = _client()
        response = client.put(
            "/internal/v1/policies/tool-use",
            params={**_params(), "scope_user_id": "usr_sarah"},
            json={
                "policies": [
                    {"kind": "read", "mode": "auto"},
                    {"kind": "write", "mode": "require"},
                    {"kind": "destructive", "mode": "block"},
                ]
            },
        )
        assert response.status_code == 200, response.text
        modes = {entry["kind"]: entry["mode"] for entry in response.json()["policies"]}
        assert modes["write"] == "require"
        assert modes["destructive"] == "block"
        # Three rows in the store (one per kind) under the user scope.
        rows = policy.list_for_scope(org_id="org_acme", user_id="usr_sarah")
        assert {row.kind.value for row in rows} == {
            "read",
            "write",
            "destructive",
        }
        # Audit row landed.
        events = identity.list_identity_audit(org_id="org_acme")
        policy_events = [e for e in events if e.action == "policy.tool_use.update"]
        assert len(policy_events) == 1
        meta = policy_events[0].metadata or {}
        assert meta["scope"] == "usr_sarah"
        assert meta["after"]["destructive"] == "block"

    def test_rejects_duplicate_kind_in_payload(self) -> None:
        client, _i, _p = _client()
        response = client.put(
            "/internal/v1/policies/tool-use",
            params={**_params(), "scope_user_id": "usr_sarah"},
            json={
                "policies": [
                    {"kind": "read", "mode": "auto"},
                    {"kind": "read", "mode": "block"},
                ]
            },
        )
        assert response.status_code == 422

    def test_rejects_invalid_mode(self) -> None:
        client, _i, _p = _client()
        response = client.put(
            "/internal/v1/policies/tool-use",
            params={**_params(), "scope_user_id": "usr_sarah"},
            json={
                "policies": [
                    {"kind": "read", "mode": "bogus"},
                ]
            },
        )
        assert response.status_code == 422

    def test_workspace_scope_put_without_admin_scope_is_403(self) -> None:
        # Caller has only RUNTIME_USE in the dev path; workspace-default
        # writes require ADMIN_USERS so the dev caller is rejected.
        client, _i, _p = _client()
        response = client.put(
            "/internal/v1/policies/tool-use",
            params=_params(),  # no scope_user_id => workspace
            json={
                "policies": [
                    {"kind": "read", "mode": "auto"},
                ]
            },
        )
        assert response.status_code == 403


class TestUserOverrideTakesPrecedenceOverWorkspace:
    def test_user_row_persists_alongside_workspace_row(self) -> None:
        # The store keys on (org, scope, kind) so a user override and
        # the workspace default coexist independently. The evaluator
        # (separate PR) is what picks the user row when present.
        policy = InMemoryToolUsePolicyStore()
        client, _i, _p = _client(policy_store=policy)
        # Seed a workspace default directly on the store (the dev caller
        # can't write workspace via the route — see test above — but
        # the AI backend's evaluator reads both rows so we test layout).
        from backend_app.policies.store import (
            ToolUsePolicyKind,
            ToolUsePolicyMode,
            ToolUsePolicyRow,
        )

        policy.upsert(
            ToolUsePolicyRow(
                org_id="org_acme",
                user_id=None,
                kind=ToolUsePolicyKind.DESTRUCTIVE,
                mode=ToolUsePolicyMode.REQUIRE,
                updated_by_user_id="usr_admin",
            )
        )
        # User overrides destructive to block.
        response = client.put(
            "/internal/v1/policies/tool-use",
            params={**_params(), "scope_user_id": "usr_sarah"},
            json={
                "policies": [
                    {"kind": "destructive", "mode": "block"},
                ]
            },
        )
        assert response.status_code == 200, response.text
        # Both rows present, distinct scopes.
        ws_rows = policy.list_for_scope(org_id="org_acme", user_id=None)
        user_rows = policy.list_for_scope(org_id="org_acme", user_id="usr_sarah")
        ws_modes = {row.kind.value: row.mode.value for row in ws_rows}
        user_modes = {row.kind.value: row.mode.value for row in user_rows}
        assert ws_modes["destructive"] == "require"
        assert user_modes["destructive"] == "block"
