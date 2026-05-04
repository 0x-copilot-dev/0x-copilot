"""HTTP route tests for the B4 usage endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from agent_runtime.api.service import RuntimeApiService
from agent_runtime.persistence.records import RuntimeRunUsageRecord
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
