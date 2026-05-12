"""PR 4.3 — workspace behavior overrides + retention/effective + workspace
data lifecycle stubs.

Mirrors the depth and shape of ``test_workspace_defaults_lifecycle.py``
(PR 1.6) but scoped to PR 4.3's deltas:

  * GET /v1/agent/workspace/defaults — round-trips ``behavior_overrides``.
  * PUT /v1/agent/workspace/defaults — extends the existing PR 1.6 PUT
    with the optional ``behavior_overrides`` block; admin-gate unchanged.
  * Audit emission — ``workspace.behavior_overrides.update`` fires when
    the block changes; ``workspace.training_opt_out.update`` fires only
    on the boolean transition.
  * GET /v1/retention/effective — open to any tenant member; matches the
    ``RetentionPolicyResolver`` the sweeper uses.
  * POST /v1/agent/workspace/export — admin-only; 202 + audit row.
  * DELETE /v1/agent/workspace/data — admin-only; always 501;
    ``typed_confirmation_correct`` recorded in audit either way.

Provider-specific training-opt-out kwargs are unit-tested independently
in ``test_provider_kwargs.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from enterprise_service_contracts.scopes import (
    ADMIN_RETENTION,
    ADMIN_USERS,
    RUNTIME_USE,
)
from fastapi.testclient import TestClient

from agent_runtime.api.constants import Messages
from agent_runtime.persistence.records.retention import (
    RetentionKind,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory


_SERVICE_TOKEN = "test-service-token"


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _SERVICE_TOKEN)
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.setenv("RBAC_MODE", "enforce")
    yield


class BehaviorOverridesFixtureMixin:
    class Values:
        ORG_ID = "org_pr43"
        USER_ID = "user_pr43"
        DEFAULT_MODEL_NAME = "gpt-5.4-mini"

    def create_client(self) -> tuple[TestClient, InMemoryRuntimeApiStore]:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": self.Values.DEFAULT_MODEL_NAME,
            }
        )
        ports = RuntimeAdapterFactory.from_store(store)
        app = RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
        return TestClient(app), store

    def _headers(
        self,
        *,
        permission_scopes: tuple[str, ...] = (RUNTIME_USE,),
    ) -> dict[str, str]:
        return {
            "x-enterprise-service-token": _SERVICE_TOKEN,
            "x-enterprise-org-id": self.Values.ORG_ID,
            "x-enterprise-user-id": self.Values.USER_ID,
            "x-enterprise-roles": "employee",
            "x-enterprise-permission-scopes": ",".join(permission_scopes),
            "x-enterprise-connector-scopes": "{}",
        }

    def _put_payload(
        self,
        *,
        retention_days: int = 365,
        behavior_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "default_model": {
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
            },
            "default_connectors": {},
            "retention_days": retention_days,
        }
        if behavior_overrides is not None:
            payload["behavior_overrides"] = behavior_overrides
        return payload


class TestBehaviorOverridesRoundTrip(BehaviorOverridesFixtureMixin):
    def test_get_returns_default_overrides_when_empty(self) -> None:
        client, _ = self.create_client()
        response = client.get("/v1/agent/workspace/defaults", headers=self._headers())
        assert response.status_code == 200, response.text
        body = response.json()
        # Default-shape: every override field present, all None except
        # ``training_data_opt_out=False``. The FE renders absence of a
        # value with the same semantics as None, so this is the
        # ergonomic shape (FastAPI's default response serialisation).
        overrides = body["behavior_overrides"]
        assert overrides["training_data_opt_out"] is False
        assert overrides["system_prompt_override"] is None
        assert overrides["temperature"] is None
        assert overrides["citation_density"] is None
        assert overrides["refusal_behavior"] is None
        assert overrides["default_reasoning_effort"] is None

    def test_put_round_trips_full_overrides(self) -> None:
        client, _ = self.create_client()
        overrides = {
            "system_prompt_override": "Sign off as the GTM team.",
            "temperature": 0.4,
            "citation_density": "thorough",
            "refusal_behavior": "strict",
            "default_reasoning_effort": "high",
            "training_data_opt_out": True,
        }
        put = client.put(
            "/v1/agent/workspace/defaults",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json=self._put_payload(behavior_overrides=overrides),
        )
        assert put.status_code == 200, put.text
        get = client.get("/v1/agent/workspace/defaults", headers=self._headers())
        body = get.json()
        # Every key we set round-trips intact (FastAPI's serialiser may
        # surface extra ``None`` fields for keys we omitted; we only
        # assert the values we set).
        for key, value in overrides.items():
            assert body["behavior_overrides"][key] == value, (key, value)

    def test_put_rejects_unknown_override_key(self) -> None:
        client, _ = self.create_client()
        response = client.put(
            "/v1/agent/workspace/defaults",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json=self._put_payload(behavior_overrides={"unknown_knob": True}),
        )
        # The runtime API's typed-error handler maps Pydantic
        # validation failures to 400 (see ``RuntimeApiErrorMapper``).
        # Either 400 or 422 is acceptable; we accept both for forward
        # compatibility with FastAPI body-validation changes.
        assert response.status_code in {400, 422}, response.text

    def test_put_rejects_temperature_outside_range(self) -> None:
        client, _ = self.create_client()
        response = client.put(
            "/v1/agent/workspace/defaults",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json=self._put_payload(behavior_overrides={"temperature": 1.5}),
        )
        assert response.status_code in {400, 422}, response.text


class TestBehaviorOverridesAuditEmission(BehaviorOverridesFixtureMixin):
    def test_overrides_change_emits_dedicated_audit_row(self) -> None:
        client, store = self.create_client()
        # First PUT — overrides change from empty to populated.
        put = client.put(
            "/v1/agent/workspace/defaults",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json=self._put_payload(
                behavior_overrides={"citation_density": "thorough"},
            ),
        )
        assert put.status_code == 200, put.text
        actions = [event_type for event_type, _ in store.audit_log]
        assert Messages.Audit.WORKSPACE_BEHAVIOR_OVERRIDES_UPDATE in actions, actions
        # The PR 1.6 row also fires (the broader defaults diff).
        assert Messages.Audit.WORKSPACE_DEFAULTS_UPDATE in actions

    def test_training_opt_out_transition_emits_dedicated_audit(self) -> None:
        client, store = self.create_client()
        # First PUT — training opt-out off → on.
        client.put(
            "/v1/agent/workspace/defaults",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json=self._put_payload(
                behavior_overrides={"training_data_opt_out": True},
            ),
        )
        opt_out_rows = [
            record
            for event_type, record in store.audit_log
            if event_type == Messages.Audit.WORKSPACE_TRAINING_OPT_OUT_UPDATE
        ]
        assert len(opt_out_rows) == 1, opt_out_rows
        assert opt_out_rows[0]["metadata"] == {
            "before": False,
            "after": True,
        }

    def test_training_opt_out_no_transition_does_not_audit(self) -> None:
        client, store = self.create_client()
        # First PUT seeds the row with opt-out=True.
        client.put(
            "/v1/agent/workspace/defaults",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json=self._put_payload(
                behavior_overrides={"training_data_opt_out": True},
            ),
        )
        # Second PUT keeps opt-out=True but tweaks an unrelated knob.
        # The dedicated training-opt-out row must NOT fire again.
        client.put(
            "/v1/agent/workspace/defaults",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json=self._put_payload(
                behavior_overrides={
                    "training_data_opt_out": True,
                    "citation_density": "minimal",
                },
            ),
        )
        opt_out_rows = [
            record
            for event_type, record in store.audit_log
            if event_type == Messages.Audit.WORKSPACE_TRAINING_OPT_OUT_UPDATE
        ]
        assert len(opt_out_rows) == 1, opt_out_rows


class TestRetentionEffectiveRoute(BehaviorOverridesFixtureMixin):
    def test_member_can_read_effective_view(self) -> None:
        client, _ = self.create_client()
        response = client.get("/v1/retention/effective", headers=self._headers())
        assert response.status_code == 200, response.text
        body = response.json()
        # Every supported kind appears.
        assert set(body["effective"].keys()) == {kind.value for kind in RetentionKind}
        # Messages defaults to deployment SaaS floor (365d).
        messages = body["effective"]["messages"]
        # Source scope is None when the deployment default applies (no
        # per-tenant policy yet).
        assert messages["source_scope"] is None
        assert messages["source_policy_id"] is None

    def test_org_policy_overrides_show_org_scope(self) -> None:
        client, store = self.create_client()
        # Seed a 30-day org-scope retention via the admin route.
        admin_headers = self._headers(
            permission_scopes=(RUNTIME_USE, ADMIN_RETENTION),
        )
        for kind in (RetentionKind.MESSAGES, RetentionKind.EVENTS):
            response = client.post(
                "/v1/retention/policies",
                headers=admin_headers,
                json={
                    "scope": "org",
                    "kind": kind.value,
                    "ttl_seconds": 30 * 24 * 60 * 60,
                },
            )
            assert response.status_code == 200, response.text
        # Member reads the effective view.
        view = client.get("/v1/retention/effective", headers=self._headers())
        assert view.status_code == 200, view.text
        effective = view.json()["effective"]
        assert effective["messages"]["source_scope"] == "org"
        assert effective["messages"]["ttl_seconds"] == 30 * 24 * 60 * 60
        assert effective["messages"]["source_policy_id"] is not None


class TestWorkspaceDataStubs(BehaviorOverridesFixtureMixin):
    def test_export_requires_admin(self) -> None:
        client, _ = self.create_client()
        response = client.post(
            "/v1/agent/workspace/export",
            headers=self._headers(),
            json={"scope": "workspace"},
        )
        assert response.status_code == 403, response.text

    def test_export_returns_202_and_audits(self) -> None:
        client, store = self.create_client()
        response = client.post(
            "/v1/agent/workspace/export",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json={"scope": "workspace"},
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "queued"
        assert body["export_id"].startswith("exp_")
        rows = [
            record
            for event_type, record in store.audit_log
            if event_type == Messages.Audit.WORKSPACE_EXPORT_REQUEST
        ]
        assert len(rows) == 1, rows
        assert rows[0]["metadata"]["scope"] == "workspace"

    def test_export_rejects_unknown_scope(self) -> None:
        client, _ = self.create_client()
        response = client.post(
            "/v1/agent/workspace/export",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json={"scope": "user"},
        )
        # Either 400 (runtime API typed-error mapper) or 422 (FastAPI
        # default body validation) is acceptable.
        assert response.status_code in {400, 422}, response.text

    def test_delete_all_returns_501_and_audits_correctness(self) -> None:
        client, store = self.create_client()
        # Wrong slug — typed_confirmation_correct=False.
        wrong = client.delete(
            "/v1/agent/workspace/data?confirm_slug=not-the-org",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
        )
        assert wrong.status_code == 501, wrong.text
        # Right slug — typed_confirmation_correct=True (still 501).
        right = client.delete(
            f"/v1/agent/workspace/data?confirm_slug={self.Values.ORG_ID}",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
        )
        assert right.status_code == 501, right.text
        rows = [
            record
            for event_type, record in store.audit_log
            if event_type == Messages.Audit.WORKSPACE_DELETE_ATTEMPT
        ]
        assert len(rows) == 2, rows
        correctness = [row["metadata"]["typed_confirmation_correct"] for row in rows]
        assert sorted(correctness) == [False, True]

    def test_delete_all_requires_admin(self) -> None:
        client, _ = self.create_client()
        response = client.delete(
            "/v1/agent/workspace/data?confirm_slug=anything",
            headers=self._headers(),
        )
        assert response.status_code == 403, response.text
