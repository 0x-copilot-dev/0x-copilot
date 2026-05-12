"""HTTP route tests for the B5 ``/conversations/{id}/context`` endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from agent_runtime.persistence.records import (
    ModelPricingRecord,
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import (
    ConversationStatus,
    CreateConversationRequest,
)


async def _bootstrap(
    *,
    org_id: str = "org_a",
    user_id: str = "user_1",
    seed_pricing: bool = True,
) -> tuple[TestClient, InMemoryRuntimeApiStore, str]:
    store = InMemoryRuntimeApiStore()
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )
    ports = RuntimeAdapterFactory.from_store(store)
    conv = await store.create_conversation(
        CreateConversationRequest(org_id=org_id, user_id=user_id, title="Demo")
    )
    if seed_pricing:
        await store.upsert_pricing(
            ModelPricingRecord(
                provider="openai",
                model_name="gpt-5.4-mini",
                effective_from=datetime.now(timezone.utc) - timedelta(days=10),
                input_per_1m_micro_usd=1_000_000,
                output_per_1m_micro_usd=2_000_000,
                context_window_tokens=128_000,
                pricing_version="2026-q1",
            )
        )
    return (
        TestClient(RuntimeApiAppFactory.create_app(ports=ports, settings=settings)),
        store,
        conv.conversation_id,
    )


def _seed_run(
    store: InMemoryRuntimeApiStore,
    *,
    conversation_id: str,
    run_id: str,
    completed_at: datetime,
    org_id: str = "org_a",
    user_id: str = "user_1",
    input_tokens: int = 1_000,
    output_tokens: int = 200,
    cached_input_tokens: int = 0,
) -> None:
    store.run_usage[run_id] = RuntimeRunUsageRecord(
        id=run_id,
        org_id=org_id,
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
        model_provider="openai",
        model_name="gpt-5.4-mini",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        total_tokens=input_tokens + output_tokens + cached_input_tokens,
        chunk_count=1,
        duration_ms=500,
        started_at=completed_at - timedelta(seconds=1),
        completed_at=completed_at,
        status="completed",
    )


class TestConversationContextRoute:
    async def test_empty_conversation_returns_zero_slice(self) -> None:
        client, _, conv_id = await _bootstrap()
        response = client.get(
            f"/v1/agent/conversations/{conv_id}/context",
            params={"org_id": "org_a", "user_id": "user_1"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["current"]["last_run_id"] is None
        assert body["current"]["headroom_pct"] is None
        assert body["model"]["provider"] == "openai"

    async def test_populated_run_returns_window_and_headroom(self) -> None:
        client, store, conv_id = await _bootstrap()
        completed = datetime.now(timezone.utc) - timedelta(minutes=1)
        _seed_run(
            store,
            conversation_id=conv_id,
            run_id="r-latest",
            completed_at=completed,
            input_tokens=1_000,
            output_tokens=200,
        )
        response = client.get(
            f"/v1/agent/conversations/{conv_id}/context",
            params={"org_id": "org_a", "user_id": "user_1"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["current"]["last_run_id"] == "r-latest"
        assert body["current"]["input_tokens"] == 1_000
        assert body["current"]["output_tokens"] == 200
        assert body["current"]["headroom_pct"] == 99
        assert body["current"]["available_tokens"] == 127_000
        assert body["model"]["context_window_tokens"] == 128_000

    async def test_picks_latest_run_when_multiple_exist(self) -> None:
        client, store, conv_id = await _bootstrap()
        early = datetime.now(timezone.utc) - timedelta(hours=2)
        late = datetime.now(timezone.utc) - timedelta(minutes=1)
        _seed_run(
            store,
            conversation_id=conv_id,
            run_id="r-early",
            completed_at=early,
            input_tokens=100,
        )
        _seed_run(
            store,
            conversation_id=conv_id,
            run_id="r-late",
            completed_at=late,
            input_tokens=900,
        )
        response = client.get(
            f"/v1/agent/conversations/{conv_id}/context",
            params={"org_id": "org_a", "user_id": "user_1"},
        )
        body = response.json()
        assert body["current"]["last_run_id"] == "r-late"
        assert body["current"]["input_tokens"] == 900

    async def test_404_for_foreign_tenant(self) -> None:
        client, _, conv_id = await _bootstrap()
        response = client.get(
            f"/v1/agent/conversations/{conv_id}/context",
            params={"org_id": "org_b", "user_id": "user_xx"},
        )
        # 404 — does NOT 403, to avoid leaking conversation existence.
        assert response.status_code == 404

    async def test_per_call_breakdown_present_when_calls_exist(self) -> None:
        client, store, conv_id = await _bootstrap()
        completed = datetime.now(timezone.utc) - timedelta(minutes=1)
        _seed_run(
            store,
            conversation_id=conv_id,
            run_id="r1",
            completed_at=completed,
            input_tokens=1_500,
        )
        store.model_call_usage.append(
            RuntimeModelCallUsageRecord(
                id="call-a",
                org_id="org_a",
                run_id="r1",
                conversation_id=conv_id,
                trace_id="trace-1",
                model_provider="openai",
                model_name="gpt-5.4-mini",
                input_tokens=900,
                output_tokens=120,
                total_tokens=1_020,
                duration_ms=300,
            )
        )
        store.model_call_usage.append(
            RuntimeModelCallUsageRecord(
                id="call-b",
                org_id="org_a",
                run_id="r1",
                conversation_id=conv_id,
                trace_id="trace-2",
                subagent_id="sub-x",
                model_provider="openai",
                model_name="gpt-5.4-mini",
                input_tokens=600,
                output_tokens=80,
                total_tokens=680,
                duration_ms=400,
            )
        )
        response = client.get(
            f"/v1/agent/conversations/{conv_id}/context",
            params={"org_id": "org_a", "user_id": "user_1"},
        )
        body = response.json()
        assert len(body["breakdown"]["by_call"]) == 2
        assert len(body["breakdown"]["by_subagent"]) == 1
        assert body["breakdown"]["by_subagent"][0]["subagent_id"] == "sub-x"

    async def test_unknown_model_returns_null_headroom(self) -> None:
        client, store, conv_id = await _bootstrap(seed_pricing=False)
        completed = datetime.now(timezone.utc) - timedelta(minutes=1)
        _seed_run(
            store,
            conversation_id=conv_id,
            run_id="r1",
            completed_at=completed,
            input_tokens=10_000,
        )
        response = client.get(
            f"/v1/agent/conversations/{conv_id}/context",
            params={"org_id": "org_a", "user_id": "user_1"},
        )
        body = response.json()
        assert body["model"]["context_window_tokens"] is None
        assert body["current"]["headroom_pct"] is None
        assert body["current"]["available_tokens"] is None

    async def test_archived_conversation_still_returns_context(self) -> None:
        # Archive shouldn't 404 the context view — users open /context to
        # inspect a conversation they just archived.
        client, store, conv_id = await _bootstrap()
        store.conversations[conv_id] = store.conversations[conv_id].model_copy(
            update={"status": ConversationStatus.ARCHIVED}
        )
        response = client.get(
            f"/v1/agent/conversations/{conv_id}/context",
            params={"org_id": "org_a", "user_id": "user_1"},
        )
        assert response.status_code == 200
