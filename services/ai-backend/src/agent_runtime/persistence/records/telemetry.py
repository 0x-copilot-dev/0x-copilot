"""Compression, capability, per-run usage, and context-occupancy telemetry records.

Three observation families share this module because they answer three
different questions about the same model call. ``runtime_run_usage`` /
``runtime_model_call_usage`` answer *what did it cost*.
``runtime_context_occupancy`` answers *what was in the window and who put
it there* — a decomposition of the input scalar, never a second billing
source (Context Occupancy Ledger design §6.1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

from agent_runtime.execution.contracts import JsonObject, JsonValue, RuntimeContract


class RuntimeRunUsageRecord(RuntimeContract):
    """Denormalized per-run token usage row.

    One row per assistant run, written by the worker on RUN_COMPLETED.
    ``id`` mirrors ``run_id`` so the unique constraint underwrites the
    ``ON CONFLICT (run_id) DO NOTHING`` write path. ``cost_micro_usd``,
    ``pricing_id``, and ``pricing_version`` are populated by the pricing
    hook; left ``None`` when the catalog has no entry for the model.

    Token-kind columns mirror
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
    """Per-LLM-call token usage row.

    Written once per AIMessage that closes with usage. ``task_id`` and
    ``subagent_id`` are populated when the call ran inside a subagent so
    queries can attribute tokens by feature / agent. ``connector_slug``
    carries the connector that prompted this call: the most recent completed
    tool invocation on the same run with ``completed_at`` strictly before
    this call's ``created_at``. ``None`` for cold-turn calls (planning before
    any tool fires). Cost columns mirror the run-level row and are populated
    by the pricing hook.

    Token-kind columns mirror
    :class:`agent_runtime.observability.token_usage.NormalizedTokenUsage`.
    See :class:`RuntimeRunUsageRecord` for field semantics.
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
    # Generative Surfaces v2 (PRD-A2, FR-G) attribution columns. Nullable —
    # pre-migration rows exist and ``schema_version`` stays 1 (additive). Not
    # flag-gated: the future usage UI must need no backfill (FR-G4). ``user_id``
    # attributes the call to a user (per-user rollups, E3); ``surface_id`` ties a
    # shaping call to a derived surface when known (``view_shaping`` records
    # ``None``; B4 ``shape_request`` carries a concrete id).
    user_id: str | None = None
    surface_id: str | None = None
    # Attribution columns. ``purpose`` defaults to ``'main'`` so
    # pre-migration rows and any code path that doesn't build a
    # ``UsageAttributionContext`` get the safe bucket.
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


class UsageAttributionRelationship(StrEnum):
    """The immutable way one metered call relates to an operation output."""

    PRODUCED = "produced"
    REVISED = "revised"
    PROPOSED = "proposed"
    SHAPED = "shaped"


class UsageAttributionEdge(RuntimeContract):
    """An immutable attribution link kept separate from a historical usage row.

    The persistence scope (organization) intentionally lives in the store API,
    not on this portable edge contract.  That makes the edge safe to project to
    clients while keeping tenancy derived from the authenticated persistence
    operation.  An edge is append-only: it never alters token or cost columns
    on :class:`RuntimeModelCallUsageRecord`.
    """

    edge_id: str = Field(
        default_factory=lambda: uuid4().hex, min_length=1, max_length=128
    )
    usage_record_id: str = Field(min_length=1, max_length=128)
    operation_id: str = Field(min_length=1, max_length=128)
    artifact_id: str | None = Field(default=None, min_length=1, max_length=128)
    stage_id: str | None = Field(default=None, min_length=1, max_length=128)
    surface_id: str | None = Field(default=None, min_length=1, max_length=512)
    relationship: UsageAttributionRelationship
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _relationship_target_is_present(self) -> "UsageAttributionEdge":
        if self.relationship is UsageAttributionRelationship.PROPOSED:
            if self.stage_id is None:
                raise ValueError("proposed usage attribution requires stage_id")
            return self
        if self.relationship is UsageAttributionRelationship.SHAPED:
            if self.artifact_id is None and self.surface_id is None:
                raise ValueError(
                    "shaped usage attribution requires artifact_id or surface_id"
                )
            return self
        if self.artifact_id is None:
            raise ValueError(
                f"{self.relationship.value} usage attribution requires artifact_id"
            )
        return self

    @property
    def idempotency_key(
        self,
    ) -> tuple[
        str,
        str,
        str | None,
        str | None,
        str | None,
        str,
    ]:
        """Stable natural identity for retry-safe append-only persistence."""

        return (
            self.usage_record_id,
            self.operation_id,
            self.artifact_id,
            self.stage_id,
            self.surface_id,
            self.relationship.value,
        )


class ModelPricingRecord(RuntimeContract):
    """Versioned price for one (provider, model, region) at a point in time.

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
    """Daily per-user-per-model rollup row."""

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
    """Daily per-org-per-model rollup row."""

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
    """Daily per-org-per-connector rollup row.

    ``connector_slug`` is the empty string for the "(unattributed)"
    bucket (LLM calls before any tool fired this turn). The base table
    stores ``NULL``; the rollup loop coalesces to ``''`` so the row is
    representable inside the natural-key PK.

    ``model_name`` extends the PK so a single connector can split costs
    across multiple models. Empty string represents pre-migration rows
    that didn't carry a model dimension.
    """

    org_id: str
    day: datetime
    connector_slug: str
    model_name: str = ""
    runs_count: NonNegativeInt
    distinct_users: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cached_input_tokens: NonNegativeInt
    total_tokens: NonNegativeInt
    cost_micro_usd: int | None = None
    refreshed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UsageDailySubagentRow(RuntimeContract):
    """Daily per-org-per-subagent rollup row.

    Org-scoped (no user_id) — matches the connector rollup pattern.
    ``subagent_slug`` is the empty string for orchestrator-scope LLM
    calls (mirrors the connector rollup's "(unattributed)" bucket).

    Carries all seven token kinds so per-subagent reports are total-correct
    even for reasoning / cached / audio workloads.
    """

    org_id: str
    day: datetime
    subagent_slug: str
    model_provider: str
    model_name: str
    call_count: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cached_input_tokens: NonNegativeInt
    cache_creation_input_tokens: NonNegativeInt = 0
    reasoning_tokens: NonNegativeInt = 0
    audio_input_tokens: NonNegativeInt = 0
    audio_output_tokens: NonNegativeInt = 0
    total_tokens: NonNegativeInt
    cost_micro_usd: int | None = None
    refreshed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UsageDailyPurposeRow(RuntimeContract):
    """Daily per-org-per-purpose rollup row.

    ``purpose`` is the ``Purpose`` enum value (``main`` /
    ``tool_planning`` / ``tool_interpretation`` / ``subagent_work`` /
    ``context_compression``). Lets ops answer "what share of org
    spend is context compression" without scanning raw rows.
    """

    org_id: str
    day: datetime
    purpose: str
    model_provider: str
    model_name: str
    call_count: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    cached_input_tokens: NonNegativeInt
    cache_creation_input_tokens: NonNegativeInt = 0
    reasoning_tokens: NonNegativeInt = 0
    audio_input_tokens: NonNegativeInt = 0
    audio_output_tokens: NonNegativeInt = 0
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


