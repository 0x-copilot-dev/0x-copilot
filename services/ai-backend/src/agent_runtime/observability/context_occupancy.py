"""Records for the Context Occupancy Ledger (PRD-04, solution design §4.5).

One :class:`ContextOccupancySnapshot` per model call answers the question usage
tracking cannot: *what is in the model's context window right now, and who put
it there.* Usage is a scalar (``input_tokens``) attributed to a ``Purpose``;
occupancy is that scalar decomposed into the segments that produced it, so an
engineer can see that a tool nobody calls is charging rent on every request.

This module is contracts and arithmetic only. It does not measure anything, does
not touch the model call, and does not persist — PRD-05 wires it into
``ModelInvocationMiddleware``, PRD-08 persists it. Keeping it inert is what lets
the reconciliation rules below be unit-tested against exact numbers instead of
against a live graph.

**The one rule that shapes every derived field: do not fabricate.** Two counts
exist and they will disagree — our per-segment estimate and the provider's
authoritative ``input_tokens``. §3.3 forbids scaling segments to match the
provider total, because across OpenAI / Anthropic / Gemini / OpenRouter / Ollama
that manufactures precision we do not have. Instead the disagreement is promoted
to two first-class, oppositely-meaningful fields (§4.4):

``undeclared_tokens``
    Measured bytes matching no declaration. Expected **0**. Non-zero is a
    contract bug — a contributor put text in front of the model without
    declaring it, and it is actionable as a defect.

``unattributed_delta``
    Provider total minus our measured total. Expected small and **signed**.
    Provider wire overhead and tokenizer drift live here. Negative means we
    over-counted (see the message-envelope bias documented on
    :mod:`agent_runtime.observability.context_token_counter`).

Collapsing those two into a single "unknown" is exactly the failure mode this
design exists to avoid, which is why the builder computes them separately and
never lets one absorb the other.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, ClassVar, Final, Literal

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    ValidationError,
    field_validator,
)

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.observability.context_origin import (
    MAX_LABEL_LENGTH as MAX_CONTEXT_LABEL_LENGTH,
)
from agent_runtime.observability.context_origin import (
    UNDECLARED_CONTEXT_LABEL,
    ContextLifecycle,
    ContextOrigin,
    ContextSegmentClass,
    ContextTextWidth,
)
from agent_runtime.observability.context_token_counter import (
    ContextTokenCounter,
    TokenCounterSource,
)
from agent_runtime.prompts.assembly import PromptCacheEligibility


class GraphScope(StrEnum):
    """Which context window a snapshot describes (§6.2).

    A subagent has its **own** window. Summing child occupancy into the parent
    is not a rounding error, it is a category error that would report >100%
    utilization on any run that delegates. Every snapshot therefore names its
    scope, ``free_tokens`` is computed within one scope only, and run-level
    rollups report ``max`` utilization per scope rather than a cross-scope sum.
    """

    ROOT = "root"
    SUBAGENT = "subagent"


class ContextOccupancyError(RuntimeError):
    """Typed, content-free failure raised when a snapshot cannot be assembled.

    Occupancy is measured on the model-call path, so the caller's fail-open guard
    (§6.4) needs exactly one exception type to catch. Pydantic's
    :class:`ValidationError` message embeds the offending *input values*, which
    for this ledger can be segment detail — so the wrapper reports failing field
    paths and nothing else. Never let raw validation text reach a log line or an
    HTTP response here; §6.5 makes occupancy externally readable.
    """

    def __init__(self, *, field_paths: tuple[str, ...]) -> None:
        self.field_paths = field_paths
        joined = ", ".join(field_paths) or "unknown"
        super().__init__(f"context occupancy snapshot is invalid: {joined}")


class ContextSegment(RuntimeContract):
    """One attributable slice of the materialized provider request.

    A segment is the join of a *declaration* (who owns this text, what class of
    the request does it land in, how does it behave over a conversation) and a
    *measurement* (how many bytes, how many tokens, counted by which tier). The
    declaration half is what makes the ledger actionable: ``lifecycle`` says
    whether a number is rent (``RESIDENT``), a per-turn cost, or a multiplier on
    tool-call count (``PER_RESULT``) — and those demand different fixes.

    ``label`` is the owner-namespaced ``"owner:name"`` string from the
    declaration, or ``UNDECLARED_CONTEXT_LABEL`` when measurement found bytes
    that no declaration covers. Labels are deliberately **not** an enum: a
    central list of every contributor would be stale the moment someone adds a
    tool, and would put the burden on the team least able to know what a new
    contributor is for (§3.2).

    ``detail`` is the sub-identity within a label — a tool name, a
    ``fragment_id``, a message ordinal range. It is bounded and validated
    because occupancy is exposed over an HTTP read API (§7): segments carry
    counts and safe identifiers, never content (§6.5).

    Construct through :meth:`measure` (declared bytes) or
    :meth:`measure_undeclared` (bytes no declaration covered) rather than by
    hand. Those are the only two ways bytes can arrive, and routing every
    caller through them is what stops a measurement site from hand-assembling
    an ``owner:name`` string that drifts from the declaration it claims to
    mirror.
    """

    MAX_DETAIL_LENGTH: ClassVar[int] = 200
    # Derived from ContextOrigin's own bounds, never restated. A narrower bound
    # here was a live §6.4 violation: ``measure`` passes ``origin.label``
    # straight through, so any declaration longer than the guessed literal
    # raised ValidationError on the model-call path — an observability contract
    # taking down a run, which is the one thing this design forbids outright.
    MAX_LABEL_LENGTH: ClassVar[int] = MAX_CONTEXT_LABEL_LENGTH

    segment_class: ContextSegmentClass
    label: Annotated[str, Field(min_length=1, max_length=MAX_LABEL_LENGTH)]
    lifecycle: ContextLifecycle
    third_party: bool = False
    detail: str | None = Field(default=None, max_length=MAX_DETAIL_LENGTH)
    byte_count: NonNegativeInt
    estimated_tokens: NonNegativeInt
    item_count: NonNegativeInt = 1
    cache_eligibility: PromptCacheEligibility | None = None
    counter_source: TokenCounterSource

    @field_validator("detail")
    @classmethod
    def _reject_content_shaped_detail(cls, value: str | None) -> str | None:
        """Fail closed on any ``detail`` that looks like content rather than an identifier.

        Two checks, both cheap, both aimed at §6.5. The length bound is the
        blunt one: message text and tool results are long, identifiers are not.
        The control-character check is the sharp one: every legitimate detail
        this ledger produces (tool name, ``fragment_id``, ``messages[12..37]``)
        is a single printable line, while pasted content is almost always
        multi-line. A contributor that trips either is leaking, and the snapshot
        is discarded by the caller's fail-open guard rather than published.
        """

        if value is None:
            return None
        if len(value) > cls.MAX_DETAIL_LENGTH:
            msg = f"detail must be at most {cls.MAX_DETAIL_LENGTH} characters"
            raise ValueError(msg)
        if any(character < " " or character == "\x7f" for character in value):
            msg = "detail must not contain control characters"
            raise ValueError(msg)
        return value

    @property
    def is_undeclared(self) -> bool:
        """Whether these bytes matched no declaration (§4.4 — expected never)."""

        return self.label == UNDECLARED_CONTEXT_LABEL

    @property
    def sort_key(self) -> tuple[str, str, str]:
        """Total, deterministic ordering key: class, then label, then detail.

        Ordering is canonicalized rather than left to whatever order the
        measurement pass happened to walk the request in. Two snapshots of the
        same context must diff cleanly — golden-fixture tests (§9) and the
        prompt-cache-style digest comparisons both depend on it.
        """

        return (
            str(self.segment_class.value),
            self.label,
            "" if self.detail is None else self.detail,
        )

    @classmethod
    def measure(
        cls,
        text: str,
        *,
        counter: ContextTokenCounter,
        model: str,
        origin: ContextOrigin,
        detail: str | None = None,
        item_count: NonNegativeInt = 1,
        digest: str | None = None,
    ) -> ContextSegment:
        """Measure ``text`` against the declaration that owns it (§4.1 → §4.5).

        Every attribute a reader uses to interpret the number — ``label``,
        ``segment_class``, ``lifecycle``, ``third_party``, ``cache_eligibility``
        — is taken from ``origin`` rather than re-supplied here. That is the
        whole point of the declaration seam: the contributor states what its
        text is once, at the point of composition, and the measurement site is
        not allowed a second opinion.

        Pass ``digest`` whenever the system already holds one for exactly these
        bytes (``content_digest`` for a system fragment, ``tool_schema_revision``
        for the tool block) to take the §3.4 memoized path; omit it for
        genuinely new text such as a fresh message.
        """

        return cls._measured(
            text,
            counter=counter,
            model=model,
            segment_class=origin.segment_class,
            label=origin.label,
            lifecycle=origin.lifecycle,
            third_party=origin.third_party,
            cache_eligibility=origin.cache_eligibility,
            detail=detail,
            item_count=item_count,
            digest=digest,
        )

    @classmethod
    def measure_undeclared(
        cls,
        text: str,
        *,
        counter: ContextTokenCounter,
        model: str,
        segment_class: ContextSegmentClass,
        lifecycle: ContextLifecycle,
        detail: str | None = None,
        item_count: NonNegativeInt = 1,
    ) -> ContextSegment:
        """Measure bytes that no declaration covered, as ``UNDECLARED`` (§4.4).

        A separate, explicitly-named constructor rather than a ``label``
        parameter on :meth:`measure`, because recording undeclared occupancy is
        a decision and should read like one at the call site. The runtime takes
        it instead of raising — an undeclared contributor is a contract bug, but
        a measurement concern must never take a run down (§6.4). PRD-02's AST
        gate is what fails, in CI, where failing is free.

        ``segment_class`` and ``lifecycle`` still come from the caller because
        they are *structural*: the measurement site knows whether the bytes were
        in the system block, the tool block, or the message list even when it
        has no idea who wrote them. Deliberately unmemoized — undeclared bytes
        are by definition not a surface we track a digest for.
        """

        return cls._measured(
            text,
            counter=counter,
            model=model,
            segment_class=segment_class,
            label=UNDECLARED_CONTEXT_LABEL,
            lifecycle=lifecycle,
            third_party=False,
            cache_eligibility=None,
            detail=detail,
            item_count=item_count,
            digest=None,
        )

    @classmethod
    def _measured(
        cls,
        text: str,
        *,
        counter: ContextTokenCounter,
        model: str,
        segment_class: ContextSegmentClass,
        label: str,
        lifecycle: ContextLifecycle,
        third_party: bool,
        cache_eligibility: PromptCacheEligibility | None,
        detail: str | None,
        item_count: NonNegativeInt,
        digest: str | None,
    ) -> ContextSegment:
        """Shared counting body behind both public constructors.

        ``byte_count`` is UTF-8 bytes of the materialized text, which is what
        actually crosses the wire. ``len(str)`` would undercount every non-ASCII
        segment and badly undercount base64 file content (audit item R).

        The width comes from :class:`ContextTextWidth` rather than an inline
        ``.encode("utf-8")``, and that is a correctness requirement, not tidiness.
        The inline form raises ``UnicodeEncodeError`` on the lone surrogate a JSON
        escape can legally carry, and this line runs on the model-call path
        *inside* the recorder's per-class guard: a single stray escape in one
        tool result therefore discarded every message segment for that call, and
        the missing bytes reappeared inside ``unattributed_delta`` where they are
        indistinguishable from tokenizer drift. Losing the row was survivable;
        reporting a total that looked plausible was not.

        Non-``str`` input degrades to empty rather than raising, for the same
        fail-open reason the counter itself never raises (§6.4).
        """

        material = text if isinstance(text, str) else ""
        if digest:
            estimated_tokens, counter_source = counter.count_digested(
                material,
                model=model,
                digest=digest,
            )
        else:
            estimated_tokens, counter_source = counter.count(material, model=model)
        return cls(
            segment_class=segment_class,
            label=label,
            lifecycle=lifecycle,
            third_party=third_party,
            detail=detail,
            byte_count=ContextTextWidth.utf8_byte_count(material),
            estimated_tokens=estimated_tokens,
            item_count=item_count,
            cache_eligibility=cache_eligibility,
            counter_source=counter_source,
        )


class ContextOccupancySnapshot(RuntimeContract):
    """One model call's context window, decomposed and reconciled (§4.5).

    Identity is ``(model_call_id, attempt_ordinal)``. Retries do not overwrite
    (§6.3): a retried call produces a second snapshot, and rollups deduplicate on
    ``model_call_id`` taking the last attempt — because the retry's context is
    genuinely different from the attempt that failed, and averaging them would
    describe a request that never existed.

    The three token totals are deliberately *not* redundant:

    - ``estimated_input_tokens`` is ours, decomposable, approximate.
    - ``provider_input_tokens`` is the provider's, authoritative, opaque. It is
      **copied** from the same ``NormalizedTokenUsage`` the ``UsageMeter``
      consumes — read-side denormalization for reconciliation, never a second
      source of billing truth (§6.1).
    - ``cached_input_tokens`` / ``cache_creation_input_tokens`` are what makes
      the report correct rather than merely large (§6.6). A resident segment
      that is cached bills at roughly a tenth of a fresh one; without these a
      reader would recommend trimming the stable prefix, which is exactly
      backwards.

    ``free_tokens`` is ``None`` — not zero — when the model is absent from the
    pricing catalog and no context window is known. Zero would assert a full
    window; ``None`` states that we do not know, which is the honest claim.
    """

    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: Literal[1] = 1
    model_call_id: Annotated[str, Field(min_length=1, max_length=200)]
    assembly_record_id: Annotated[str, Field(max_length=200)] | None = None
    attempt_ordinal: PositiveInt = 1
    graph_scope: GraphScope
    provider: Annotated[str, Field(min_length=1, max_length=120)]
    model_family: Annotated[str, Field(min_length=1, max_length=200)]
    context_window_tokens: NonNegativeInt | None = None
    segments: tuple[ContextSegment, ...] = ()
    estimated_input_tokens: NonNegativeInt = 0
    provider_input_tokens: NonNegativeInt | None = None
    cached_input_tokens: NonNegativeInt = 0
    cache_creation_input_tokens: NonNegativeInt = 0
    undeclared_tokens: NonNegativeInt = 0
    unattributed_delta: int = 0
    free_tokens: int | None = None

    @field_validator("segments")
    @classmethod
    def _canonicalize_order(
        cls,
        value: tuple[ContextSegment, ...],
    ) -> tuple[ContextSegment, ...]:
        """Sort segments into their canonical order on every construction path.

        Canonicalizing here rather than only in :class:`SnapshotBuilder` means a
        snapshot read back from JSONB, or built directly by a test, orders
        identically to one the builder produced. The sort is stable, so
        segments tying on all three key components keep their input order and
        the result stays deterministic.
        """

        return tuple(sorted(value, key=lambda segment: segment.sort_key))


class SnapshotBuilder:
    """Assemble a :class:`ContextOccupancySnapshot` and derive §3.3's reconciliation.

    The builder owns the derived arithmetic so there is exactly one place the
    reconciliation rules live:

    ``estimated_input_tokens``
        Σ ``segment.estimated_tokens``. Nothing is scaled, weighted, or
        redistributed — see the module docstring.

    ``undeclared_tokens``
        Σ over segments carrying ``UNDECLARED_CONTEXT_LABEL``. The runtime
        records that label instead of raising, because a measurement concern
        must never take a run down (§6.4); the AST conformance gate in PRD-02 is
        what hard-fails, in CI, where failing is free. Undeclared bytes are
        still real occupancy, so they also count into
        ``estimated_input_tokens`` — this field names the offending subset, it
        does not carve it out of the total.

    ``unattributed_delta``
        ``provider_input_tokens − estimated_input_tokens``, signed, and ``0``
        when the provider reported nothing. Zero is right for the unreported
        case: a delta against an absent total is not a small residual, it is no
        measurement at all, and inventing one would put fiction into the field
        whose entire job is honesty.

    ``free_tokens``
        ``context_window_tokens −`` the best available occupancy: the provider's
        total when it reported one, our estimate otherwise. ``None`` when the
        window is unknown.

    A note for the wiring in PRD-05: pass ``provider_input_tokens=None`` when the
    provider did not report usage. Passing a defaulted ``0`` is treated as a real
    reported total and yields a large negative delta on every call.
    """

    _ZERO_DELTA: Final[int] = 0

    def build(
        self,
        *,
        model_call_id: str,
        graph_scope: GraphScope,
        provider: str,
        model_family: str,
        segments: Iterable[ContextSegment] = (),
        assembly_record_id: str | None = None,
        attempt_ordinal: PositiveInt = 1,
        context_window_tokens: NonNegativeInt | None = None,
        provider_input_tokens: NonNegativeInt | None = None,
        cached_input_tokens: NonNegativeInt = 0,
        cache_creation_input_tokens: NonNegativeInt = 0,
    ) -> ContextOccupancySnapshot:
        """Return the reconciled snapshot for one model call attempt.

        Raises :class:`ContextOccupancyError` — and only that — when the inputs
        cannot form a valid record, so the caller's fail-open guard has a single
        type to catch and no validation text leaks into it.
        """

        # Materialize once — ``segments`` may be a one-shot generator from the
        # measurement pass, and the sums below walk it three times. Canonical
        # ordering is applied by the record itself, so every construction path
        # (builder, JSONB read-back, test) lands on the same sequence.
        collected = tuple(segments)
        estimated_input_tokens = self._sum_estimated_tokens(collected)
        try:
            return ContextOccupancySnapshot(
                model_call_id=model_call_id,
                assembly_record_id=assembly_record_id,
                attempt_ordinal=attempt_ordinal,
                graph_scope=graph_scope,
                provider=provider,
                model_family=model_family,
                context_window_tokens=context_window_tokens,
                segments=collected,
                estimated_input_tokens=estimated_input_tokens,
                provider_input_tokens=provider_input_tokens,
                cached_input_tokens=cached_input_tokens,
                cache_creation_input_tokens=cache_creation_input_tokens,
                undeclared_tokens=self._sum_undeclared_tokens(collected),
                unattributed_delta=self._unattributed_delta(
                    provider_input_tokens=provider_input_tokens,
                    estimated_input_tokens=estimated_input_tokens,
                ),
                free_tokens=self._free_tokens(
                    context_window_tokens=context_window_tokens,
                    provider_input_tokens=provider_input_tokens,
                    estimated_input_tokens=estimated_input_tokens,
                ),
            )
        except ValidationError as error:
            raise ContextOccupancyError(field_paths=self._field_paths(error)) from error

    @staticmethod
    def _sum_estimated_tokens(segments: tuple[ContextSegment, ...]) -> int:
        """Σ of every segment's estimate — our decomposable half of §3.3."""

        return sum(segment.estimated_tokens for segment in segments)

    @classmethod
    def _sum_undeclared_tokens(cls, segments: tuple[ContextSegment, ...]) -> int:
        """Σ over segments no declaration covered. Expected zero (§4.4)."""

        return sum(
            segment.estimated_tokens for segment in segments if segment.is_undeclared
        )

    @classmethod
    def _unattributed_delta(
        cls,
        *,
        provider_input_tokens: int | None,
        estimated_input_tokens: int,
    ) -> int:
        """Signed residual against the provider's authoritative total."""

        if provider_input_tokens is None:
            return cls._ZERO_DELTA
        return provider_input_tokens - estimated_input_tokens

    @staticmethod
    def _free_tokens(
        *,
        context_window_tokens: int | None,
        provider_input_tokens: int | None,
        estimated_input_tokens: int,
    ) -> int | None:
        """Remaining window within this scope only, or ``None`` when unknown.

        Signed on purpose. A negative value means the request exceeded the
        window we believe the model has, which is a real and useful thing to
        report — clamping it to zero would hide a stale pricing row or a
        genuinely over-stuffed request behind a plausible-looking number.
        """

        if context_window_tokens is None:
            return None
        occupied = (
            estimated_input_tokens
            if provider_input_tokens is None
            else provider_input_tokens
        )
        return context_window_tokens - occupied

    @staticmethod
    def _field_paths(error: ValidationError) -> tuple[str, ...]:
        """Extract failing field paths only — never the offending values (§6.5)."""

        return tuple(
            ".".join(str(part) for part in detail.get("loc", ()))
            for detail in error.errors()
        )


__all__ = (
    "ContextOccupancyError",
    "ContextOccupancySnapshot",
    "ContextSegment",
    "GraphScope",
    "SnapshotBuilder",
)
