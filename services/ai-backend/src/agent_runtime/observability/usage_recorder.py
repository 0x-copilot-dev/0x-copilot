"""Single boundary for persisting LLM token usage and cost.

:class:`UsageRecorder` collapses per-call and per-run usage writes into one
boundary: row insertion, pricing lookup, cost stamping, and fail-soft error
handling. Public methods never propagate exceptions — the run lifecycle must not
break because a usage row failed to persist.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from agent_runtime.api.ports import PersistencePort
from agent_runtime.persistence.records import (
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
)
from agent_runtime.pricing.calculator import CostCalculator
from agent_runtime.pricing.catalog import ModelPricingCatalog


@dataclass(frozen=True)
class UsageRecordingResult:
    """Outcome of one recorder write.

    - ``cost_micro_usd`` is the cost stamped on the row, or ``None``
      when pricing was unavailable (catalog miss) or the row write
      itself failed.
    - ``pricing_id`` / ``pricing_version`` mirror the row's snapshot
      columns; both are ``None`` whenever ``cost_micro_usd`` is ``None``.

    The handler reads ``cost_micro_usd`` to drive
    :class:`BudgetCharger`. No mutable-local-scope passing — the
    recorder's typed return value is the only channel.
    """

    cost_micro_usd: int | None = None
    pricing_id: str | None = None
    pricing_version: str | None = None


@runtime_checkable
class UsageRecorder(Protocol):
    """Single boundary for persisting LLM token usage + cost.

    All methods are fail-soft: failures log and return a result with
    ``cost_micro_usd is None``. The run lifecycle never breaks because
    a usage row couldn't persist. ``pricing_at`` is supplied by the
    caller so a run can pin one pricing snapshot regardless of clock
    drift across the call.
    """

    async def record_call(
        self,
        record: RuntimeModelCallUsageRecord,
        *,
        pricing_at: datetime,
    ) -> UsageRecordingResult: ...

    async def record_run(
        self,
        record: RuntimeRunUsageRecord,
        *,
        pricing_at: datetime,
    ) -> UsageRecordingResult: ...


class _RecorderLogger:
    """Class-scoped logger names so structured-log queries pin them.

    Kept inside a class so the constants travel with the recorder
    module (no module-level "magic strings" floating in the worker).
    """

    EVENT_CALL_WRITE_FAILED = "runtime_model_call_usage_write_failed"
    EVENT_CALL_COST_FAILED = "runtime_model_call_usage_cost_write_failed"
    EVENT_RUN_WRITE_FAILED = "runtime_run_usage_write_failed"
    EVENT_RUN_COST_FAILED = "runtime_run_usage_cost_write_failed"


class PostgresUsageRecorder:
    """Production :class:`UsageRecorder` — writes through PersistencePort.

    Dependencies are injected. The recorder doesn't know how
    persistence or pricing are sourced; tests can hand in fakes for
    either or both.

    Per-call and per-run flows share an identical shape: insert the
    row, then look up pricing, then stamp cost. The two cost-stamp
    operations remain distinct port calls (today's surface); the
    recorder hides that detail from callers.
    """

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        pricing_catalog: ModelPricingCatalog,
        logger: logging.Logger | None = None,
    ) -> None:
        self._persistence = persistence
        self._pricing_catalog = pricing_catalog
        self._logger = logger or logging.getLogger("agent_runtime.usage_recorder")

    async def record_call(
        self,
        record: RuntimeModelCallUsageRecord,
        *,
        pricing_at: datetime,
    ) -> UsageRecordingResult:
        if not await self._safe_insert_call(record):
            return UsageRecordingResult()
        return await self._safe_stamp_call_cost(record, pricing_at=pricing_at)

    async def record_run(
        self,
        record: RuntimeRunUsageRecord,
        *,
        pricing_at: datetime,
    ) -> UsageRecordingResult:
        if not await self._safe_insert_run(record):
            return UsageRecordingResult()
        return await self._safe_stamp_run_cost(record, pricing_at=pricing_at)

    async def _safe_insert_call(self, record: RuntimeModelCallUsageRecord) -> bool:
        try:
            await self._persistence.record_model_call_usage(record)
            return True
        except Exception:
            self._logger.warning(
                _RecorderLogger.EVENT_CALL_WRITE_FAILED,
                extra={"metadata": {"run_id": record.run_id, "usage_id": record.id}},
                exc_info=True,
            )
            return False

    async def _safe_insert_run(self, record: RuntimeRunUsageRecord) -> bool:
        try:
            await self._persistence.record_run_usage(record)
            return True
        except Exception:
            self._logger.warning(
                _RecorderLogger.EVENT_RUN_WRITE_FAILED,
                extra={"metadata": {"run_id": record.run_id}},
                exc_info=True,
            )
            return False

    async def _safe_stamp_call_cost(
        self,
        record: RuntimeModelCallUsageRecord,
        *,
        pricing_at: datetime,
    ) -> UsageRecordingResult:
        try:
            pricing = await self._pricing_catalog.lookup(
                provider=record.model_provider,
                model_name=record.model_name,
                region="global",
                at=pricing_at,
            )
            if pricing is None:
                return UsageRecordingResult()
            cost = CostCalculator.compute(
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cached_input_tokens=record.cached_input_tokens,
                pricing=pricing,
            )
            await self._persistence.update_model_call_usage_cost(
                usage_id=record.id,
                cost_micro_usd=cost,
                pricing_id=pricing.id,
                pricing_version=pricing.pricing_version,
            )
            return UsageRecordingResult(
                cost_micro_usd=cost,
                pricing_id=pricing.id,
                pricing_version=pricing.pricing_version,
            )
        except Exception:
            self._logger.warning(
                _RecorderLogger.EVENT_CALL_COST_FAILED,
                extra={"metadata": {"run_id": record.run_id, "usage_id": record.id}},
                exc_info=True,
            )
            return UsageRecordingResult()

    async def _safe_stamp_run_cost(
        self,
        record: RuntimeRunUsageRecord,
        *,
        pricing_at: datetime,
    ) -> UsageRecordingResult:
        try:
            pricing = await self._pricing_catalog.lookup(
                provider=record.model_provider,
                model_name=record.model_name,
                region="global",
                at=pricing_at,
            )
            if pricing is None:
                return UsageRecordingResult()
            cost = CostCalculator.compute(
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cached_input_tokens=record.cached_input_tokens,
                pricing=pricing,
            )
            await self._persistence.update_run_usage_cost(
                run_id=record.run_id,
                cost_micro_usd=cost,
                pricing_id=pricing.id,
                pricing_version=pricing.pricing_version,
            )
            return UsageRecordingResult(
                cost_micro_usd=cost,
                pricing_id=pricing.id,
                pricing_version=pricing.pricing_version,
            )
        except Exception:
            self._logger.warning(
                _RecorderLogger.EVENT_RUN_COST_FAILED,
                extra={"metadata": {"run_id": record.run_id}},
                exc_info=True,
            )
            return UsageRecordingResult()


@dataclass
class InMemoryUsageRecorder:
    """Test fake :class:`UsageRecorder` — captures records for assertions.

    Tests assert against ``recorder.calls`` / ``recorder.runs`` directly
    rather than reading back from a fake persistence store. Cost
    stamping is optional: when ``pricing_catalog`` is injected, cost is
    computed against it; otherwise results carry ``cost_micro_usd=None``.
    """

    pricing_catalog: ModelPricingCatalog | None = None
    calls: list[RuntimeModelCallUsageRecord] = field(default_factory=list)
    runs: list[RuntimeRunUsageRecord] = field(default_factory=list)
    results: list[UsageRecordingResult] = field(default_factory=list)

    async def record_call(
        self,
        record: RuntimeModelCallUsageRecord,
        *,
        pricing_at: datetime,
    ) -> UsageRecordingResult:
        self.calls.append(record)
        result = await self._maybe_compute_cost(
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cached_input_tokens=record.cached_input_tokens,
            model_provider=record.model_provider,
            model_name=record.model_name,
            pricing_at=pricing_at,
        )
        self.results.append(result)
        return result

    async def record_run(
        self,
        record: RuntimeRunUsageRecord,
        *,
        pricing_at: datetime,
    ) -> UsageRecordingResult:
        self.runs.append(record)
        result = await self._maybe_compute_cost(
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cached_input_tokens=record.cached_input_tokens,
            model_provider=record.model_provider,
            model_name=record.model_name,
            pricing_at=pricing_at,
        )
        self.results.append(result)
        return result

    async def _maybe_compute_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int,
        model_provider: str,
        model_name: str,
        pricing_at: datetime,
    ) -> UsageRecordingResult:
        if self.pricing_catalog is None:
            return UsageRecordingResult()
        pricing = await self.pricing_catalog.lookup(
            provider=model_provider,
            model_name=model_name,
            region="global",
            at=pricing_at,
        )
        if pricing is None:
            return UsageRecordingResult()
        cost = CostCalculator.compute(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            pricing=pricing,
        )
        return UsageRecordingResult(
            cost_micro_usd=cost,
            pricing_id=pricing.id,
            pricing_version=pricing.pricing_version,
        )


class NullUsageRecorder:
    """Accept-and-discard :class:`UsageRecorder` for replay / dev modes.

    Used wherever the runtime is replaying historical events without
    re-stamping costs (e.g. event-store replays). The recorder
    accepts every record and returns an empty result.
    """

    async def record_call(
        self,
        record: RuntimeModelCallUsageRecord,
        *,
        pricing_at: datetime,
    ) -> UsageRecordingResult:
        return UsageRecordingResult()

    async def record_run(
        self,
        record: RuntimeRunUsageRecord,
        *,
        pricing_at: datetime,
    ) -> UsageRecordingResult:
        return UsageRecordingResult()


class SummarizationUsageRecorder:
    """Architectural boundary for future summarization wiring (D4).

    ``agent_runtime/context/memory/summarization.py`` is dead code
    today — the SDK closure surface exists but no production caller
    enables it. When summarization is wired in a future PR, the
    summarizer's LLM response MUST route through this helper so the
    recorder gets the row with ``purpose=CONTEXT_COMPRESSION``.

    This class is intentionally small. It exists so the contract is
    in place: a future enable-summarization PR is a one-line wire-up,
    not a re-architecture.

    See ``docs/refactor/01-usage-capture-and-attribution.md`` §1.5
    (D4 problem statement) and ``01c-usage-recorder.md`` §4.6.
    """

    def __init__(self, *, recorder: UsageRecorder) -> None:
        self._recorder = recorder

    async def record_summarization_call(
        self,
        record: RuntimeModelCallUsageRecord,
        *,
        pricing_at: datetime,
    ) -> UsageRecordingResult:
        """Route a context-compression LLM call's usage through the
        single recorder boundary.

        Callers MUST build ``record`` with ``purpose='context_compression'``
        (the column default is ``'main'``; future summarization wiring
        sets this explicitly). The helper does not validate the
        purpose — it's a thin pass-through that documents intent.
        """

        return await self._recorder.record_call(record, pricing_at=pricing_at)
