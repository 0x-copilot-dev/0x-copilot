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


class UsageConnectorRow(RuntimeContract):
    """One connector in a by-connector breakdown (PR 7.2).

    ``connector_slug`` is the empty string for the "(unattributed)"
    bucket — calls before any tool fired this turn. The frontend
    renders the empty slug as a localised label.

    Sub-PRD 01d: ``model_name`` carries the model split. Empty string
    represents pre-01d rows (no model dimension on the connector
    rollup before the migration).
    """

    connector_slug: str
    model_name: str = ""
    input: NonNegativeInt = 0
    output: NonNegativeInt = 0
    cached_input: NonNegativeInt = 0
    total: NonNegativeInt = 0
    runs_count: NonNegativeInt = 0
    cost_micro_usd: int | None = None


class UsageSubagentRow(RuntimeContract):
    """One row of the org-scoped by-subagent breakdown (Sub-PRD 01d).

    ``subagent_slug`` is the empty string for orchestrator-scope LLM
    calls (mirrors the connector rollup's "(unattributed)" pattern).
    Carries every captured token kind so per-subagent reports are
    total-correct.
    """

    subagent_slug: str
    model_provider: str
    model_name: str
    call_count: NonNegativeInt = 0
    input: NonNegativeInt = 0
    output: NonNegativeInt = 0
    cached_input: NonNegativeInt = 0
    cache_creation_input: NonNegativeInt = 0
    reasoning: NonNegativeInt = 0
    audio_input: NonNegativeInt = 0
    audio_output: NonNegativeInt = 0
    total: NonNegativeInt = 0
    cost_micro_usd: int | None = None


class UsagePurposeRow(RuntimeContract):
    """One row of the org-scoped by-purpose breakdown (Sub-PRD 01d).

    ``purpose`` is one of the ``Purpose`` StrEnum values
    (``main`` / ``tool_planning`` / ``tool_interpretation`` /
    ``subagent_work`` / ``context_compression``).
    """

    purpose: str
    model_provider: str
    model_name: str
    call_count: NonNegativeInt = 0
    input: NonNegativeInt = 0
    output: NonNegativeInt = 0
    cached_input: NonNegativeInt = 0
    cache_creation_input: NonNegativeInt = 0
    reasoning: NonNegativeInt = 0
    audio_input: NonNegativeInt = 0
    audio_output: NonNegativeInt = 0
    total: NonNegativeInt = 0
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
    by_connector: tuple[UsageConnectorRow, ...] = ()
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
    by_connector: tuple[UsageConnectorRow, ...] = ()
    cold_start_fallback: bool = False


class UsageOrgSubagentsResponse(RuntimeContract):
    """Response shape for ``GET /v1/usage/org/subagents`` (Sub-PRD 01d).

    Admin-only — same auth scope as ``/v1/usage/org``. Returns rows
    sorted by ``cost_micro_usd`` descending (or ``total`` tokens when
    cost is unknown).
    """

    period: UsagePeriodWindow
    currency: Literal["USD"] = "USD"
    rows: tuple[UsageSubagentRow, ...] = ()
    cold_start_fallback: bool = False


class UsageOrgPurposeResponse(RuntimeContract):
    """Response shape for ``GET /v1/usage/org/purpose`` (Sub-PRD 01d).

    Admin-only. Returns rows sorted by ``cost_micro_usd`` descending.
    """

    period: UsagePeriodWindow
    currency: Literal["USD"] = "USD"
    rows: tuple[UsagePurposeRow, ...] = ()
    cold_start_fallback: bool = False


class AgentUsageResponse(RuntimeContract):
    """Response shape for ``GET /v1/usage/org/agent/{agent_id}`` (P8-A4).

    Read-only projection over the existing ``runtime_model_call_usage``
    table joined by ``run_id`` to the run records carrying ``agent_id``
    on ``runtime_context.trace_metadata.agent_id``. Per cross-audit
    §5.5, this endpoint must not write to any usage table and must not
    introduce a parallel tracker — every figure here is summed from
    the canonical per-LLM-call rows.

    ``cost_breakdown_by_purpose`` maps the ``Purpose`` StrEnum string
    value (``main`` / ``tool_planning`` / ``tool_interpretation`` /
    ``subagent_work`` / ``context_compression``) to the summed
    micro-USD cost for that bucket. Models without a priced row in
    ``model_pricing`` contribute ``0`` (their cost is ``NULL`` in
    the per-call rows).
    """

    agent_id: str
    period: UsagePeriodWindow
    currency: Literal["USD"] = "USD"
    run_count: NonNegativeInt = 0
    token_in: NonNegativeInt = 0
    token_out: NonNegativeInt = 0
    cost_usd_micro: int = 0
    cost_breakdown_by_purpose: dict[str, int] = Field(default_factory=dict)


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
    """One LLM call inside ``RunUsageBreakdown.by_call``.

    PRD-E3 (FR-G) adds the ``purpose`` + ``surface_id`` attribution axes so the
    future Settings → Usage screen reads them with zero backfill. ``purpose`` is
    A2's ``Purpose`` StrEnum value (``main`` / ``subagent_work`` /
    ``view_shaping`` / ``shape_request`` / …) — the usage-row query dimension,
    deliberately distinct from the closed 4-value ``LedgerPurpose`` on the
    ``usage.recorded`` event; it is NOT normalized ``main``→``run`` here.
    ``surface_id`` ties a shaping call to a derived surface when known
    (``view_shaping`` records ``None``; B4 ``shape_request`` carries a concrete id).
    """

    id: str
    parent_event_id: str | None = None
    task_id: str | None = None
    subagent_id: str | None = None
    model_provider: str
    model_name: str
    purpose: str = "main"
    surface_id: str | None = None
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
    by_connector: tuple[UsageConnectorRow, ...] = ()


class UsageRunRow(RuntimeContract):
    """One run inside a per-conversation total."""

    run_id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: str
    total: UsageTotals
