"""HTTP route tests for the B7 ``/v1/budgets/*`` endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_runtime.api.service import RuntimeApiService
from agent_runtime.persistence.records import (
    BudgetEnforcement,
    BudgetPeriod,
    BudgetRecord,
    BudgetScope,
    BudgetStatus,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory


def _client() -> tuple[TestClient, InMemoryRuntimeApiStore]:
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
    return TestClient(RuntimeApiAppFactory.create_app(service)), store


class TestBudgetCRUD:
    def test_post_then_list(self) -> None:
        client, _ = _client()
        response = client.post(
            "/v1/budgets",
            params={"org_id": "org_a", "user_id": "user_1"},
            json={
                "scope": "user",
                "period": "day",
                "enforcement": "hard",
                "limit_micro_usd": 1_000_000,
                "user_id": "user_1",
            },
        )
        assert response.status_code == 200
        created = response.json()
        assert created["scope"] == "user"
        assert created["limit_micro_usd"] == 1_000_000

        listing = client.get(
            "/v1/budgets",
            params={"org_id": "org_a", "user_id": "user_1"},
        )
        assert listing.status_code == 200
        body = listing.json()
        assert len(body["budgets"]) == 1
        assert body["budgets"][0]["id"] == created["id"]

    def test_post_duplicate_returns_409(self) -> None:
        client, _ = _client()
        payload = {
            "scope": "org",
            "period": "month",
            "enforcement": "soft",
            "limit_micro_usd": 50_000_000,
        }
        first = client.post(
            "/v1/budgets",
            params={"org_id": "org_a", "user_id": "user_admin"},
            json=payload,
        )
        assert first.status_code == 200
        second = client.post(
            "/v1/budgets",
            params={"org_id": "org_a", "user_id": "user_admin"},
            json=payload,
        )
        assert second.status_code == 409

    async def test_patch_updates_status(self) -> None:
        client, store = _client()
        record = await store.create_budget(
            BudgetRecord(
                org_id="org_a",
                user_id=None,
                scope=BudgetScope.ORG,
                period=BudgetPeriod.DAY,
                enforcement=BudgetEnforcement.HARD,
                limit_micro_usd=1_000_000,
                status=BudgetStatus.ACTIVE,
                created_by_user_id="user_admin",
            )
        )
        response = client.patch(
            f"/v1/budgets/{record.id}",
            params={"org_id": "org_a", "user_id": "user_admin"},
            json={"status": "disabled", "limit_micro_usd": 2_000_000},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "disabled"
        assert body["limit_micro_usd"] == 2_000_000

    async def test_delete_cascades_to_state(self) -> None:
        client, store = _client()
        record = await store.create_budget(
            BudgetRecord(
                org_id="org_a",
                user_id=None,
                scope=BudgetScope.ORG,
                period=BudgetPeriod.DAY,
                enforcement=BudgetEnforcement.HARD,
                limit_micro_usd=1_000_000,
                status=BudgetStatus.ACTIVE,
                created_by_user_id="user_admin",
            )
        )
        response = client.delete(
            f"/v1/budgets/{record.id}",
            params={"org_id": "org_a", "user_id": "user_admin"},
        )
        assert response.status_code == 200
        listing = client.get(
            "/v1/budgets",
            params={"org_id": "org_a", "user_id": "user_admin"},
        )
        assert listing.json()["budgets"] == []


class TestBudgetMe:
    async def test_returns_remaining_headroom(self) -> None:
        client, store = _client()
        await store.create_budget(
            BudgetRecord(
                org_id="org_a",
                user_id="user_1",
                scope=BudgetScope.USER,
                period=BudgetPeriod.DAY,
                enforcement=BudgetEnforcement.HARD,
                limit_micro_usd=1_000_000,
                status=BudgetStatus.ACTIVE,
                created_by_user_id="user_1",
            )
        )
        response = client.get(
            "/v1/budgets/me",
            params={"org_id": "org_a", "user_id": "user_1"},
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["budgets"]) == 1
        row = body["budgets"][0]
        assert row["limit_micro_usd"] == 1_000_000
        assert row["remaining_micro_usd"] == 1_000_000
        assert row["current_micro_usd"] == 0
