"""Compression, capability, and per-run usage telemetry records."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import Field, NonNegativeInt

from agent_runtime.execution.contracts import JsonObject, RuntimeContract


class RuntimeRunUsageRecord(RuntimeContract):
    """Denormalized per-run token usage row (B1).

    One row per assistant run, written by the worker on RUN_COMPLETED.
    ``id`` mirrors ``run_id`` so the unique constraint underwrites the
    ``ON CONFLICT (run_id) DO NOTHING`` write path. ``cost_micro_usd``,
    ``pricing_id``, and ``pricing_version`` are populated by B3's pricing
    hook; left ``None`` when the catalog has no entry for the model.

    Sub-PRD 01a — token-kind columns mirror
    :class:`agent_runtime.observability.token_usage.NormalizedTokenUsage`.
    ``input_tokens`` is the GROSS input figure (includes cached +
    cache_creation); ``cached_input_tokens`` and
    ``cache_creation_input_tokens`` are subsets billed at their own
    rates. ``reasoning_tokens`` / ``audio_*`` are independent kinds
    summed into ``total_tokens``.
    """

    id: str
    org_id: str
    user_id: str
    conversation_id: str
    run_id: str
    assistant_id: str | None = None
    model_provider: str
    model_name: str
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cached_input_tokens: NonNegativeInt = 0
    cache_creation_input_tokens: NonNegativeInt = 0
    reasoning_tokens: NonNegativeInt = 0
    audio_input_tokens: NonNegativeInt = 0
    audio_output_tokens: NonNegativeInt = 0
    total_tokens: NonNegativeInt = 0
    chunk_count: NonNegativeInt = 0
    first_token_ms: NonNegativeInt | None = None
    duration_ms: NonNegativeInt = 0
    started_at: datetime
    completed_at: datetime
    status: str
    schema_version: int = 1
    retention_until: datetime | None = None
    pii_purged_at: datetime | None = None
    cost_micro_usd: int | None = None
    pricing_id: str | None = None
    pricing_version: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RuntimeModelCallUsageRecord(RuntimeContract):
    """Per-LLM-call token usage row (B2).

    Written once per AIMessage that closes with usage. ``task_id`` and
    ``subagent_id`` are populated when the call ran inside a subagent so
    queries can attribute tokens by feature / agent. ``connector_slug``
    (PR 7.2) carries the connector that prompted this call: the most
    recent completed tool invocation on the same run with
    ``completed_at`` strictly before this call's ``created_at``. ``None``
    for cold-turn calls (planning before any tool fires). Cost columns
    mirror the run-level row and are populated by B3.

    Sub-PRD 01a — token-kind columns mirror
    :class:`agent_runtime.observability.token_usage.NormalizedTokenUsage`.
    See :class:`RuntimeRunUsageRecord` for the field semantics.
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: str
    run_id: str
    conversation_id: str
    parent_event_id: str | None = None
    trace_id: str
    task_id: str | None = None
    subagent_id: str | None = None
    model_provider: str
    model_name: str
    connector_slug: str | None = None
    # Sub-PRD 01b — attribution columns. ``purpose`` defaults to
    # ``'main'`` so pre-migration rows + any code path that doesn't
    # build a ``UsageAttributionContext`` get the safe bucket.
    # ``originating_tool_*`` are only populated for tool_interpretation
    # / tool_planning calls.
    purpose: str = "main"
    originating_tool_call_id: str | None = None
    originating_tool_name: str | None = None
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cached_input_tokens: NonNegativeInt = 0
    cache_creation_input_tokens: NonNegativeInt = 0
    reasoning_tokens: NonNegativeInt = 0
    audio_input_tokens: NonNegativeInt = 0
    audio_output_tokens: NonNegativeInt = 0
    total_tokens: NonNegativeInt = 0
    duration_ms: NonNegativeInt = 0
    schema_version: int = 1
    cost_micro_usd: int | None = None
    pricing_id: str | None = None
    pricing_version: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ModelPricingRecord(RuntimeContract):
    """Versioned price for one (provider, model, region) at a point in time (B3).

    Cost is stored in micro-USD integer (1 USD = 1_000_000 micro_usd) so
    no float drift can creep in on the persistence path. ``pricing_id``
    is snapshotted onto each usage row so retroactive price changes never
    mutate historical cost.
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    provider: str
    model_name: str
    region: str = "global"
    effective_from: datetime
    effective_until: datetime | None = None
    input_per_1m_micro_usd: NonNegativeInt
    output_per_1m_micro_usd: NonNegativeInt
    cached_input_per_1m_micro_usd: NonNegativeInt | None = None
    context_window_tokens: NonNegativeInt | None = None
    pricing_source: str = "yaml-seed"
    pricing_version: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UsageDailyUserRow(RuntimeContract):
    """Daily per-user-per-model rollup row (B4)."""

    org_id: str
    user_id: str
    day: datetime  # date stored as midnight UTC for consistent serialization
    model_provider: str
    model_name: str
    runs_count: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cached_input_tokens: NonNegativeInt
    total_tokens: NonNegativeInt
    cost_micro_usd: int | None = None
    refreshed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UsageDailyOrgRow(RuntimeContract):
    """Daily per-org-per-model rollup row (B4)."""

    org_id: str
    day: datetime
    model_provider: str
    model_name: str
    runs_count: NonNegativeInt
    distinct_users: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cached_input_tokens: NonNegativeInt
    total_tokens: NonNegativeInt
    cost_micro_usd: int | None = None
    refreshed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UsageDailyConnectorRow(RuntimeContract):
    """Daily per-org-per-connector rollup row (PR 7.2).

    ``connector_slug`` is the empty string for the "(unattributed)"
    bucket (LLM calls before any tool fired this turn). The base table
    stores ``NULL``; the rollup loop coalesces to ``''`` so the row is
    representable inside the natural-key PK.
    """

    org_id: str
    day: datetime
    connector_slug: str
    runs_count: NonNegativeInt
    distinct_users: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cached_input_tokens: NonNegativeInt
    total_tokens: NonNegativeInt
    cost_micro_usd: int | None = None
    refreshed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UsageConversationAggregateRecord(RuntimeContract):
    """Per-conversation aggregate returned by top-conversation usage queries."""

    conversation_id: str
    title: str | None = None
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    cached_input_tokens: NonNegativeInt = 0
    total_tokens: NonNegativeInt = 0
    runs_count: NonNegativeInt = 0
    cost_micro_usd: int | None = None


class CompressionEventRecord(RuntimeContract):
    """Redacted context compression telemetry."""

    compression_event_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    org_id: str
    before_tokens: NonNegativeInt
    after_tokens: NonNegativeInt
    strategy: str
    payload_refs: JsonObject = Field(default_factory=dict)
    trace_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CapabilitySnapshotRecord(RuntimeContract):
    """Model-visible capability summary available during a run."""

    snapshot_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str
    org_id: str
    capability_type: str
    capability_name: str
    capability_version: str | None = None
    scopes: JsonObject = Field(default_factory=dict)
    risk_class: str | None = None
    summary: str
    loaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