# Bounded identifier shape shared by every column of ``runtime_context_occupancy``
# that carries a runtime-minted id. The width matches
# ``ContextOccupancySnapshot`` exactly, so no snapshot the observability lane
# can build is unrepresentable here — a narrower persistence bound would turn a
# legal measurement into a silently dropped row.
#
# The widths live on ``RuntimeContextOccupancyRecord.Limits`` rather than as
# literals here because the public read/stream contract in
# ``runtime_api.schemas.context_occupancy`` has to bound the same three fields,
# and a restated literal there is exactly how the label bound drifted once
# already. One constant, two readers.
_OCCUPANCY_IDENTIFIER_CHARS = 200
_OCCUPANCY_PROVIDER_CHARS = 120
_OccupancyIdentifier = Annotated[
    str, Field(min_length=1, max_length=_OCCUPANCY_IDENTIFIER_CHARS)
]


class RuntimeContextGraphScope(StrEnum):
    """Which model context window one occupancy row was measured inside.

    A subagent runs its own graph against its **own** window, so occupancy
    rows from different scopes describe different denominators and must never
    be summed (design §6.2). Closed for the same reason ``ContextSegmentClass``
    is: a provider request is issued by exactly one graph, and there is no
    third kind of graph.

    This is the **durable projection** of
    :class:`agent_runtime.observability.context_occupancy.GraphScope`, declared
    here rather than imported because ``persistence.records`` is a leaf layer —
    every sibling record module depends on nothing but
    ``execution.contracts``, and importing the observability lane (which
    reaches into prompts, budgets, and the token counter) to reuse a two-member
    enum would invert that and put a real import cycle one edit away.

    The duplication is therefore deliberate, and it is *gated* rather than
    trusted: ``tests/unit/agent_runtime/persistence/test_context_occupancy_record.py``
    asserts the two value sets are identical, so a third scope added upstream
    fails CI here — where the CHECK constraint on ``runtime_context_occupancy``
    also has to be widened before any such row could be stored.
    """

    ROOT = "root"
    SUBAGENT = "subagent"


