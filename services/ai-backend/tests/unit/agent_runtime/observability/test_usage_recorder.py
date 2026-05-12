"""Tests for :class:`UsageRecorder` and its three implementations (01c).

The recorder is the boundary that replaces ``handlers/run.py``'s two
parallel writer methods. These tests pin:

- Production impl writes row + stamps cost via pricing catalog.
- Pricing miss → ``cost_micro_usd is None``; no cost UPDATE issued.
- Insert raises → cost stamp never attempted; result empty.
- Cost stamp raises → row remains in place; result empty.
- In-memory fake captures records in insertion order.
- Null impl accepts and discards.
- Summarization scaffold routes through the same boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


from agent_runtime.observability.usage_recorder import (
    InMemoryUsageRecorder,
    NullUsageRecorder,
    PostgresUsageRecorder,
    SummarizationUsageRecorder,
    UsageRecorder,
    UsageRecordingResult,
)
from agent_runtime.persistence.records import (
    ModelPricingRecord,
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _FakePersistence:
    """Minimal PersistencePort stand-in for the four methods the
    recorder calls. Captures all writes + lets tests inject raise-on-call
    flags to exercise fail-soft paths."""

    raise_on_call_insert: bool = False
    raise_on_call_cost: bool = False
    raise_on_run_insert: bool = False
    raise_on_run_cost: bool = False
    calls: list[RuntimeModelCallUsageRecord] = field(default_factory=list)
    runs: list[RuntimeRunUsageRecord] = field(default_factory=list)
    call_cost_updates: list[dict[str, Any]] = field(default_factory=list)
    run_cost_updates: list[dict[str, Any]] = field(default_factory=list)

    async def record_model_call_usage(
        self, record: RuntimeModelCallUsageRecord
    ) -> None:
        if self.raise_on_call_insert:
            raise RuntimeError("insert blew up")
        self.calls.append(record)

    async def record_run_usage(self, record: RuntimeRunUsageRecord) -> None:
        if self.raise_on_run_insert:
            raise RuntimeError("insert blew up")
        self.runs.append(record)

    async def update_model_call_usage_cost(
        self,
        *,
        usage_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
        if self.raise_on_call_cost:
            raise RuntimeError("cost update blew up")
        self.call_cost_updates.append(
            {
                "usage_id": usage_id,
                "cost_micro_usd": cost_micro_usd,
                "pricing_id": pricing_id,
                "pricing_version": pricing_version,
            }
        )

    async def update_run_usage_cost(
        self,
        *,
        run_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
        if self.raise_on_run_cost:
            raise RuntimeError("cost update blew up")
        self.run_cost_updates.append(
            {
                "run_id": run_id,
                "cost_micro_usd": cost_micro_usd,
                "pricing_id": pricing_id,
                "pricing_version": pricing_version,
            }
        )


@dataclass
class _FakePricingCatalog:
    """ModelPricingCatalog stand-in. Returns a pre-baked
    ``ModelPricingRecord`` or ``None`` (miss) on lookup."""

    pricing: ModelPricingRecord | None
    lookup_calls: list[dict[str, Any]] = field(default_factory=list)

    async def lookup(
        self,
        *,
        provider: str,
        model_name: str,
        region: str,
        at: datetime,
    ) -> ModelPricingRecord | None:
        self.lookup_calls.append(
            {
                "provider": provider,
                "model_name": model_name,
                "region": region,
                "at": at,
            }
        )
        return self.pricing


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _model_call_record(**overrides: Any) -> RuntimeModelCallUsageRecord:
    base: dict[str, Any] = {
        "id": "msg_call_1",
        "org_id": "org_1",
        "run_id": "run_1",
        "conversation_id": "conv_1",
        "trace_id": "trace_1",
        "model_provider": "openai",
        "model_name": "gpt-5.4-mini",
        "input_tokens": 1000,
        "output_tokens": 200,
        "cached_input_tokens": 400,
        "total_tokens": 1200,
    }
    base.update(overrides)
    return RuntimeModelCallUsageRecord(**base)


def _run_usage_record(**overrides: Any) -> RuntimeRunUsageRecord:
    base: dict[str, Any] = {
        "id": "run_1",
        "org_id": "org_1",
        "user_id": "user_1",
        "conversation_id": "conv_1",
        "run_id": "run_1",
        "model_provider": "openai",
        "model_name": "gpt-5.4-mini",
        "input_tokens": 1500,
        "output_tokens": 350,
        "cached_input_tokens": 400,
        "total_tokens": 1850,
        "started_at": datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 5, 11, 10, 5, tzinfo=timezone.utc),
        "status": "completed",
    }
    base.update(overrides)
    return RuntimeRunUsageRecord(**base)


def _pricing_record() -> ModelPricingRecord:
    return ModelPricingRecord(
        id=uuid4().hex,
        provider="openai",
        model_name="gpt-5.4-mini",
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        input_per_1m_micro_usd=150_000,  # $0.15 per million
        output_per_1m_micro_usd=600_000,
        cached_input_per_1m_micro_usd=75_000,
        pricing_version="v1",
    )


def _pricing_at() -> datetime:
    return datetime(2026, 5, 11, 10, 5, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Protocol-level — every impl satisfies the runtime_checkable Protocol
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_postgres_recorder_is_a_usage_recorder(self) -> None:
        recorder = PostgresUsageRecorder(
            persistence=_FakePersistence(),  # type: ignore[arg-type]
            pricing_catalog=_FakePricingCatalog(pricing=None),  # type: ignore[arg-type]
        )
        assert isinstance(recorder, UsageRecorder)

    def test_in_memory_recorder_is_a_usage_recorder(self) -> None:
        assert isinstance(InMemoryUsageRecorder(), UsageRecorder)

    def test_null_recorder_is_a_usage_recorder(self) -> None:
        assert isinstance(NullUsageRecorder(), UsageRecorder)


# ---------------------------------------------------------------------------
# PostgresUsageRecorder — the production impl
# ---------------------------------------------------------------------------


class TestPostgresUsageRecorderHappyPath:
    async def test_record_call_writes_row_and_stamps_cost(self) -> None:
        persistence = _FakePersistence()
        pricing = _pricing_record()
        catalog = _FakePricingCatalog(pricing=pricing)
        recorder = PostgresUsageRecorder(
            persistence=persistence,  # type: ignore[arg-type]
            pricing_catalog=catalog,  # type: ignore[arg-type]
        )

        record = _model_call_record()
        result = await recorder.record_call(record, pricing_at=_pricing_at())

        assert len(persistence.calls) == 1
        assert persistence.calls[0].id == record.id
        assert len(persistence.call_cost_updates) == 1
        assert persistence.call_cost_updates[0]["usage_id"] == record.id
        assert result.cost_micro_usd is not None
        assert result.cost_micro_usd > 0
        assert result.pricing_id == pricing.id
        assert result.pricing_version == pricing.pricing_version

    async def test_record_run_writes_row_and_stamps_cost(self) -> None:
        persistence = _FakePersistence()
        pricing = _pricing_record()
        catalog = _FakePricingCatalog(pricing=pricing)
        recorder = PostgresUsageRecorder(
            persistence=persistence,  # type: ignore[arg-type]
            pricing_catalog=catalog,  # type: ignore[arg-type]
        )

        record = _run_usage_record()
        result = await recorder.record_run(record, pricing_at=_pricing_at())

        assert len(persistence.runs) == 1
        assert persistence.runs[0].run_id == record.run_id
        assert len(persistence.run_cost_updates) == 1
        assert persistence.run_cost_updates[0]["run_id"] == record.run_id
        assert result.cost_micro_usd is not None
        assert result.cost_micro_usd > 0

    async def test_pricing_lookup_uses_caller_supplied_at(self) -> None:
        """``pricing_at`` is the caller's pin — same value for both
        per-call and run-level inside one run."""

        persistence = _FakePersistence()
        catalog = _FakePricingCatalog(pricing=_pricing_record())
        recorder = PostgresUsageRecorder(
            persistence=persistence,  # type: ignore[arg-type]
            pricing_catalog=catalog,  # type: ignore[arg-type]
        )
        pricing_at = _pricing_at()
        await recorder.record_run(_run_usage_record(), pricing_at=pricing_at)
        await recorder.record_call(_model_call_record(), pricing_at=pricing_at)

        assert len(catalog.lookup_calls) == 2
        assert catalog.lookup_calls[0]["at"] == pricing_at
        assert catalog.lookup_calls[1]["at"] == pricing_at


class TestPostgresUsageRecorderPricingMiss:
    async def test_call_with_no_pricing_returns_empty_result(self) -> None:
        persistence = _FakePersistence()
        catalog = _FakePricingCatalog(pricing=None)
        recorder = PostgresUsageRecorder(
            persistence=persistence,  # type: ignore[arg-type]
            pricing_catalog=catalog,  # type: ignore[arg-type]
        )

        result = await recorder.record_call(
            _model_call_record(), pricing_at=_pricing_at()
        )

        # Row was inserted; cost UPDATE was NOT issued; result empty.
        assert len(persistence.calls) == 1
        assert persistence.call_cost_updates == []
        assert result == UsageRecordingResult()

    async def test_run_with_no_pricing_returns_empty_result(self) -> None:
        persistence = _FakePersistence()
        catalog = _FakePricingCatalog(pricing=None)
        recorder = PostgresUsageRecorder(
            persistence=persistence,  # type: ignore[arg-type]
            pricing_catalog=catalog,  # type: ignore[arg-type]
        )

        result = await recorder.record_run(
            _run_usage_record(), pricing_at=_pricing_at()
        )

        assert len(persistence.runs) == 1
        assert persistence.run_cost_updates == []
        assert result == UsageRecordingResult()


class TestPostgresUsageRecorderFailSoft:
    async def test_insert_call_raise_returns_empty_no_cost_stamp(self) -> None:
        persistence = _FakePersistence(raise_on_call_insert=True)
        catalog = _FakePricingCatalog(pricing=_pricing_record())
        recorder = PostgresUsageRecorder(
            persistence=persistence,  # type: ignore[arg-type]
            pricing_catalog=catalog,  # type: ignore[arg-type]
        )

        # Must not raise.
        result = await recorder.record_call(
            _model_call_record(), pricing_at=_pricing_at()
        )

        assert persistence.calls == []
        assert persistence.call_cost_updates == []
        # Pricing catalog never consulted when insert failed.
        assert catalog.lookup_calls == []
        assert result == UsageRecordingResult()

    async def test_insert_run_raise_returns_empty_no_cost_stamp(self) -> None:
        persistence = _FakePersistence(raise_on_run_insert=True)
        catalog = _FakePricingCatalog(pricing=_pricing_record())
        recorder = PostgresUsageRecorder(
            persistence=persistence,  # type: ignore[arg-type]
            pricing_catalog=catalog,  # type: ignore[arg-type]
        )

        result = await recorder.record_run(
            _run_usage_record(), pricing_at=_pricing_at()
        )

        assert persistence.runs == []
        assert persistence.run_cost_updates == []
        assert catalog.lookup_calls == []
        assert result == UsageRecordingResult()

    async def test_cost_stamp_raise_returns_empty_but_row_persisted(self) -> None:
        """Insert succeeded; cost UPDATE failed. The row stays in place
        with cost_micro_usd NULL. Result is empty so the caller's budget
        charger sees no cost."""

        persistence = _FakePersistence(raise_on_call_cost=True)
        catalog = _FakePricingCatalog(pricing=_pricing_record())
        recorder = PostgresUsageRecorder(
            persistence=persistence,  # type: ignore[arg-type]
            pricing_catalog=catalog,  # type: ignore[arg-type]
        )

        result = await recorder.record_call(
            _model_call_record(), pricing_at=_pricing_at()
        )

        # The INSERT happened.
        assert len(persistence.calls) == 1
        # The cost UPDATE was attempted (it raised) — no rows recorded.
        assert persistence.call_cost_updates == []
        # Pricing was consulted.
        assert len(catalog.lookup_calls) == 1
        assert result == UsageRecordingResult()

    async def test_run_cost_stamp_raise_returns_empty_but_row_persisted(
        self,
    ) -> None:
        persistence = _FakePersistence(raise_on_run_cost=True)
        catalog = _FakePricingCatalog(pricing=_pricing_record())
        recorder = PostgresUsageRecorder(
            persistence=persistence,  # type: ignore[arg-type]
            pricing_catalog=catalog,  # type: ignore[arg-type]
        )

        result = await recorder.record_run(
            _run_usage_record(), pricing_at=_pricing_at()
        )

        assert len(persistence.runs) == 1
        assert persistence.run_cost_updates == []
        assert result == UsageRecordingResult()


# ---------------------------------------------------------------------------
# InMemoryUsageRecorder
# ---------------------------------------------------------------------------


class TestInMemoryUsageRecorder:
    async def test_captures_calls_and_runs_in_insertion_order(self) -> None:
        recorder = InMemoryUsageRecorder()
        await recorder.record_call(
            _model_call_record(id="msg_a"), pricing_at=_pricing_at()
        )
        await recorder.record_run(_run_usage_record(), pricing_at=_pricing_at())
        await recorder.record_call(
            _model_call_record(id="msg_b"), pricing_at=_pricing_at()
        )

        assert [c.id for c in recorder.calls] == ["msg_a", "msg_b"]
        assert [r.run_id for r in recorder.runs] == ["run_1"]
        assert len(recorder.results) == 3

    async def test_no_pricing_catalog_leaves_cost_none(self) -> None:
        recorder = InMemoryUsageRecorder()
        result = await recorder.record_call(
            _model_call_record(), pricing_at=_pricing_at()
        )
        assert result == UsageRecordingResult()

    async def test_with_pricing_catalog_computes_cost(self) -> None:
        catalog = _FakePricingCatalog(pricing=_pricing_record())
        recorder = InMemoryUsageRecorder(pricing_catalog=catalog)  # type: ignore[arg-type]
        result = await recorder.record_call(
            _model_call_record(), pricing_at=_pricing_at()
        )
        assert result.cost_micro_usd is not None
        assert result.cost_micro_usd > 0


# ---------------------------------------------------------------------------
# NullUsageRecorder
# ---------------------------------------------------------------------------


class TestNullUsageRecorder:
    async def test_record_call_returns_empty_result(self) -> None:
        recorder = NullUsageRecorder()
        result = await recorder.record_call(
            _model_call_record(), pricing_at=_pricing_at()
        )
        assert result == UsageRecordingResult()

    async def test_record_run_returns_empty_result(self) -> None:
        recorder = NullUsageRecorder()
        result = await recorder.record_run(
            _run_usage_record(), pricing_at=_pricing_at()
        )
        assert result == UsageRecordingResult()


# ---------------------------------------------------------------------------
# SummarizationUsageRecorder scaffold
# ---------------------------------------------------------------------------


class TestSummarizationScaffold:
    async def test_routes_through_underlying_recorder(self) -> None:
        underlying = InMemoryUsageRecorder()
        helper = SummarizationUsageRecorder(recorder=underlying)
        record = _model_call_record(purpose="context_compression")
        result = await helper.record_summarization_call(
            record, pricing_at=_pricing_at()
        )
        assert underlying.calls == [record]
        # No pricing catalog on the underlying fake → empty cost.
        assert result == UsageRecordingResult()
