"""PR 1.2 — PATCH /v1/agent/conversations/{id}/connectors + run-create fallback."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from runtime_api.app import RuntimeApiAppFactory
from agent_runtime.api.constants import Messages
from agent_runtime.api.service import RuntimeApiService
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from agent_runtime.settings import RuntimeSettings


class ConnectorScopeRouteFixtureMixin:
    class Values:
        ORG_ID = "org_pr12"
        USER_ID = "user_pr12"
        ASSISTANT_ID = "assistant_pr12"

    def create_client(self) -> tuple[TestClient, InMemoryRuntimeApiStore]:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        service = RuntimeApiService(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
        )
        app = RuntimeApiAppFactory.create_app(service)
        return TestClient(app), store

    def conversation_payload(self) -> dict[str, Any]:
        return {
            "org_id": self.Values.ORG_ID,
            "user_id": self.Values.USER_ID,
            "assistant_id": self.Values.ASSISTANT_ID,
            "title": "scope test",
        }

    def create_conversation(self, client: TestClient) -> str:
        response = client.post(
            "/v1/agent/conversations", json=self.conversation_payload()
        )
        assert response.status_code == 200, response.text
        return response.json()["conversation_id"]

    def patch_scopes(
        self,
        client: TestClient,
        conversation_id: str,
        scopes: dict[str, list[str] | None],
    ) -> Any:
        return client.patch(
            f"/v1/agent/conversations/{conversation_id}/connectors",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
            json={"scopes": scopes},
        )

    def run_payload(
        self,
        conversation_id: str,
        *,
        connector_scopes: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        return {
            "conversation_id": conversation_id,
            "org_id": self.Values.ORG_ID,
            "user_id": self.Values.USER_ID,
            "user_input": "hello",
            "content_format": "text",
            "model": {"provider": "openai", "model_name": "gpt-5.4-mini"},
            "request_context": {
                "roles": ["employee"],
                "permission_scopes": ["search:read"],
                "connector_scopes": connector_scopes or {},
            },
        }


class TestUpdateConversationConnectorsRoute(ConnectorScopeRouteFixtureMixin):
    async def test_merge_patch_round_trip(self) -> None:
        client, _store = self.create_client()
        conversation_id = await self.create_conversation(client)

        first = self.patch_scopes(
            client,
            conversation_id,
            {"slack": ["read"], "drive": ["read", "comment"]},
        )
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["conversation_id"] == conversation_id
        assert body["scopes"] == {
            "slack": ["read"],
            "drive": ["read", "comment"],
        }
        assert body["updated_at"] is not None

        # Second patch only touches `slack` (pause it). `drive` survives.
        second = self.patch_scopes(client, conversation_id, {"slack": None})
        assert second.status_code == 200, second.text
        assert second.json()["scopes"] == {
            "slack": None,
            "drive": ["read", "comment"],
        }

        # GET on the conversation surfaces the same snapshot inline.
        snapshot = client.get(
            f"/v1/agent/conversations/{conversation_id}",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )
        assert snapshot.status_code == 200
        assert snapshot.json()["enabled_connectors"] == {
            "slack": None,
            "drive": ["read", "comment"],
        }

    async def test_foreign_org_404s(self) -> None:
        client, _store = self.create_client()
        conversation_id = await self.create_conversation(client)

        response = client.patch(
            f"/v1/agent/conversations/{conversation_id}/connectors",
            params={"org_id": "other_org", "user_id": self.Values.USER_ID},
            json={"scopes": {"slack": None}},
        )
        assert response.status_code == 404
        assert Messages.Error.CONVERSATION_NOT_FOUND in response.text

    async def test_invalid_payload_rejected(self) -> None:
        client, _store = self.create_client()
        conversation_id = await self.create_conversation(client)

        response = client.patch(
            f"/v1/agent/conversations/{conversation_id}/connectors",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
            json={"scopes": {"slack": "read"}},  # value must be list-or-null
        )
        # The app's exception handler maps Pydantic validation errors to
        # 400 (not the FastAPI default 422); see runtime_api/http/errors.py.
        assert response.status_code == 400

    async def test_audit_row_emitted_with_diff_metadata(self) -> None:
        client, store = self.create_client()
        conversation_id = await self.create_conversation(client)

        self.patch_scopes(client, conversation_id, {"slack": ["read"]})
        self.patch_scopes(client, conversation_id, {"slack": None})

        action = Messages.Audit.CONVERSATION_CONNECTORS_UPDATE
        rows = [record for kind, record in store.audit_log if kind == action]
        assert len(rows) == 2
        last = rows[-1]
        assert last["resource_type"] == "conversation"
        assert last["resource_id"] == conversation_id
        meta = last["metadata"]
        assert meta["diff_keys"] == ["slack"]
        assert meta["before"] == {"slack": ["read"]}
        assert meta["after"] == {"slack": None}


class TestRunCreateConsumesConversationScope(ConnectorScopeRouteFixtureMixin):
    async def test_run_inherits_chat_scope_when_header_empty(self) -> None:
        client, store = self.create_client()
        conversation_id = await self.create_conversation(client)

        # Pause Slack at the chat level; activate Drive.
        self.patch_scopes(
            client,
            conversation_id,
            {"slack": None, "drive": ["read"]},
        )

        # No connector_scopes on the request → fallback materialises the
        # active subset (Drive only) into the runtime context.
        run = client.post(
            "/v1/agent/runs",
            json=self.run_payload(conversation_id, connector_scopes={}),
        )
        assert run.status_code == 200, run.text
        run_id = run.json()["run_id"]

        # The frozen runtime context on the run row exposes only `drive`
        # (paused connectors filtered out). AgentRuntimeContext normalizes
        # scope tuples to frozenset for hashable lookup.
        record = store.runs[run_id]
        assert record.runtime_context.connector_scopes == {
            "drive": frozenset({"read"}),
        }

    async def test_explicit_header_overrides_chat_scope(self) -> None:
        client, store = self.create_client()
        conversation_id = await self.create_conversation(client)
        self.patch_scopes(client, conversation_id, {"drive": ["read"]})

        # A non-empty header wins (mirrors a service-to-service caller
        # that pre-computed scopes for, e.g., a share-link recipient).
        run = client.post(
            "/v1/agent/runs",
            json=self.run_payload(
                conversation_id,
                connector_scopes={"notion": ["read"]},
            ),
        )
        assert run.status_code == 200, run.text
        record = store.runs[run.json()["run_id"]]
        assert record.runtime_context.connector_scopes == {
            "notion": frozenset({"read"}),
        }


class TestPausedConnectorsLandOnRuntimeContext(ConnectorScopeRouteFixtureMixin):
    """PR 4.4.6.2 / 4.4.7 — pin the latent leak fix where
    ``RuntimeRequestContext.paused_connectors`` was missing the field
    declaration AND ``_request_with_runtime_context`` wasn't threading
    it onto the ``AgentRuntimeContext`` consumed by the MCP gate. End-
    to-end assertion through ``create_run``."""

    async def test_paused_slug_lands_on_runtime_context_paused_connectors(
        self,
    ) -> None:
        client, store = self.create_client()
        conversation_id = await self.create_conversation(client)

        # Pause Linear at the chat level. ``runtime_connector_scopes``
        # drops the entry so ``connector_scopes`` is empty; the FIX
        # under test is that ``paused_connectors`` carries the slug
        # explicitly so the MCP gate can read it.
        self.patch_scopes(client, conversation_id, {"seed:linear": None})

        run = client.post(
            "/v1/agent/runs",
            json=self.run_payload(conversation_id, connector_scopes={}),
        )
        assert run.status_code == 200, run.text
        record = store.runs[run.json()["run_id"]]

        assert "seed:linear" in record.runtime_context.paused_connectors
        # Connector scopes filters out the paused entry — invariant
        # preserved by Phase 1.
        assert "seed:linear" not in record.runtime_context.connector_scopes

    async def test_paused_set_carries_through_when_header_drives_scopes(
        self,
    ) -> None:
        client, store = self.create_client()
        conversation_id = await self.create_conversation(client)
        # Two-shape scope: Slack actively scoped at the chat level,
        # Linear paused at the chat level. The header pre-supplies a
        # third connector (Notion). Header wins on connector_scopes;
        # the chat's pause set still applies.
        self.patch_scopes(
            client,
            conversation_id,
            {"seed:slack": ["read"], "seed:linear": None},
        )

        run = client.post(
            "/v1/agent/runs",
            json=self.run_payload(
                conversation_id,
                connector_scopes={"notion": ["read"]},
            ),
        )
        assert run.status_code == 200, run.text
        record = store.runs[run.json()["run_id"]]

        # Header drove ``connector_scopes`` (Notion only).
        assert record.runtime_context.connector_scopes == {
            "notion": frozenset({"read"}),
        }
        # But the conversation's paused set still applies — service-to-
        # service callers don't bypass the user's per-chat mute.
        assert "seed:linear" in record.runtime_context.paused_connectors


class TestSuggestedConnectorsLandOnRuntimeContext(ConnectorScopeRouteFixtureMixin):
    """PR 4.4.7 Phase 2 (Slice B) — assert the suggestible-connectors
    snapshot is materialised onto ``AgentRuntimeContext`` at run-create
    so the system prompt section + discovery service can consume it.

    The default ``Null`` resolver returns empty (no
    ``BACKEND_BASE_URL``/``ENTERPRISE_SERVICE_TOKEN`` configured in the
    test env). The TEST FIX is that the empty tuple lands cleanly
    rather than raising or producing a missing-field defect, AND that a
    concrete resolver injecting a card flows through unchanged.
    """

    async def test_default_null_resolver_yields_empty_tuple(self) -> None:
        client, store = self.create_client()
        conversation_id = await self.create_conversation(client)
        run = client.post(
            "/v1/agent/runs",
            json=self.run_payload(conversation_id, connector_scopes={}),
        )
        assert run.status_code == 200, run.text
        record = store.runs[run.json()["run_id"]]
        assert record.runtime_context.suggested_connectors == ()

    async def test_resolver_cards_land_on_context(self) -> None:
        from agent_runtime.execution.contracts import CatalogSuggestionCard

        class _StubResolver:
            async def resolve(
                self,
                *,
                org_id: str,
                user_id: str,
                exclude_paused,
            ):
                return (
                    CatalogSuggestionCard(
                        slug="linear",
                        display_name="Linear",
                        description="Issues, projects, and cycles.",
                    ),
                )

        # Build a service explicitly so we can inject the resolver.
        # ``ConnectorScopeRouteFixtureMixin.create_client`` builds the
        # service inline; here we replicate the bits we need.
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        service = RuntimeApiService(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            suggestible_connectors_resolver=_StubResolver(),  # type: ignore[arg-type]
        )
        app = RuntimeApiAppFactory.create_app(service)
        client = TestClient(app)
        conversation_id = await self.create_conversation(client)

        run = client.post(
            "/v1/agent/runs",
            json=self.run_payload(conversation_id, connector_scopes={}),
        )
        assert run.status_code == 200, run.text
        record = store.runs[run.json()["run_id"]]
        assert len(record.runtime_context.suggested_connectors) == 1
        card = record.runtime_context.suggested_connectors[0]
        assert card.slug == "linear"
        assert card.display_name == "Linear"

    async def test_suggestible_resolver_receives_paused_set(self) -> None:
        # The resolver gets the conversation's paused server_ids so the
        # backend can pre-filter them out. Without this the agent would
        # see paused entries as "discoverable" and re-suggest them.
        from agent_runtime.execution.contracts import CatalogSuggestionCard

        observed_paused: list[tuple[str, ...]] = []

        class _CapturingResolver:
            async def resolve(self, *, org_id, user_id, exclude_paused):
                observed_paused.append(tuple(exclude_paused))
                return (
                    CatalogSuggestionCard(
                        slug="linear",
                        display_name="Linear",
                    ),
                )

        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        service = RuntimeApiService(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
            suggestible_connectors_resolver=_CapturingResolver(),  # type: ignore[arg-type]
        )
        app = RuntimeApiAppFactory.create_app(service)
        client = TestClient(app)
        conversation_id = await self.create_conversation(client)
        # Pause two connectors at the chat level.
        self.patch_scopes(
            client,
            conversation_id,
            {"seed:linear": None, "seed:atlassian": None},
        )

        run = client.post(
            "/v1/agent/runs",
            json=self.run_payload(conversation_id, connector_scopes={}),
        )
        assert run.status_code == 200, run.text
        # The resolver was called with the conversation's paused set.
        assert observed_paused, "resolver was not called"
        observed = set(observed_paused[0])
        assert "seed:linear" in observed
        assert "seed:atlassian" in observed
