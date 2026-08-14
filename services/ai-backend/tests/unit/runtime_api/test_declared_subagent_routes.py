"""The declare-an-agent entry point, asserted through the mounted app.

Every case here goes through ``TestClient(RuntimeApiAppFactory.create_app(...))``
rather than calling the handlers, because the thing that was missing was never
the handler — ``FileSubagentDefinitionStore.write_definition`` has existed and
worked since the file store landed. What was missing was a path anything could
call, so a test that called the class directly would have passed before this
change and proved nothing.

The round-trip case is the load-bearing one: it declares an agent over HTTP and
then reads it back through ``FileSubagentDefinitionProvider`` — the port the
supervisor's ``DynamicSubagentCatalog`` actually consumes — so "declared" means
"the catalog can see it", not "a file appeared".
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi.testclient import TestClient

from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory

_SERVICE_TOKEN = "test-service-token"


class DeclaredSubagentFixtureMixin:
    """Client, headers, and the definition payload every case declares."""

    class Values:
        ORG_ID = "org_declared"
        USER_ID = "user_declared"
        NAME = "doc-reader"
        GRAPH_ID = "research_graph"

    def client(self) -> TestClient:
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        ports = RuntimeAdapterFactory.from_store(InMemoryRuntimeApiStore())
        return TestClient(
            RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
        )

    def headers(self) -> dict[str, str]:
        return {
            "x-enterprise-service-token": _SERVICE_TOKEN,
            "x-enterprise-org-id": self.Values.ORG_ID,
            "x-enterprise-user-id": self.Values.USER_ID,
            "x-enterprise-roles": "employee",
            "x-enterprise-permission-scopes": RUNTIME_USE,
            "x-enterprise-connector-scopes": "{}",
        }

    def definition(
        self,
        *,
        name: str | None = None,
        tools: tuple[str, ...] = ("read_file", "ls"),
    ) -> dict[str, object]:
        return {
            "name": name or self.Values.NAME,
            "description": "Reads documents and reports what they say.",
            "graph_id": self.Values.GRAPH_ID,
            "tools": list(tools),
            "skills": ["search-subagent-logs"],
        }


@pytest.fixture(autouse=True)
def _service_auth(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _SERVICE_TOKEN)
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    monkeypatch.setenv("RBAC_MODE", "enforce")
    yield


@pytest.fixture
def file_store(monkeypatch, tmp_path: Path) -> Iterator[Path]:
    """Activate the file-native store the way the desktop does — by environment."""

    monkeypatch.setenv("RUNTIME_STORE_BACKEND", "file")
    monkeypatch.setenv("RUNTIME_FILE_STORE_ROOT", str(tmp_path))
    yield tmp_path


class TestDeclaringAnAgent(DeclaredSubagentFixtureMixin):
    def test_a_declared_agent_reaches_the_catalog_provider(
        self, file_store: Path
    ) -> None:
        """Declare over HTTP, read back through the port the supervisor consumes.

        Fails without this change: there was no route to PUT to at all, so the
        provider had nothing to find.
        """

        from runtime_adapters.file.agent_state_store import (
            FileAgentStateWiring,
        )

        response = self.client().put(
            f"/v1/agent/subagents/{self.Values.NAME}",
            json=self.definition(),
            headers=self.headers(),
        )

        assert response.status_code == 200
        provider = FileAgentStateWiring().subagent_definition_provider()
        assert provider is not None
        declared = provider.list_subagent_definitions()
        assert [entry["name"] for entry in declared] == [self.Values.NAME]

    def test_the_declared_tools_are_what_the_definition_carries(
        self, file_store: Path
    ) -> None:
        """The tool allowlist survives the round trip byte for byte.

        This is the field ``SubagentAuthorityPolicy.narrow`` intersects a
        child's requested tools against, so a route that dropped or widened it
        in translation would be a permission bug, not a serialisation one.
        """

        client = self.client()
        client.put(
            f"/v1/agent/subagents/{self.Values.NAME}",
            json=self.definition(tools=("read_file",)),
            headers=self.headers(),
        )

        listed = client.get("/v1/agent/subagents", headers=self.headers()).json()

        assert [entry["name"] for entry in listed["subagents"]] == [self.Values.NAME]
        assert listed["subagents"][0]["tools"] == ["read_file"]

    def test_declaring_twice_replaces_rather_than_duplicates(
        self, file_store: Path
    ) -> None:
        """One name is one agent — the catalog refuses duplicate names outright."""

        client = self.client()
        client.put(
            f"/v1/agent/subagents/{self.Values.NAME}",
            json=self.definition(tools=("read_file",)),
            headers=self.headers(),
        )
        client.put(
            f"/v1/agent/subagents/{self.Values.NAME}",
            json=self.definition(tools=("ls",)),
            headers=self.headers(),
        )

        listed = client.get("/v1/agent/subagents", headers=self.headers()).json()

        assert len(listed["subagents"]) == 1
        assert listed["subagents"][0]["tools"] == ["ls"]

    def test_a_body_naming_a_different_agent_is_refused(self, file_store: Path) -> None:
        """Neither side silently wins; the mismatch is the answer."""

        response = self.client().put(
            "/v1/agent/subagents/reader",
            json=self.definition(name="writer"),
            headers=self.headers(),
        )

        assert response.status_code == 400

    def test_undeclare_removes_it_from_the_catalog(self, file_store: Path) -> None:
        client = self.client()
        client.put(
            f"/v1/agent/subagents/{self.Values.NAME}",
            json=self.definition(),
            headers=self.headers(),
        )

        deleted = client.delete(
            f"/v1/agent/subagents/{self.Values.NAME}", headers=self.headers()
        )
        listed = client.get("/v1/agent/subagents", headers=self.headers()).json()

        assert deleted.status_code == 204
        assert listed["subagents"] == []

    def test_undeclaring_an_unknown_name_is_404_not_204(self, file_store: Path) -> None:
        """A delete that reports success for a typo teaches the wrong thing."""

        response = self.client().delete(
            "/v1/agent/subagents/never-declared", headers=self.headers()
        )

        assert response.status_code == 404

    def test_an_invalid_definition_is_refused_by_the_domain_contract(
        self, file_store: Path
    ) -> None:
        """No parallel DTO means no second, laxer validation of a capability grant.

        400, not 422: this service maps ``RequestValidationError`` through its
        own handler (``runtime_api/http/errors.py``), which is the shape every
        other body-validated route on it already answers with.
        """

        response = self.client().put(
            f"/v1/agent/subagents/{self.Values.NAME}",
            json={"name": self.Values.NAME, "description": "x"},
            headers=self.headers(),
        )

        assert response.status_code == 400
        assert not (file_store / "subagent_defs").exists()

    def test_a_hand_edited_unparseable_definition_does_not_break_the_list(
        self, file_store: Path
    ) -> None:
        """The directory is hand-editable, so one bad file must not hide the rest."""

        client = self.client()
        client.put(
            f"/v1/agent/subagents/{self.Values.NAME}",
            json=self.definition(),
            headers=self.headers(),
        )
        (file_store / "subagent_defs" / "broken.json").write_text(
            '{"name": "broken"}', encoding="utf-8"
        )

        listed = client.get("/v1/agent/subagents", headers=self.headers()).json()

        assert [entry["name"] for entry in listed["subagents"]] == [self.Values.NAME]


class TestWithoutTheFileStore(DeclaredSubagentFixtureMixin):
    """``subagent_defs/`` is a file-store directory; say so rather than pretend."""

    def test_declaring_answers_501_when_no_file_store_is_active(
        self, monkeypatch
    ) -> None:
        monkeypatch.delenv("RUNTIME_STORE_BACKEND", raising=False)
        monkeypatch.delenv("RUNTIME_FILE_STORE_ROOT", raising=False)

        response = self.client().put(
            f"/v1/agent/subagents/{self.Values.NAME}",
            json=self.definition(),
            headers=self.headers(),
        )

        assert response.status_code == 501
