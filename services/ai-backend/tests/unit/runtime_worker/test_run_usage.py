"""End-to-end tests for B1 (run-usage write) and B3 (cost stamp) hooks."""

from __future__ import annotations

from datetime import datetime, timezone


from agent_runtime.persistence.records import (
    ModelPricingRecord,
    RuntimeRunUsageRecord,
)
from runtime_api.schemas import ConversationRecord
from runtime_adapters.in_memory import InMemoryRuntimeApiStore


def _pricing() -> ModelPricingRecord:
    return ModelPricingRecord(
        provider="openai",
        model_name="gpt-5.4-mini",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        input_per_1m_micro_usd=1_500_000,
        output_per_1m_micro_usd=6_000_000,
        cached_input_per_1m_micro_usd=150_000,
        pricing_version="openai-2026-q1.v1",
    )


def _usage(
    *,
    run_id: str,
    org_id: str = "org_a",
    conversation_id: str = "conv_1",
    cost_micro_usd: int | None = None,
) -> RuntimeRunUsageRecord:
    return RuntimeRunUsageRecord(
        id=run_id,
        org_id=org_id,
        user_id="user_1",
        conversation_id=conversation_id,
        run_id=run_id,
        model_provider="openai",
        model_name="gpt-5.4-mini",
        input_tokens=1_000,
        output_tokens=500,
        cached_input_tokens=200,
        total_tokens=1_500,
        chunk_count=3,
        duration_ms=4_200,
        started_at=datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 5, 4, 10, 0, 4, tzinfo=timezone.utc),
        status="completed",
        cost_micro_usd=cost_micro_usd,
    )


class TestRunUsageInMemoryStore:
    def test_record_run_usage_inserts_row(self) -> None:
        store = InMemoryRuntimeApiStore()
        store.record_run_usage(_usage(run_id="run_1"))
        assert "run_1" in store.run_usage
        row = store.run_usage["run_1"]
        assert row.input_tokens == 1_000
        assert row.cost_micro_usd is None  # B3 hook stamps later

    def test_record_run_usage_is_idempotent(self) -> None:
        store = InMemoryRuntimeApiStore()
        store.record_run_usage(_usage(run_id="run_1"))
        # Second write with different values -> no-op (run-completion event
        # is the source of truth; usage row is derived).
        modified = _usage(run_id="run_1")
        modified = modified.model_copy(update={"input_tokens": 9_999})
        store.record_run_usage(modified)
        assert store.run_usage["run_1"].input_tokens == 1_000

    def test_update_run_usage_cost_stamps_columns(self) -> None:
        store = InMemoryRuntimeApiStore()
        store.record_run_usage(_usage(run_id="run_1"))
        store.update_run_usage_cost(
            run_id="run_1",
            cost_micro_usd=4_500,
            pricing_id="pricing-abc",
            pricing_version="anthropic-2026-q1.v1",
        )
        row = store.run_usage["run_1"]
        assert row.cost_micro_usd == 4_500
        assert row.pricing_id == "pricing-abc"
        assert row.pricing_version == "anthropic-2026-q1.v1"

    def test_update_cost_no_op_when_run_missing(self) -> None:
        store = InMemoryRuntimeApiStore()
        # Should not raise; no row for this run_id yet.
        store.update_run_usage_cost(
            run_id="missing_run",
            cost_micro_usd=1,
            pricing_id="x",
            pricing_version="y",
        )
        assert "missing_run" not in store.run_usage

    def test_query_run_usage_scoped_by_org(self) -> None:
        store = InMemoryRuntimeApiStore()
        store.record_run_usage(_usage(run_id="run_1", org_id="org_a"))
        store.record_run_usage(_usage(run_id="run_2", org_id="org_b"))
        assert store.query_run_usage(org_id="org_a", run_id="run_1") is not None
        # Cross-tenant lookup returns None.
        assert store.query_run_usage(org_id="org_b", run_id="run_1") is None

    def test_query_top_conversations_excludes_pii_purged(self) -> None:
        store = InMemoryRuntimeApiStore()
        store.insert_forked_conversation(
            ConversationRecord(
                conversation_id="conv_1",
                org_id="org_a",
                user_id="user_1",
                assistant_id="assistant_default",
                title="Quarterly planning",
            )
        )
        store.record_run_usage(_usage(run_id="run_1"))
        store.record_run_usage(_usage(run_id="run_3", cost_micro_usd=4_500))
        purged = _usage(run_id="run_2").model_copy(
            update={"pii_purged_at": datetime(2026, 6, 1, tzinfo=timezone.utc)}
        )
        store.record_run_usage(purged)
        rows = store.query_top_conversations(
            org_id="org_a",
            user_id="user_1",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 12, 31, tzinfo=timezone.utc),
            limit=10,
        )
        # run_2 was purged so its tokens are excluded.
        assert len(rows) == 1
        row = rows[0]
        assert row.conversation_id == "conv_1"
        assert row.title == "Quarterly planning"
        assert row.input_tokens == 2_000
        assert row.output_tokens == 1_000
        assert row.cached_input_tokens == 400
        assert row.total_tokens == 3_000
        assert row.runs_count == 2
        assert row.cost_micro_usd == 4_500


class TestPricingUpsertReplacesActiveRow:
    def test_later_pricing_closes_prior_active(self) -> None:
        store = InMemoryRuntimeApiStore()
        v1 = _pricing()
        store.upsert_pricing(v1)
        v2 = v1.model_copy(
            update={
                "id": "different",
                "effective_from": datetime(2026, 4, 1, tzinfo=timezone.utc),
                "pricing_version": "openai-2026-q2.v1",
                "input_per_1m_micro_usd": 1_200_000,
            }
        )
        store.upsert_pricing(v2)
        # The old row's effective_until should now equal v2's effective_from.
        rows = [
            r for r in store.pricing_rows if r.pricing_version == v1.pricing_version
        ]
        assert len(rows) == 1
        assert rows[0].effective_until == v2.effective_from
        # Lookup at the boundary returns v2 (effective_until is exclusive).
        active = store.lookup_pricing(
            provider="openai",
            model_name="gpt-5.4-mini",
            region="global",
            at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        )
        assert active is not None
        assert active.pricing_version == "openai-2026-q2.v1"

    def test_lookup_at_historical_time_returns_old_pricing(self) -> None:
        store = InMemoryRuntimeApiStore()
        v1 = _pricing()
        store.upsert_pricing(v1)
        v2 = v1.model_copy(
            update={
                "id": "v2",
                "effective_from": datetime(2026, 4, 1, tzinfo=timezone.utc),
                "pricing_version": "openai-2026-q2.v1",
            }
        )
        store.upsert_pricing(v2)
        # Run completed in March -> still v1 pricing.
        historical = store.lookup_pricing(
            provider="openai",
            model_name="gpt-5.4-mini",
            region="global",
            at=datetime(2026, 3, 15, tzinfo=timezone.utc),
        )
        assert historical is not None
        assert historical.pricing_version == "openai-2026-q1.v1"
