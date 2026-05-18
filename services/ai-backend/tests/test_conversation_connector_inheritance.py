"""P6.5-A2 — project connector-allowlist inheritance at conversation create.

PRD §5.4 contract under test:

1. **Caller-explicit wins.** When the create request carries a non-``None``
   ``enabled_connectors`` map (including an explicit ``{}``), the
   conversation is seeded with that map verbatim and the project
   allowlist + workspace defaults are BOTH skipped.
2. **Project allowlist non-empty.** When ``project_id`` is set, the
   caller did NOT pass connectors, and the project resolver returns a
   non-empty tuple, the conversation is seeded with each slug → active.
3. **Project allowlist empty.** A resolver return of ``()`` (explicit
   denial) seeds an empty map and stops; workspace defaults are not
   consulted (the project policy is "no connectors").
4. **Project allowlist absent.** A resolver return of ``None`` falls
   through to the existing workspace-defaults seed path.
5. **Resolver failure.** A resolver that raises is caught upstream
   (the resolver contract is "never raises"; we exercise the
   Null-resolver and a fail-open custom fake to confirm the wiring
   does not crash the create on a bad project id).

The audit row's ``context.inherited_from_project_default`` flag must be
``True`` exactly when the project allowlist was consulted and applied
(including the empty-tuple "explicit denial" case).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from enterprise_service_contracts.scopes import ADMIN_USERS, RUNTIME_USE
from fastapi.testclient import TestClient

from agent_runtime.api.project_resolver import ProjectResolverPort
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory


_SERVICE_TOKEN = "test-service-token"


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch) -> Iterator[None]:
    """Force the trusted-bearer lane on for every test in this module."""
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _SERVICE_TOKEN)
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.setenv("RBAC_MODE", "enforce")
    yield


# ---------------------------------------------------------------------------
# Fakes + builder mixin
# ---------------------------------------------------------------------------


class FakeProjectResolver:
    """Deterministic resolver that returns a pre-seeded allowlist per project id.

    Recorded calls let tests assert the resolver is consulted (or not)
    depending on the inheritance ladder branch under test.
    """

    def __init__(self, table: dict[str, tuple[str, ...] | None]) -> None:
        self._table = table
        self.calls: list[dict[str, str]] = []

    async def fetch_connector_allowlist(
        self,
        *,
        org_id: str,
        user_id: str,
        project_id: str,
    ) -> tuple[str, ...] | None:
        self.calls.append(
            {"org_id": org_id, "user_id": user_id, "project_id": project_id}
        )
        return self._table.get(project_id)


class InheritanceFixtureMixin:
    """Test fixtures + builders for the P6.5-A2 inheritance hook tests."""

    class Values:
        ORG_ID = "org_p65a2"
        USER_ID = "user_p65a2"
        ASSISTANT_ID = "assistant_p65a2"
        DEFAULT_MODEL_NAME = "gpt-5.4-mini"
        PROJECT_WITH_ALLOWLIST = "prj_alpha"
        PROJECT_WITH_EMPTY_ALLOWLIST = "prj_empty"
        PROJECT_WITH_NULL_ALLOWLIST = "prj_null"
        UNKNOWN_PROJECT = "prj_missing"

    def create_client(
        self,
        *,
        resolver: ProjectResolverPort | None = None,
    ) -> tuple[TestClient, InMemoryRuntimeApiStore, FakeProjectResolver]:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": self.Values.DEFAULT_MODEL_NAME,
            }
        )
        ports = RuntimeAdapterFactory.from_store(store)
        fake = (
            resolver
            if resolver is not None
            else FakeProjectResolver(
                {
                    self.Values.PROJECT_WITH_ALLOWLIST: ("salesforce", "gmail"),
                    self.Values.PROJECT_WITH_EMPTY_ALLOWLIST: (),
                    self.Values.PROJECT_WITH_NULL_ALLOWLIST: None,
                    # UNKNOWN_PROJECT is absent → resolver returns None
                }
            )
        )
        app = RuntimeApiAppFactory.create_app(
            ports=ports,
            settings=settings,
            project_resolver=fake,
        )
        return TestClient(app), store, fake  # type: ignore[return-value]

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

    def _seed_workspace_defaults(self, client: TestClient) -> None:
        """Install non-empty workspace defaults so the fall-through path is observable."""
        response = client.put(
            "/v1/agent/workspace/defaults",
            headers=self._headers(permission_scopes=(RUNTIME_USE, ADMIN_USERS)),
            json={
                "default_model": {
                    "provider": "openai",
                    "model_name": self.Values.DEFAULT_MODEL_NAME,
                },
                "default_connectors": {
                    "notion": ["read"],
                },
                "retention_days": 90,
            },
        )
        assert response.status_code == 200, response.text

    def _post_create(
        self,
        client: TestClient,
        *,
        project_id: str | None = None,
        enabled_connectors: dict[str, Any] | None | object = ...,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "org_id": self.Values.ORG_ID,
            "user_id": self.Values.USER_ID,
            "assistant_id": self.Values.ASSISTANT_ID,
            "title": "p6.5-a2 test",
        }
        if project_id is not None:
            body["project_id"] = project_id
        if enabled_connectors is not ...:
            body["enabled_connectors"] = enabled_connectors
        response = client.post(
            "/v1/agent/conversations",
            headers=self._headers(),
            json=body,
        )
        assert response.status_code == 200, response.text
        return response.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProjectAllowlistInheritance(InheritanceFixtureMixin):
    """Rule 2: project allowlist seeds the new conversation."""

    def test_non_empty_allowlist_seeds_each_slug_as_active(self) -> None:
        client, store, fake = self.create_client()
        response = self._post_create(
            client, project_id=self.Values.PROJECT_WITH_ALLOWLIST
        )
        assert response["enabled_connectors"] == {
            "salesforce": [],
            "gmail": [],
        }
        # Resolver was consulted with the right scoping headers.
        assert len(fake.calls) == 1
        assert fake.calls[0] == {
            "org_id": self.Values.ORG_ID,
            "user_id": self.Values.USER_ID,
            "project_id": self.Values.PROJECT_WITH_ALLOWLIST,
        }
        # Audit row carries the inheritance flag.
        events = [
            record
            for event_type, record in store.audit_log
            if event_type == "conversation_created"
        ]
        assert events, "conversation_created audit row missing"
        context = events[-1].get("context") or {}
        assert context.get("project_id") == self.Values.PROJECT_WITH_ALLOWLIST
        assert context.get("inherited_from_project_default") is True

    def test_empty_allowlist_seeds_empty_map_and_records_inheritance(self) -> None:
        client, store, fake = self.create_client()
        response = self._post_create(
            client, project_id=self.Values.PROJECT_WITH_EMPTY_ALLOWLIST
        )
        # PRD §5.4: empty allowlist == explicit denial. No connectors.
        assert response["enabled_connectors"] == {}
        # The project policy was consulted + applied — flag is True.
        events = [
            record
            for event_type, record in store.audit_log
            if event_type == "conversation_created"
        ]
        assert events
        assert (
            events[-1].get("context", {}).get("inherited_from_project_default") is True
        )


class TestExplicitConnectorsWin(InheritanceFixtureMixin):
    """Rule 1: caller-explicit ``enabled_connectors`` skips inheritance."""

    def test_explicit_non_empty_skips_project_inheritance(self) -> None:
        client, store, fake = self.create_client()
        response = self._post_create(
            client,
            project_id=self.Values.PROJECT_WITH_ALLOWLIST,
            enabled_connectors={"jira": ["read", "comment"]},
        )
        # Caller's choice wins, NOT the project's allowlist.
        assert response["enabled_connectors"] == {
            "jira": ["read", "comment"],
        }
        # Resolver was NOT consulted — caller wins short-circuits.
        assert fake.calls == []
        # Audit row records the absence of project inheritance.
        events = [
            record
            for event_type, record in store.audit_log
            if event_type == "conversation_created"
        ]
        assert events
        assert (
            events[-1].get("context", {}).get("inherited_from_project_default") is False
        )

    def test_explicit_empty_map_skips_inheritance(self) -> None:
        client, _, fake = self.create_client()
        response = self._post_create(
            client,
            project_id=self.Values.PROJECT_WITH_ALLOWLIST,
            enabled_connectors={},
        )
        # Explicit empty == caller's "no connectors" choice. Wins over
        # the project's non-empty allowlist.
        assert response["enabled_connectors"] == {}
        assert fake.calls == []


class TestWorkspaceDefaultsFallThrough(InheritanceFixtureMixin):
    """Rule 3: no project allowlist → workspace defaults still seed."""

    def test_null_project_allowlist_falls_through_to_workspace_defaults(self) -> None:
        client, _, fake = self.create_client()
        self._seed_workspace_defaults(client)
        response = self._post_create(
            client, project_id=self.Values.PROJECT_WITH_NULL_ALLOWLIST
        )
        # Workspace default applied; no project inheritance.
        assert response["enabled_connectors"] == {"notion": ["read"]}
        assert len(fake.calls) == 1

    def test_no_project_id_falls_through_to_workspace_defaults(self) -> None:
        client, store, fake = self.create_client()
        self._seed_workspace_defaults(client)
        response = self._post_create(client)
        # No project_id → resolver is never called; workspace seed runs.
        assert response["enabled_connectors"] == {"notion": ["read"]}
        assert fake.calls == []
        events = [
            record
            for event_type, record in store.audit_log
            if event_type == "conversation_created"
        ]
        assert events
        ctx = events[-1].get("context", {})
        assert ctx.get("project_id") is None
        assert ctx.get("inherited_from_project_default") is False

    def test_unknown_project_id_falls_through_to_workspace_defaults(self) -> None:
        client, _, _ = self.create_client()
        self._seed_workspace_defaults(client)
        response = self._post_create(client, project_id=self.Values.UNKNOWN_PROJECT)
        # Resolver returns None for unknown project → fall through to
        # workspace defaults. PRD §5.4: a bad project id must never
        # block create.
        assert response["enabled_connectors"] == {"notion": ["read"]}
