"""Public response schemas for the ``/v1/usage/*`` endpoints (B4).

All cost figures are micro-USD integers (1 USD = 1_000_000 micro_usd) — never
floats — and are ``None`` when the model has no priced row in
``model_pricing``. The currency code is returned alongside so the UI never
has to infer it.

Period semantics (handled by ``UsageQueryService.parse_period``):

- ``today``  → [today_00:00 UTC, now)
- ``7d``     → last 7 days, inclusive of today.
- ``30d``    → last 30 days, inclusive of today.
- ``month``  → first-of-month 00:00 UTC through now.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, NonNegativeInt

from agent_runtime.execution.contracts import RuntimeContract


UsagePeriod = Literal["today", "7d", "30d", "month"]


class UsageTotals(RuntimeContract):
    """Token + cost totals for one slice (a model, a day, or the whole period)."""

    input: NonNegativeInt = 0
    output: NonNegativeInt = 0
    cached_input: NonNegativeInt = 0
    total: NonNegativeInt = 0
    runs_count: NonNegativeInt = 0
    cost_micro_usd: int | None = None


class UsageDailyRow(RuntimeContract):
    """One day in a per-day breakdown."""

    day: str  # ISO-8601 date, e.g. "2026-05-04"
    input: NonNegativeInt = 0
    output: NonNegativeInt = 0
    cached_input: NonNegativeInt = 0
    total: NonNegativeInt = 0
    runs_count: NonNegativeInt = 0
    cost_micro_usd: int | None = None


class UsageModelRow(RuntimeContract):
    """One model in a by-model breakdown."""

    provider: str
    model: str
    input: NonNegativeInt = 0
    output: NonNegativeInt = 0
    cached_input: NonNegativeInt = 0
    total: NonNegativeInt = 0
    runs_count: NonNegativeInt = 0
    cost_micro_usd: int | None = None


class UsageConversationRow(RuntimeContract):
    """One conversation in a top-conversations breakdown."""

    conversation_id: str
    title: str | None = None
    input: NonNegativeInt = 0
    output: NonNegativeInt = 0
    cached_input: NonNegativeInt = 0
    total: NonNegativeInt = 0
    runs_count: NonNegativeInt = 0
    cost_micro_usd: int | None = None


class UsagePeriodWindow(RuntimeContract):
    """Inclusive start, exclusive end of the period being reported."""

    start: datetime
    end: datetime


class UsageMeResponse(RuntimeContract):
    """Response shape for ``GET /v1/usage/me``."""

    period: UsagePeriodWindow
    currency: Literal["USD"] = "USD"
    total: UsageTotals
    by_day: tuple[UsageDailyRow, ...] = ()
    by_model: tuple[UsageModelRow, ...] = ()
    cold_start_fallback: bool = Field(
        default=False,
        description=(
            "True when rollups were empty for this range and the response "
            "was computed via a direct scan of runtime_run_usage."
        ),
    )


class UsageOrgResponse(RuntimeContract):
    """Response shape for ``GET /v1/usage/org`` (admin only)."""

    period: UsagePeriodWindow
    currency: Literal["USD"] = "USD"
    total: UsageTotals
    by_day: tuple[UsageDailyRow, ...] = ()
    by_model: tuple[UsageModelRow, ...] = ()
    by_user: tuple[UsageConversationRow, ...] = ()
    cold_start_fallback: bool = False


class RunUsageBreakdown(RuntimeContract):
    """Response shape for ``GET /v1/usage/runs/{run_id}``.

    Joins B1 (run-level row) with B2 (per-call rows) so callers see the
    aggregate plus the per-LLM-call breakdown in one payload.
    """

    run_id: str
    org_id: str
    user_id: str
    conversation_id: str
    model_provider: str
    model_name: str
    started_at: datetime
    completed_at: datetime
    duration_ms: NonNegativeInt
    chunk_count: NonNegativeInt
    status: str
    total: UsageTotals
    by_call: tuple["RunUsageCallRow", ...] = ()


class RunUsageCallRow(RuntimeContract):
    """One LLM call inside ``RunUsageBreakdown.by_call``."""

    id: str
    parent_event_id: str | None = None
    task_id: str | None = None
    subagent_id: str | None = None
    model_provider: str
    model_name: str
    input: NonNegativeInt = 0
    output: NonNegativeInt = 0
    cached_input: NonNegativeInt = 0
    total: NonNegativeInt = 0
    duration_ms: NonNegativeInt = 0
    cost_micro_usd: int | None = None
    created_at: datetime


class ConversationUsageResponse(RuntimeContract):
    """Response shape for ``GET /v1/usage/conversations/{conversation_id}``."""

    conversation_id: str
    period: UsagePeriodWindow
    currency: Literal["USD"] = "USD"
    total: UsageTotals
    by_run: tuple["UsageRunRow", ...] = ()


class UsageRunRow(RuntimeContract):
    """One run inside a per-conversation total."""

    run_id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: str
    total: UsageTotals