class RuntimeContextOccupancyRecord(RuntimeContract):
    """One measured occupancy snapshot for one model-call attempt.

    The third record in the observation family that already holds
    ``PromptAssembledRecord`` and ``PromptCacheObservedRecord``, and it links
    back to the first exactly the way ``PromptCacheObservationInput`` does —
    by ``assembly_record_id``. Nullable there and here: occupancy is measured
    on the materialized provider request, which exists even when typed prompt
    assembly did not run for that call.

    **Identity is ``(model_call_id, attempt_ordinal)``, not ``id``.** A retried
    call is a second materialized request against a second window state, so it
    earns a second row rather than overwriting the first (design §6.3);
    rollups deduplicate on ``model_call_id`` and take the last attempt.
    ``id`` is transport-level only, which is why the durable uniqueness lives
    on :attr:`idempotency_key` and on the table's UNIQUE index.

    **The row is immutable once written.** The application role holds
    ``SELECT, INSERT`` and nothing else; a re-append of the same attempt is a
    no-op rather than a rewrite. An occupancy measurement is a fact about a
    request that has already been sent — there is no later information that
    could legitimately correct it.

    Three residuals are deliberately kept apart rather than collapsed into one
    "unknown" (design §4.4):

    - ``undeclared_tokens`` — measured text matching no declared origin.
      Expected **0**; anything above it is a first-party contract defect.
    - ``unattributed_delta`` — ``provider_input_tokens - estimated_input_tokens``,
      **signed**. Negative means our tokenizer over-counted. This is provider
      wire overhead and tokenizer drift: expected, bounded, not a bug.
    - ``free_tokens`` — derived, and ``None`` whenever the model is absent from
      the pricing catalog. A fabricated default window would be a worse answer
      than no answer.

    ``segments_json`` is written whole and read whole (design §5). Persistence
    deliberately does not own the segment vocabulary — that belongs to the
    observability lane that composes the declarations — but it does enforce the
    invariant it is responsible for: this table is exposed over an HTTP read
    API, so §6.5's "counts and bounded identifiers only, never content" is
    checked structurally here, at the durability boundary, where a leak would
    otherwise become permanent.
    """

    class Keys:
        """Canonical keys inside the ``segments_json`` envelope.

        ``DETAIL`` is named here rather than matched inline because it is the
        one segment field §6.5 singles out as the content vector, and the
        validator below has to reach it by name to bound it separately from a
        label (see :class:`Limits`).
        """

        SEGMENTS = "segments"
        DETAIL = "detail"

    class Limits:
        """Bounds that keep one row small and provably content-free.

        ``MAX_SEGMENTS`` caps per-tool granularity (~25–40 segments on a
        realistic call) with headroom, so a runaway tool surface fails the
        contract instead of writing an unbounded JSONB document.

        The two string bounds are deliberately different widths, and collapsing
        them was a live §6.5 hole. ``MAX_SEGMENT_TEXT_CHARS`` is the structural
        backstop applied to *every* string in the envelope, so it has to admit
        the widest legal one — a rendered ``owner:name`` label, which
        ``context_origin.MAX_LABEL_LENGTH`` allows to reach 401 characters.
        A single uniform bound is therefore necessarily as loose as a label, and
        ``detail`` — the field fed by an *untrusted* source (an MCP-registry tool
        name) and the one §6.5 names — inherited that looseness: 512 characters
        of arbitrary text could be written to a column that is served over an
        HTTP read API. ``MAX_SEGMENT_DETAIL_CHARS`` closes that by bounding
        ``detail`` at the width its only producer can actually emit.

        ``MAX_SEGMENT_DETAIL_CHARS`` **mirrors**
        ``agent_runtime.observability.context_occupancy.ContextSegment.MAX_DETAIL_LENGTH``
        and is restated rather than imported for the same reason
        :class:`RuntimeContextGraphScope` is: ``persistence.records`` is a leaf
        that depends on nothing but ``execution.contracts``, and the
        observability lane reaches into prompts, budgets, and the token counter.
        The copy is *gated*, not trusted — the record's unit test asserts the two
        values are identical, so widening one without the other fails CI.
        """

        MAX_SEGMENTS = 512
        MAX_SEGMENT_TEXT_CHARS = 512
        MAX_SEGMENT_DETAIL_CHARS = 200
        MAX_SEGMENT_DEPTH = 4
        #: Widths of the row's own identifier columns, published here so the
        #: read/stream contract can bound the same fields without a second
        #: literal. See ``_OccupancyIdentifier`` above.
        MAX_IDENTIFIER_CHARS = _OCCUPANCY_IDENTIFIER_CHARS
        MAX_PROVIDER_CHARS = _OCCUPANCY_PROVIDER_CHARS

    id: str = Field(default_factory=lambda: uuid4().hex)
    org_id: _OccupancyIdentifier
    run_id: _OccupancyIdentifier
    conversation_id: _OccupancyIdentifier
    model_call_id: _OccupancyIdentifier
    attempt_ordinal: PositiveInt = 1
    assembly_record_id: str | None = Field(
        default=None, max_length=_OCCUPANCY_IDENTIFIER_CHARS
    )
    graph_scope: RuntimeContextGraphScope = RuntimeContextGraphScope.ROOT
    provider: Annotated[str, Field(min_length=1, max_length=_OCCUPANCY_PROVIDER_CHARS)]
    model_family: _OccupancyIdentifier
    context_window_tokens: NonNegativeInt | None = None
    estimated_input_tokens: NonNegativeInt = 0
    provider_input_tokens: NonNegativeInt | None = None
    cached_input_tokens: NonNegativeInt = 0
    cache_creation_input_tokens: NonNegativeInt = 0
    undeclared_tokens: NonNegativeInt = 0
    # Signed on purpose: over-counting is as informative as under-counting,
    # and clamping it at zero would hide the tokenizer drift it exists to show.
    unattributed_delta: int = 0
    # ``validate_default`` so the canonical envelope is produced even on the
    # fail-open path that constructs a snapshot with nothing measured — a row
    # whose shape depends on whether a caller passed the field would push that
    # branch onto every reader.
    segments_json: JsonObject = Field(default_factory=dict, validate_default=True)
    schema_version: Literal[1] = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        """Fold provider casing so ``OpenAI`` and ``openai`` group as one."""

        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("provider must be non-empty")
        return normalized

    @field_validator("assembly_record_id")
    @classmethod
    def _absent_assembly_link_is_null(cls, value: str | None) -> str | None:
        """Store "no linked assembly record" as NULL, never as an empty string.

        The upstream snapshot permits an empty string, and an empty foreign id
        means the same thing as no id. Normalizing here keeps one representable
        form in the column so a reader never has to test for both.
        """

        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("model_family")
    @classmethod
    def _normalize_model_family(cls, value: str) -> str:
        """Keep model-family casing but reject whitespace-only families."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("model_family must be non-empty")
        return normalized

    @field_validator("created_at")
    @classmethod
    def _aware_created_at(cls, value: datetime) -> datetime:
        """Refuse naive timestamps: this row is ordered across tenants."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value

    @field_validator("segments_json")
    @classmethod
    def _segments_envelope_is_bounded(cls, value: JsonObject) -> JsonObject:
        """Normalize the envelope and enforce the no-content-leakage bound.

        The envelope carries exactly one key so a reader never has to guess
        whether an unknown key is data or drift. A missing key normalizes to an
        empty list, which is the honest shape for a fail-open partial snapshot
        (design §6.4) — no segments measured is different from no row.
        """

        unknown = sorted(set(value) - {cls.Keys.SEGMENTS})
        if unknown:
            raise ValueError(
                f"context occupancy segments envelope has unknown keys: {unknown}"
            )
        normalized: JsonObject = dict(value)
        segments = normalized.setdefault(cls.Keys.SEGMENTS, [])
        if not isinstance(segments, list):
            raise ValueError("context occupancy segments must be a JSON array")
        if len(segments) > cls.Limits.MAX_SEGMENTS:
            raise ValueError(
                f"context occupancy segment count exceeds {cls.Limits.MAX_SEGMENTS}"
            )
        for segment in segments:
            if not isinstance(segment, dict):
                raise ValueError("each context occupancy segment must be an object")
            cls._assert_bounded_json(segment, depth=0)
            cls._assert_detail_is_an_identifier(segment)
        return normalized

    @classmethod
    def _assert_detail_is_an_identifier(cls, segment: Mapping[str, object]) -> None:
        """Bound the one segment field an untrusted source feeds (§6.5).

        The structural sweep above has to admit the widest legal string in the
        envelope, which is a 401-character ``owner:name`` label — so on its own it
        lets ``detail`` carry 512 characters. That matters more for ``detail``
        than for any sibling: a label is minted from our own declarations, while
        ``detail`` carries an MCP-registry tool name, and MCP descriptors are
        untrusted input. This is the narrow, named check that keeps the column an
        identifier column, and it is deliberately the *only* place persistence
        looks at a segment by field name — because ``detail`` is the field §6.5
        names, not because persistence has started owning the vocabulary.

        Absent, ``None``, and non-``str`` all pass: the shape belongs to the
        observability lane, and this method's job is the bound, not the type.
        """

        detail = segment.get(cls.Keys.DETAIL)
        if (
            isinstance(detail, str)
            and len(detail) > cls.Limits.MAX_SEGMENT_DETAIL_CHARS
        ):
            raise ValueError(
                "context occupancy segment detail is an identifier; a value "
                f"longer than {cls.Limits.MAX_SEGMENT_DETAIL_CHARS} characters "
                "is content"
            )

    @classmethod
    def _assert_bounded_json(cls, value: object, *, depth: int) -> None:
        """Walk one segment and refuse anything that could carry content.

        Structural rather than vocabulary-aware on purpose: the segment shape
        is owned by the observability lane, but "identifiers only" is a
        persistence-boundary invariant that must hold whatever that shape
        becomes.

        Two structural rules, and the second is the sharp one. The length bound
        is blunt — it has to admit a 401-character label, so it admits 512
        characters of anything. The control-character rule is what a length bound
        cannot express: **every** legitimate segment string is a single printable
        token (a closed-vocabulary enum value, an ``owner:name`` label whose
        pattern forbids whitespace outright, a sanitized tool name, ``msg[12]``),
        while a smuggled message body or tool result is almost always multi-line.
        ``ContextSegment`` already fails closed on exactly this for ``detail`` at
        the measurement boundary; restating it here is what makes the record's
        claim to enforce §6.5 *structurally* true for the whole envelope rather
        than for one field of it — and a leak that reaches a column is permanent.
        """

        if depth > cls.Limits.MAX_SEGMENT_DEPTH:
            raise ValueError("context occupancy segment nesting exceeds the bound")
        if isinstance(value, str):
            if len(value) > cls.Limits.MAX_SEGMENT_TEXT_CHARS:
                raise ValueError(
                    "context occupancy segments carry identifiers only; a value "
                    f"longer than {cls.Limits.MAX_SEGMENT_TEXT_CHARS} characters "
                    "is content"
                )
            if any(character < " " or character == "\x7f" for character in value):
                raise ValueError(
                    "context occupancy segments carry identifiers only; a value "
                    "containing control characters is content"
                )
            return
        if isinstance(value, (bytes, bytearray)):
            raise ValueError("context occupancy segments must not carry bytes")
        if isinstance(value, Mapping):
            for key, item in value.items():
                cls._assert_bounded_json(key, depth=depth + 1)
                cls._assert_bounded_json(item, depth=depth + 1)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                cls._assert_bounded_json(item, depth=depth + 1)

    @model_validator(mode="after")
    def _residuals_reconcile(self) -> "RuntimeContextOccupancyRecord":
        """Refuse a row whose three residuals contradict each other.

        The two-residual split of design §4.4 only means something if the
        arithmetic behind it is real, so the definitions are enforced rather
        than documented: a row that cannot be reconciled is a measurement bug
        and is better surfaced at the write than persisted as a plausible lie.
        The write path is already fail-open (§6.4), so a raise here degrades to
        a dropped snapshot, never to a failed run.

        The unreported branch below checks the cache subsets *before* it returns,
        which is the non-obvious half. It would be easy to read "no provider
        total" as "nothing to compare the subsets against, so skip them", but the
        subsets are defined as subsets **of** that total: with no total, the only
        self-consistent pair is ``(0, 0)``, and a non-zero one is more
        contradictory than an oversized one, not less. Every production path
        already satisfies it — ``provider_input_tokens`` is ``None`` exactly when
        the provider reported no usage block at all, so there is no cache
        metadata to copy — which is precisely why the check belongs here rather
        than being left to the one call site that happens to honour it.
        """

        if self.undeclared_tokens > self.estimated_input_tokens:
            raise ValueError("undeclared tokens exceed the measured estimate")
        if self.provider_input_tokens is None:
            if self.unattributed_delta != 0:
                raise ValueError(
                    "unattributed delta requires a provider input total to "
                    "reconcile against"
                )
            if self.cached_input_tokens or self.cache_creation_input_tokens:
                raise ValueError(
                    "cache token subsets require a provider input total to be "
                    "subsets of"
                )
            return self
        if (
            self.cached_input_tokens + self.cache_creation_input_tokens
            > self.provider_input_tokens
        ):
            raise ValueError("cache token subsets exceed provider input tokens")
        expected_delta = self.provider_input_tokens - self.estimated_input_tokens
        if self.unattributed_delta != expected_delta:
            raise ValueError(
                "unattributed delta must equal provider_input_tokens minus "
                "estimated_input_tokens"
            )
        return self

    @property
    def idempotency_key(self) -> tuple[str, int]:
        """Durable identity of one measured attempt.

        Mirrors :attr:`UsageAttributionEdge.idempotency_key`: the natural key
        every adapter dedupes on, so an at-least-once writer cannot produce a
        second row for a request that was measured once.
        """

        return (self.model_call_id, self.attempt_ordinal)

    @property
    def segments(self) -> tuple[JsonObject, ...]:
        """The measured segments as written, in their recorded order."""

        segments = self.segments_json.get(self.Keys.SEGMENTS, [])
        if not isinstance(segments, list):
            return ()
        return tuple(segment for segment in segments if isinstance(segment, dict))  # type: ignore[misc]

    @property
    def segment_count(self) -> int:
        """How many segments this snapshot decomposed the window into."""

        return len(self.segments)

    @property
    def free_tokens(self) -> int | None:
        """Unoccupied window for this scope, or ``None`` when the window is unknown.

        Derived rather than stored, and derived by exactly the rule
        ``SnapshotBuilder._free_tokens`` uses, so the read API cannot report a
        different number than the snapshot that produced the row: the best
        available occupancy is the provider's total when it reported one and
        our estimate otherwise. ``None`` — never zero — when the model is
        absent from the pricing catalog; zero would assert a full window where
        we simply do not know the denominator.

        Signed: negative means the request exceeded the window we believe the
        model has, which is a stale pricing row or a genuinely over-stuffed
        request, and worth seeing.

        Computed strictly **within one ``graph_scope``** (design §6.2): a
        parent's free space is unaffected by what a subagent put in its own
        window.
        """

        if self.context_window_tokens is None:
            return None
        occupied = (
            self.estimated_input_tokens
            if self.provider_input_tokens is None
            else self.provider_input_tokens
        )
        return self.context_window_tokens - occupied

    @classmethod
    def from_measurement(
        cls,
        *,
        org_id: str,
        run_id: str,
        conversation_id: str,
        model_call_id: str,
        provider: str,
        model_family: str,
        graph_scope: RuntimeContextGraphScope = RuntimeContextGraphScope.ROOT,
        attempt_ordinal: int = 1,
        assembly_record_id: str | None = None,
        context_window_tokens: int | None = None,
        estimated_input_tokens: int = 0,
        provider_input_tokens: int | None = None,
        cached_input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        undeclared_tokens: int = 0,
        segments: Sequence[Mapping[str, JsonValue]] = (),
        record_id: str | None = None,
        created_at: datetime | None = None,
    ) -> "RuntimeContextOccupancyRecord":
        """Build a row with ``unattributed_delta`` derived, never guessed.

        The single supported construction path for the capture seam. Callers
        supply the two counts they actually measured; the residual between them
        is computed here so no call site can invent, clamp, or transpose it.

        The keyword set maps field-for-field onto
        :class:`~agent_runtime.observability.context_occupancy.ContextOccupancySnapshot`,
        so wiring is a projection rather than a translation. Pass
        ``provider_input_tokens=None`` — not a defaulted ``0`` — when the
        provider reported no usage; a zero is treated as a real reported total
        and yields a large negative delta on every call.
        """

        delta = (
            0
            if provider_input_tokens is None
            else provider_input_tokens - estimated_input_tokens
        )
        values: dict[str, object] = {
            "org_id": org_id,
            "run_id": run_id,
            "conversation_id": conversation_id,
            "model_call_id": model_call_id,
            "attempt_ordinal": attempt_ordinal,
            "assembly_record_id": assembly_record_id,
            "graph_scope": graph_scope,
            "provider": provider,
            "model_family": model_family,
            "context_window_tokens": context_window_tokens,
            "estimated_input_tokens": estimated_input_tokens,
            "provider_input_tokens": provider_input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
            "undeclared_tokens": undeclared_tokens,
            "unattributed_delta": delta,
            "segments_json": {cls.Keys.SEGMENTS: [dict(s) for s in segments]},
        }
        if record_id is not None:
            values["id"] = record_id
        if created_at is not None:
            values["created_at"] = created_at
        return cls.model_validate(values)
