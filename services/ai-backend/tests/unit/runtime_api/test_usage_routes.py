"""HTTP route tests for the B4 usage endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from agent_runtime.api.service import RuntimeApiService
from agent_runtime.persistence.records import (
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory


def _client_with_seed_runs(
    *,
    org_id: str = "org_a",
    user_id: str = "user_1",
) -> tuple[TestClient, InMemoryRuntimeApiStore]:
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
    completed = datetime.now(timezone.utc) - timedelta(hours=1)
    store.run_usage["r1"] = RuntimeRunUsageRecord(
        id="r1",
        org_id=org_id,
        user_id=user_id,
        conversation_id="conv-1",
        run_id="r1",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        input_tokens=100,
        output_tokens=200,
        cached_input_tokens=0,
        total_tokens=300,
        chunk_count=1,
        duration_ms=2000,
        started_at=completed - timedelta(seconds=2),
        completed_at=completed,
        status="completed",
    )
    store.run_usage["r2"] = RuntimeRunUsageRecord(
        id="r2",
        org_id=org_id,
        user_id=user_id,
        conversation_id="conv-2",
        run_id="r2",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        input_tokens=50,
        output_tokens=70,
        cached_input_tokens=10,
        total_tokens=120,
        chunk_count=1,
        duration_ms=1500,
        started_at=completed - timedelta(seconds=2),
        completed_at=completed,
        status="completed",
    )
    return TestClient(RuntimeApiAppFactory.create_app(service)), store


class TestUsageMe:
    def test_returns_cold_start_fallback_when_rollups_empty(self) -> None:
        client, _ = _client_with_seed_runs()
        response = client.get(
            "/v1/usage/me",
            params={"org_id": "org_a", "user_id": "user_1", "period": "30d"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["cold_start_fallback"] is True
        assert body["currency"] == "USD"
        assert body["total"]["runs_count"] == 2
        assert body["total"]["input"] == 150
        assert body["total"]["output"] == 270
        assert body["total"]["total"] == 420
        assert len(body["by_model"]) == 1
        assert body["by_model"][0]["provider"] == "openai"
        assert body["by_model"][0]["runs_count"] == 2

    def test_400_when_org_id_missing_and_no_service_token(self) -> None:
        client, _ = _client_with_seed_runs()
        response = client.get("/v1/usage/me", params={"period": "today"})
        assert response.status_code == 400


class TestUsageRun:
    def test_returns_breakdown_for_existing_run(self) -> None:
        client, _ = _client_with_seed_runs()
        response = client.get(
            "/v1/usage/runs/r1",
            params={"org_id": "org_a", "user_id": "user_1"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["run_id"] == "r1"
        assert body["total"]["input"] == 100
        assert body["total"]["output"] == 200
        # No per-call rows seeded — `by_call` empty.
        assert body["by_call"] == []

    def test_404_when_run_unknown(self) -> None:
        client, _ = _client_with_seed_runs()
        response = client.get(
            "/v1/usage/runs/nope",
            params={"org_id": "org_a", "user_id": "user_1"},
        )
        assert response.status_code == 404

    def test_404_for_other_tenant(self) -> None:
        client, _ = _client_with_seed_runs(org_id="org_a")
        response = client.get(
            "/v1/usage/runs/r1",
            params={"org_id": "org_b", "user_id": "user_x"},
        )
        # Different tenant can't see org_a's run.
        assert response.status_code == 404


class TestUsageOrgSubagents:
    """Sub-PRD 01d — ``/v1/usage/org/subagents`` endpoint contract."""

    def _seed_call(
        self,
        store: InMemoryRuntimeApiStore,
        *,
        org_id: str,
        subagent_id: str | None,
        created_at: datetime,
        input_tokens: int = 100,
        output_tokens: int = 50,
    ) -> None:
        store.model_call_usage.append(
            RuntimeModelCallUsageRecord(
                id=f"call-{len(store.model_call_usage)}",
                org_id=org_id,
                run_id="r1",
                conversation_id="conv-1",
                trace_id="trace-1",
                subagent_id=subagent_id,
                model_provider="openai",
                model_name="gpt-5.4-mini",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=0,
                total_tokens=input_tokens + output_tokens,
                duration_ms=500,
                created_at=created_at,
            )
        )

    def test_cold_start_fallback_synthesizes_rows_from_live_scan(self) -> None:
        client, store = _client_with_seed_runs()
        completed = datetime.now(timezone.utc) - timedelta(hours=1)
        self._seed_call(
            store, org_id="org_a", subagent_id="researcher", created_at=completed
        )
        self._seed_call(
            store, org_id="org_a", subagent_id="writer", created_at=completed
        )
        response = client.get(
            "/v1/usage/org/subagents",
            params={"org_id": "org_a", "period": "30d"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["cold_start_fallback"] is True
        slugs = {row["subagent_slug"] for row in body["rows"]}
        assert slugs == {"researcher", "writer"}

    def test_returns_empty_rows_when_no_data(self) -> None:
        client, _ = _client_with_seed_runs()
        response = client.get(
            "/v1/usage/org/subagents",
            params={"org_id": "org_a", "period": "30d"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["rows"] == []
        assert body["cold_start_fallback"] is True


class TestUsageOrgPurpose:
    """Sub-PRD 01d — ``/v1/usage/org/purpose`` endpoint contract."""

    def _seed_call(
        self,
        store: InMemoryRuntimeApiStore,
        *,
        org_id: str,
        purpose: str,
        created_at: datetime,
    ) -> None:
        store.model_call_usage.append(
            RuntimeModelCallUsageRecord(
                id=f"call-{len(store.model_call_usage)}",
                org_id=org_id,
                run_id="r1",
                conversation_id="conv-1",
                trace_id="trace-1",
                purpose=purpose,
                model_provider="openai",
                model_name="gpt-5.4-mini",
                input_tokens=100,
                output_tokens=50,
                cached_input_tokens=0,
                total_tokens=150,
                duration_ms=500,
                created_at=created_at,
            )
        )

    def test_cold_start_fallback_groups_by_purpose(self) -> None:
        client, store = _client_with_seed_runs()
        completed = datetime.now(timezone.utc) - timedelta(hours=1)
        self._seed_call(store, org_id="org_a", purpose="main", created_at=completed)
        self._seed_call(
            store, org_id="org_a", purpose="tool_interpretation", created_at=completed
        )
        self._seed_call(
            store, org_id="org_a", purpose="tool_interpretation", created_at=completed
        )
        response = client.get(
            "/v1/usage/org/purpose",
            params={"org_id": "org_a", "period": "30d"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["cold_start_fallback"] is True
        by_purpose = {row["purpose"]: row for row in body["rows"]}
        assert by_purpose["main"]["call_count"] == 1
        assert by_purpose["tool_interpretation"]["call_count"] == 2


class TestUsageByConnector:
    """PR 7.2 — by_connector axis on /v1/usage/me + /v1/usage/conversations."""

    def _seed_call(
        self,
        store: InMemoryRuntimeApiStore,
        *,
        org_id: str,
        run_id: str,
        conversation_id: str,
        connector_slug: str | None,
        created_at: datetime,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        store.model_call_usage.append(
            RuntimeModelCallUsageRecord(
                id=f"{run_id}-{len(store.model_call_usage)}",
                org_id=org_id,
                run_id=run_id,
                conversation_id=conversation_id,
                trace_id=f"trace-{run_id}",
                model_provider="openai",
                model_name="gpt-5.4-mini",
                connector_slug=connector_slug,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=0,
                total_tokens=input_tokens + output_tokens,
                duration_ms=500,
                created_at=created_at,
            )
        )

    def test_by_connector_populated_on_me(self) -> None:
        client, store = _client_with_seed_runs()
        completed = datetime.now(timezone.utc) - timedelta(hours=1)
        # Per-call rows for the seeded runs r1 and r2 (two connectors +
        # one unattributed).
        self._seed_call(
            store,
            org_id="org_a",
            run_id="r1",
            conversation_id="conv-1",
            connector_slug=None,
            created_at=completed,
            input_tokens=10,
            output_tokens=5,
        )
        self._seed_call(
            store,
            org_id="org_a",
            run_id="r1",
            conversation_id="conv-1",
            connector_slug="slack",
            created_at=completed,
            input_tokens=20,
            output_tokens=10,
        )
        self._seed_call(
            store,
            org_id="org_a",
            run_id="r2",
            conversation_id="conv-2",
            connector_slug="notion",
            created_at=completed,
            input_tokens=15,
            output_tokens=7,
        )
        response = client.get(
            "/v1/usage/me",
            params={"org_id": "org_a", "user_id": "user_1", "period": "30d"},
        )
        assert response.status_code == 200
        by_connector = {
            row["connector_slug"]: row for row in response.json()["by_connector"]
        }
        assert set(by_connector.keys()) == {"", "slack", "notion"}
        assert by_connector["slack"]["input"] == 20
        assert by_connector["notion"]["input"] == 15
        assert by_connector[""]["input"] == 10

    def test_by_connector_on_conversation(self) -> None:
        client, store = _client_with_seed_runs()
        completed = datetime.now(timezone.utc) - timedelta(hours=1)
        self._seed_call(
            store,
            org_id="org_a",
            run_id="r1",
            conversation_id="conv-1",
            connector_slug="slack",
            created_at=completed,
            input_tokens=20,
            output_tokens=10,
        )
        # A second conv's calls must NOT leak into conv-1's breakdown.
        self._seed_call(
            store,
            org_id="org_a",
            run_id="r2",
            conversation_id="conv-2",
            connector_slug="notion",
            created_at=completed,
            input_tokens=15,
            output_tokens=7,
        )
        response = client.get(
            "/v1/usage/conversations/conv-1",
            params={"org_id": "org_a", "user_id": "user_1", "period": "30d"},
        )
        assert response.status_code == 200
        by_connector = {
            row["connector_slug"]: row for row in response.json()["by_connector"]
        }
        assert set(by_connector.keys()) == {"slack"}
        assert by_connector["slack"]["input"] == 20
        assert by_connector["slack"]["output"] == 10
