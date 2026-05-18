"""Tests for P8-A3 — per-user agent installs + overrides + duplicate (fork).

Coverage matches the slice's stated test goals:

  * Install idempotency (second install on a live row is a no-op).
  * Disable preserves overrides; uninstall drops them.
  * Re-enable after disable restores the prior overrides.
  * Fork creates a custom agent with ``origin="custom"`` and the
    fork's ``owner_user_id`` is the requesting user (never the body).
  * Override validation rejects instructions / skills /
    connectors_default edits and returns HTTP 422 with a
    fork-pointing hint.
  * Tenant isolation — a caller can't install / patch / fork an agent
    in another tenant via header forgery.

The tests assemble a minimal FastAPI app from
:func:`register_agent_install_routes` rather than going through
``backend_app.app.create_app``. The install slice is exclusively owned
by P8-A3 and shouldn't depend on P8-A1's catalog wiring landing first.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend_app.agents.installs import (
    ALLOWED_OVERRIDE_FIELDS,
    AgentCatalogRecord,
    FORK_REQUIRED_FIELDS,
    InMemoryAgentInstallStore,
    InMemoryAgentSource,
    OverridesValidationError,
    register_agent_install_routes,
    validate_overrides,
)
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    store.create_organization(
        OrganizationRecord(org_id="org_globex", display_name="Globex", slug="globex")
    )
    store.create_user(
        UserRecord(
            user_id="usr_sarah",
            org_id="org_acme",
            primary_email="sarah@acme.com",
            display_name="Sarah Chen",
            email_verified_at=datetime(2026, 1, 12, tzinfo=timezone.utc),
        )
    )
    store.create_user(
        UserRecord(
            user_id="usr_marcus",
            org_id="org_acme",
            primary_email="marcus@acme.com",
            display_name="Marcus Lee",
            email_verified_at=datetime(2026, 1, 12, tzinfo=timezone.utc),
        )
    )
    store.create_user(
        UserRecord(
            user_id="usr_bob",
            org_id="org_globex",
            primary_email="bob@globex.com",
            display_name="Bob Smith",
            email_verified_at=datetime(2026, 1, 12, tzinfo=timezone.utc),
        )
    )
    return store


def _seeded_source() -> InMemoryAgentSource:
    src = InMemoryAgentSource()
    # A system agent visible to both tenants — installs are scoped per
    # tenant_id at the row layer so reuse of the id is OK.
    for tenant in ("org_acme", "org_globex"):
        src.add(
            AgentCatalogRecord(
                id="agent_inbox_triage",
                tenant_id=tenant,
                name="Inbox Triage",
                slug="inbox-triage",
                description="Triage your inbox each morning.",
                icon_emoji="📥",
                color_hue=210,
                origin="system",
                owner_user_id=None,
                instructions="You triage emails.",
                model_id="anthropic:claude-sonnet-4-7-1m",
                reasoning_depth="balanced",
                skills=("skill_summarize",),
                connectors_default=("connector_gmail",),
                permissions={
                    "autonomy": "manual_approval",
                    "max_tool_calls_per_run": 20,
                    "max_output_tokens": 4000,
                    "read_only": False,
                },
            )
        )
    # Marcus owns a custom agent (visible only to him).
    src.add(
        AgentCatalogRecord(
            id="agent_marcus_drafts",
            tenant_id="org_acme",
            name="Marcus' Drafts",
            slug="marcus-drafts",
            description="",
            icon_emoji="✍️",
            color_hue=120,
            origin="custom",
            owner_user_id="usr_marcus",
            instructions="Draft like Marcus.",
            model_id="anthropic:claude-sonnet-4-7-1m",
            reasoning_depth="deep",
            skills=(),
            connectors_default=(),
            permissions={
                "autonomy": "auto_apply",
                "max_tool_calls_per_run": 10,
                "max_output_tokens": 2000,
                "read_only": False,
            },
        )
    )
    return src


def _client() -> tuple[
    TestClient,
    InMemoryAgentInstallStore,
    InMemoryAgentSource,
    InMemoryIdentityStore,
]:
    app = FastAPI()
    install_store = InMemoryAgentInstallStore()
    agent_source = _seeded_source()
    identity = _seeded_identity()
    register_agent_install_routes(
        app,
        install_store=install_store,
        agent_source=agent_source,
        identity_store=identity,
    )
    return TestClient(app), install_store, agent_source, identity


_PARAMS = {"org_id": "org_acme", "user_id": "usr_sarah"}


# ---------------------------------------------------------------------------
# Override validation (pure unit tests — no HTTP layer)
# ---------------------------------------------------------------------------


class TestValidateOverrides:
    def test_none_returns_none(self) -> None:
        assert validate_overrides(None) is None

    def test_empty_dict_returns_none(self) -> None:
        # An empty object normalizes to "no overrides" so the store
        # writes a NULL column.
        assert validate_overrides({}) is None

    def test_model_default_passes(self) -> None:
        out = validate_overrides(
            {"model_default": {"model_id": "openai:gpt-5", "reasoning_depth": "fast"}}
        )
        assert out == {
            "model_default": {"model_id": "openai:gpt-5", "reasoning_depth": "fast"}
        }

    def test_permissions_partial_passes(self) -> None:
        # Per PRD §3.3: "Permissions merge field-wise (not all-or-
        # nothing)" — a subset is valid.
        out = validate_overrides({"permissions": {"autonomy": "manual_approval"}})
        assert out == {"permissions": {"autonomy": "manual_approval"}}

    def test_both_allowlisted_fields_pass(self) -> None:
        out = validate_overrides(
            {
                "model_default": {
                    "model_id": "openai:gpt-5",
                    "reasoning_depth": "deep",
                },
                "permissions": {"max_tool_calls_per_run": 5, "read_only": True},
            }
        )
        assert out is not None
        assert set(out.keys()) == ALLOWED_OVERRIDE_FIELDS

    @pytest.mark.parametrize("field", sorted(FORK_REQUIRED_FIELDS))
    def test_instructions_skills_connectors_force_fork(self, field: str) -> None:
        # Each of the fork-required fields is rejected with the
        # forbidden_field echoed back so the FE can render a precise
        # error message.
        with pytest.raises(OverridesValidationError) as excinfo:
            validate_overrides({field: "anything"})
        assert excinfo.value.forbidden_field == field
        # The hint MUST point at /duplicate so the UI can route there.
        assert "duplicate" in excinfo.value.hint.lower()

    def test_unknown_top_level_field_rejected(self) -> None:
        with pytest.raises(OverridesValidationError) as excinfo:
            validate_overrides({"icon_emoji": "🤖"})
        assert excinfo.value.forbidden_field == "icon_emoji"

    def test_unknown_permissions_subfield_rejected(self) -> None:
        with pytest.raises(OverridesValidationError) as excinfo:
            validate_overrides({"permissions": {"bogus": True}})
        assert excinfo.value.forbidden_field.startswith("permissions.")

    def test_unknown_model_default_subfield_rejected(self) -> None:
        with pytest.raises(OverridesValidationError) as excinfo:
            validate_overrides({"model_default": {"model_id": "x", "temperature": 0.7}})
        assert excinfo.value.forbidden_field.startswith("model_default.")

    def test_non_object_overrides_rejected(self) -> None:
        with pytest.raises(OverridesValidationError):
            validate_overrides("just-a-string")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Install / idempotency / re-enable
# ---------------------------------------------------------------------------


class TestInstallIdempotency:
    def test_first_install_creates_row_and_audit(self) -> None:
        client, store, _src, identity = _client()
        response = client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["agent_id"] == "agent_inbox_triage"
        assert body["user_id"] == "usr_sarah"
        assert body["uninstalled_at"] is None
        assert body["overrides"] is None

        rows = store.list_for_user(tenant_id="org_acme", user_id="usr_sarah")
        assert len(rows) == 1
        assert rows[0].agent_id == "agent_inbox_triage"

        events = identity.list_identity_audit(org_id="org_acme")
        installs = [e for e in events if e.action == "agent.install"]
        assert len(installs) == 1
        assert installs[0].metadata["agent_id"] == "agent_inbox_triage"

    def test_second_install_is_idempotent_no_op(self) -> None:
        # Per PRD §4.5: "Idempotent — second install is a no-op (HTTP
        # 200 with current row)." No new audit row should land.
        client, store, _src, identity = _client()
        first = client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={},
        )
        assert first.status_code == 200
        second = client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={},
        )
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]

        events = identity.list_identity_audit(org_id="org_acme")
        installs = [e for e in events if e.action == "agent.install"]
        assert len(installs) == 1  # second install did NOT re-audit

        # Store still holds exactly one live row.
        rows = store.list_for_user(tenant_id="org_acme", user_id="usr_sarah")
        assert len(rows) == 1

    def test_install_with_overrides_writes_them(self) -> None:
        client, store, _src, _identity = _client()
        response = client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={
                "overrides": {
                    "permissions": {"autonomy": "manual_approval", "read_only": True}
                }
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["overrides"] == {
            "permissions": {"autonomy": "manual_approval", "read_only": True}
        }
        row = store.get(
            tenant_id="org_acme",
            agent_id="agent_inbox_triage",
            user_id="usr_sarah",
        )
        assert row is not None
        assert row.overrides == {
            "permissions": {"autonomy": "manual_approval", "read_only": True}
        }

    def test_install_on_invisible_agent_is_404(self) -> None:
        # Sarah cannot see Marcus' custom agent (not owner) — install
        # must come back as 404, not 403.
        client, _store, _src, _identity = _client()
        response = client.post(
            "/internal/v1/agents/agent_marcus_drafts/install",
            params=_PARAMS,
            json={},
        )
        assert response.status_code == 404

    def test_install_unknown_agent_is_404(self) -> None:
        client, _store, _src, _identity = _client()
        response = client.post(
            "/internal/v1/agents/agent_does_not_exist/install",
            params=_PARAMS,
            json={},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Override allowlist enforcement at the route layer
# ---------------------------------------------------------------------------


class TestOverrideAllowlist:
    def test_install_with_instructions_override_is_422(self) -> None:
        client, store, _src, _identity = _client()
        response = client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={"overrides": {"instructions": "Be sarcastic."}},
        )
        assert response.status_code == 422, response.text
        body = response.json()
        # The error body MUST point at the exact field + the
        # /duplicate escape hatch — the FE renders the hint inline.
        assert body["detail"]["forbidden_field"] == "instructions"
        assert "duplicate" in body["detail"]["hint"].lower()
        # No row should have landed in the store on a 422 path.
        assert (
            store.get(
                tenant_id="org_acme",
                agent_id="agent_inbox_triage",
                user_id="usr_sarah",
            )
            is None
        )

    def test_install_with_skills_override_is_422(self) -> None:
        client, _store, _src, _identity = _client()
        response = client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={"overrides": {"skills": ["skill_summarize", "skill_calendar"]}},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["forbidden_field"] == "skills"

    def test_install_with_connectors_override_is_422(self) -> None:
        client, _store, _src, _identity = _client()
        response = client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={"overrides": {"connectors_default": ["connector_gmail"]}},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["forbidden_field"] == "connectors_default"

    def test_patch_install_with_instructions_override_is_422(self) -> None:
        # Same allowlist applies to PATCH — the route reuses
        # validate_overrides().
        client, _store, _src, _identity = _client()
        client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={},
        )
        response = client.patch(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={"overrides": {"instructions": "be terse"}},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["forbidden_field"] == "instructions"

    def test_patch_install_with_allowlisted_overrides_succeeds(self) -> None:
        client, _store, _src, identity = _client()
        client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={},
        )
        response = client.patch(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={
                "overrides": {
                    "model_default": {
                        "model_id": "openai:gpt-5",
                        "reasoning_depth": "fast",
                    }
                }
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["overrides"]["model_default"]["model_id"] == "openai:gpt-5"

        events = identity.list_identity_audit(org_id="org_acme")
        updates = [e for e in events if e.action == "agent.override_update"]
        assert len(updates) == 1

    def test_patch_install_clears_overrides_with_null(self) -> None:
        # A patch carrying ``overrides: null`` clears the column.
        client, store, _src, _identity = _client()
        client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={"overrides": {"permissions": {"autonomy": "manual_approval"}}},
        )
        response = client.patch(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={"overrides": None},
        )
        assert response.status_code == 200
        row = store.get(
            tenant_id="org_acme",
            agent_id="agent_inbox_triage",
            user_id="usr_sarah",
        )
        assert row is not None and row.overrides is None

    def test_patch_install_no_install_is_404(self) -> None:
        client, _store, _src, _identity = _client()
        response = client.patch(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={"overrides": {}},
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Disable + uninstall (the tombstone shapes)
# ---------------------------------------------------------------------------


class TestDisableAndUninstall:
    def test_disable_preserves_overrides_and_stamps_tombstone(self) -> None:
        client, store, _src, identity = _client()
        client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={
                "overrides": {
                    "permissions": {"autonomy": "manual_approval", "read_only": True}
                }
            },
        )
        response = client.post(
            "/internal/v1/agents/agent_inbox_triage/disable",
            params=_PARAMS,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["uninstalled_at"] is not None
        # Disable PRESERVES overrides so re-enable can restore them.
        assert body["overrides"] == {
            "permissions": {"autonomy": "manual_approval", "read_only": True}
        }

        row = store.get(
            tenant_id="org_acme",
            agent_id="agent_inbox_triage",
            user_id="usr_sarah",
            include_tombstoned=True,
        )
        assert row is not None
        assert row.uninstalled_at is not None
        assert row.overrides is not None

        events = identity.list_identity_audit(org_id="org_acme")
        disables = [e for e in events if e.action == "agent.disable"]
        assert len(disables) == 1
        assert disables[0].metadata["preserved_overrides"] is True

    def test_uninstall_drops_overrides_and_stamps_tombstone(self) -> None:
        client, store, _src, identity = _client()
        client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={"overrides": {"permissions": {"autonomy": "manual_approval"}}},
        )
        response = client.post(
            "/internal/v1/agents/agent_inbox_triage/uninstall",
            params=_PARAMS,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["uninstalled_at"] is not None
        # Uninstall DROPS overrides — re-install begins from clean.
        assert body["overrides"] is None

        row = store.get(
            tenant_id="org_acme",
            agent_id="agent_inbox_triage",
            user_id="usr_sarah",
            include_tombstoned=True,
        )
        assert row is not None
        assert row.uninstalled_at is not None
        assert row.overrides is None

        events = identity.list_identity_audit(org_id="org_acme")
        uninstalls = [e for e in events if e.action == "agent.uninstall"]
        assert len(uninstalls) == 1

    def test_disable_then_install_restores_overrides(self) -> None:
        # The disable→install round trip is the user-visible "pause"
        # contract: the user's tweaks come back when they re-enable.
        client, store, _src, identity = _client()
        client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={"overrides": {"permissions": {"autonomy": "manual_approval"}}},
        )
        client.post("/internal/v1/agents/agent_inbox_triage/disable", params=_PARAMS)
        # Re-install without an explicit overrides payload — the row
        # should be revived and the prior overrides preserved.
        response = client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={},
        )
        assert response.status_code == 200
        assert response.json()["overrides"] == {
            "permissions": {"autonomy": "manual_approval"}
        }
        row = store.get(
            tenant_id="org_acme",
            agent_id="agent_inbox_triage",
            user_id="usr_sarah",
        )
        assert row is not None
        assert row.uninstalled_at is None

        events = identity.list_identity_audit(org_id="org_acme")
        reinstalls = [e for e in events if e.action == "agent.reinstall"]
        assert len(reinstalls) == 1

    def test_uninstall_then_install_starts_clean(self) -> None:
        client, _store, _src, _identity = _client()
        client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={"overrides": {"permissions": {"autonomy": "manual_approval"}}},
        )
        client.post("/internal/v1/agents/agent_inbox_triage/uninstall", params=_PARAMS)
        response = client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={},
        )
        assert response.status_code == 200
        # Overrides were dropped on uninstall — re-install must be clean.
        assert response.json()["overrides"] is None

    def test_disable_without_install_is_404(self) -> None:
        client, _store, _src, _identity = _client()
        response = client.post(
            "/internal/v1/agents/agent_inbox_triage/disable", params=_PARAMS
        )
        assert response.status_code == 404

    def test_uninstall_without_install_is_404(self) -> None:
        client, _store, _src, _identity = _client()
        response = client.post(
            "/internal/v1/agents/agent_inbox_triage/uninstall", params=_PARAMS
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Duplicate (fork)
# ---------------------------------------------------------------------------


class TestDuplicate:
    def test_duplicate_creates_custom_owned_by_caller(self) -> None:
        client, _store, src, identity = _client()
        response = client.post(
            "/internal/v1/agents/agent_inbox_triage/duplicate",
            params=_PARAMS,
            json={},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        new_id = body["new_agent_id"]
        assert body["source_agent_id"] == "agent_inbox_triage"
        assert body["source_version"] == 1

        # The new row exists in the catalog with origin="custom" and is
        # owned by the requesting user (never the body).
        clone = src.get_agent(
            tenant_id="org_acme", agent_id=new_id, as_user_id="usr_sarah"
        )
        assert clone is not None
        assert clone.origin == "custom"
        assert clone.owner_user_id == "usr_sarah"
        assert clone.forked_from_agent_id == "agent_inbox_triage"

        events = identity.list_identity_audit(org_id="org_acme")
        dupes = [e for e in events if e.action == "agent.duplicate"]
        assert len(dupes) == 1
        assert dupes[0].metadata["source_agent_id"] == "agent_inbox_triage"
        assert dupes[0].metadata["new_agent_id"] == new_id

    def test_duplicate_uses_caller_name_when_provided(self) -> None:
        client, _store, src, _identity = _client()
        response = client.post(
            "/internal/v1/agents/agent_inbox_triage/duplicate",
            params=_PARAMS,
            json={"name": "Sarah's Triage"},
        )
        assert response.status_code == 201
        new_id = response.json()["new_agent_id"]
        clone = src.get_agent(
            tenant_id="org_acme", agent_id=new_id, as_user_id="usr_sarah"
        )
        assert clone is not None
        assert clone.name == "Sarah's Triage"

    def test_duplicate_invisible_source_is_404(self) -> None:
        # Sarah cannot see Marcus' custom agent — fork must 404.
        client, _store, _src, _identity = _client()
        response = client.post(
            "/internal/v1/agents/agent_marcus_drafts/duplicate",
            params=_PARAMS,
            json={},
        )
        assert response.status_code == 404

    def test_duplicate_owner_is_caller_not_body(self) -> None:
        # The body cannot redirect ownership. We don't expose an
        # ``owner_user_id`` field on the wire shape at all — extra
        # fields are rejected by the Pydantic model.
        client, _store, _src, _identity = _client()
        response = client.post(
            "/internal/v1/agents/agent_inbox_triage/duplicate",
            params=_PARAMS,
            json={"owner_user_id": "usr_marcus"},
        )
        # Extra fields → 422 from FastAPI's pydantic validator.
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_install_does_not_leak_across_tenants(self) -> None:
        # Sarah (org_acme) installs the system agent. Bob (org_globex)
        # querying the SAME agent_id sees no install of his own.
        client, store, _src, _identity = _client()
        client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={},
        )
        # Bob in a different tenant — still sees zero installs.
        assert store.list_for_user(tenant_id="org_globex", user_id="usr_bob") == ()
        bob_row = store.get(
            tenant_id="org_globex",
            agent_id="agent_inbox_triage",
            user_id="usr_bob",
        )
        assert bob_row is None

    def test_disable_in_one_tenant_doesnt_affect_other(self) -> None:
        client, store, _src, _identity = _client()
        client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params=_PARAMS,
            json={},
        )
        # Bob installs in his own tenant.
        client.post(
            "/internal/v1/agents/agent_inbox_triage/install",
            params={"org_id": "org_globex", "user_id": "usr_bob"},
            json={},
        )
        # Sarah disables her install.
        client.post("/internal/v1/agents/agent_inbox_triage/disable", params=_PARAMS)
        # Bob's install is unaffected.
        bob_row = store.get(
            tenant_id="org_globex",
            agent_id="agent_inbox_triage",
            user_id="usr_bob",
        )
        assert bob_row is not None and bob_row.uninstalled_at is None
