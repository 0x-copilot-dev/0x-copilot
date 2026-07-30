"""Public response schemas for the Context Occupancy Ledger read API (§7).

Usage answers *what did this run cost*. Occupancy answers the question usage
cannot: **what is in the model's context window right now, and who put it
there.** These are the wire contracts for the second question — the per-turn
series for a run, and the "what is resident right now" snapshot for a
conversation.

**Why these are separate types from the ledger's own contracts.** The
measurement lane already owns two perfectly good shapes: the domain snapshot
(``agent_runtime.observability.context_occupancy.ContextOccupancySnapshot``) and
the durable row (``RuntimeContextOccupancyRecord``). Neither is returned
directly, for opposite reasons:

- The durable row carries ``org_id`` and stores its decomposition as an opaque
  JSONB envelope. Returning it would put a tenant identifier on a user-visible
  payload and make ``segments_json``'s internal envelope key part of the public
  contract.
- The domain snapshot is an *internal* measurement contract that PRDs 04–08
  evolve freely. Serving it would make every field the ledger adds for its own
  purposes an instant public API commitment.

So this module holds a third, deliberately thin shape whose only job is to be
the thing clients depend on. It reuses the closed vocabularies (segment class,
lifecycle, cache eligibility, counter source) because those *are* the shared
contract — re-typing them here would create a fourth copy that could silently
drift from the values actually stored.

**Nothing here carries content (§6.5).** Segments are counts plus bounded
identifiers: an ``owner:name`` label, a tool name, a ``fragment_id``, a message
ordinal range.

The bounds below mirror the **producer's** — ``ContextSegment`` in the
observability lane, the one object that can put a segment on this wire — rather
than the durability envelope's coarse structural backstop. That distinction was
a live hole. The envelope applies one uniform width to every string in a
segment, so it has to admit the widest legal one (a 401-character ``owner:name``
label); reading ``detail`` under that same width meant this contract published
512 characters of arbitrary text, newlines included, for a field whose only
producer clips it to 200 printable characters and whose content comes from an
*untrusted* MCP-registry tool name. A read contract wider than its writer is not
defence in depth, it is the gap. So the rule is: **this module accepts exactly
what a measurement can emit** — no more (a wider row is a leak, and is dropped)
and no less (a narrower bound would make a legitimately-written segment
unreadable). The two halves are gated against drift in
``tests/unit/runtime_api/test_context_occupancy_schemas.py``.

**Reading is fail-open, exactly like measuring (§6.4).** A stored segment this
build cannot parse — written by a newer writer, or by a schema the reader has
not learned yet — is dropped from the decomposition and counted into
``unreadable_segment_count`` rather than failing the whole read. The snapshot's
rollup totals are stored columns, not sums of the segment list, so they stay
authoritative even when part of the decomposition is unreadable. Saying "here
are the totals and 3 segments I could not read" is a strictly better answer than
a 500.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import logging
from typing import Annotated, ClassVar, Literal

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
    ContextLifecycle,
    ContextSegmentClass,
)
from agent_runtime.observability.context_token_counter import TokenCounterSource
from agent_runtime.persistence.records import (
    RuntimeContextGraphScope,
    RuntimeContextOccupancyRecord,
)
from agent_runtime.prompts.assembly import PromptCacheEligibility


_LOGGER = logging.getLogger(__name__)


class ContextOccupancySegment(RuntimeContract):
    """One attributable slice of a materialized provider request, on the wire.

    The join of a *declaration* — who owns this text, which part of the provider
    request it lands in, how often it is re-sent — and a *measurement* — how many
    bytes, how many tokens, counted by which tier of the fallback chain.

    ``lifecycle`` is the field that makes a number actionable rather than merely
    large: ``resident`` bytes are rent charged on every model call and are fixed
    by deferring or trimming the surface, while ``per_result`` bytes are a
    multiplier on tool-call count and are fixed by shrinking the per-result note.
    A client that renders occupancy without it will recommend the wrong change.

    ``label`` is an owner-namespaced ``"owner:name"`` string, or the reserved
    ``UNDECLARED`` sentinel when measurement found bytes no declaration covers.
    It is deliberately **not** an enum: a central list of every contributor would
    be stale the moment someone adds a tool (§3.2). Clients must treat it as an
    opaque grouping key and must not switch on known values.

    ``cache_eligibility`` is what stops a reader concluding that the largest
    resident segment is the one to cut (§6.6). A cached stable prefix bills at
    roughly a tenth of a fresh one, so "large but cached" and "large and re-billed
    every turn" are different findings with different fixes.
    """

    class Limits:
        """Wire bounds, taken from the producer rather than re-picked.

        Each is single-sourced from the object that decides it, so no literal
        here can drift from what a measurement can actually emit:

        ``MAX_LABEL``
            ``context_origin.MAX_LABEL_LENGTH`` — the exact width a rendered
            ``owner:name`` declaration can reach. Restating it as a literal is
            what broke the label bound once already.
        ``MAX_DETAIL``
            ``ContextSegment.MAX_DETAIL_LENGTH``, mirrored on the durability
            boundary as ``MAX_SEGMENT_DETAIL_CHARS``. Read from the persistence
            constant so the column and the wire cannot disagree about what a
            stored ``detail`` is allowed to be.
        """

        MAX_LABEL = MAX_CONTEXT_LABEL_LENGTH
        MAX_DETAIL = RuntimeContextOccupancyRecord.Limits.MAX_SEGMENT_DETAIL_CHARS

    segment_class: ContextSegmentClass
    label: Annotated[str, Field(min_length=1, max_length=Limits.MAX_LABEL)]
    lifecycle: ContextLifecycle
    third_party: bool = False
    detail: str | None = Field(default=None, max_length=Limits.MAX_DETAIL)
    byte_count: NonNegativeInt = 0
    estimated_tokens: NonNegativeInt = 0
    item_count: NonNegativeInt = 1
    cache_eligibility: PromptCacheEligibility | None = None
    counter_source: TokenCounterSource

    @field_validator("detail")
    @classmethod
    def _reject_content_shaped_detail(cls, value: str | None) -> str | None:
        """Refuse a stored ``detail`` that reads as content rather than a name.

        The same closed check ``ContextSegment`` applies when the segment is
        *measured*, restated where the segment is *published*. The length bound
        above is the blunt half; this is the sharp one, and a length bound cannot
        express it: every detail this runtime produces is a single printable
        token (``publish_artifact``, ``msg[12]``, ``system[unattributed]``) while
        a smuggled message or tool-result body is almost always multi-line.

        Restated here rather than inherited because the read path and the write
        path are different objects by design (see the module docstring), and this
        is the boundary a client is actually served from. A row that trips it is
        counted into ``unreadable_segment_count`` and dropped — the totals stay
        exact, and the caller is told the decomposition was incomplete rather
        than handed something this contract cannot vouch for.
        """

        if value is None:
            return None
        if any(character < " " or character == "\x7f" for character in value):
            msg = "detail must not contain control characters"
            raise ValueError(msg)
        return value

    @classmethod
    def from_stored(cls, stored: Mapping[str, object]) -> "ContextOccupancySegment":
        """Parse one stored segment object, raising only :class:`ValidationError`.

        Kept a plain constructor rather than a forgiving one: the *caller*
        decides what to do with an unparseable segment, because only the caller
        knows whether it is reading a whole series (drop and count it) or
        validating a single event payload (reject the payload). Swallowing the
        failure here would take that choice away and hide a real writer/reader
        skew behind a silently shorter list.
        """

        return cls.model_validate(dict(stored))


class ContextOccupancySnapshotPayload(RuntimeContract):
    """One model call's context window, decomposed and reconciled, on the wire.

    Identity is ``(model_call_id, attempt_ordinal)``. A retried call is a second
    materialized request against a second window state, so it appears as a second
    snapshot rather than overwriting the first (§6.3) — a client charting
    utilization deduplicates on ``model_call_id`` and takes the last attempt.

    The token totals are deliberately not redundant, and a client that collapses
    them loses the entire point of the ledger:

    ``estimated_input_tokens``
        Ours. Decomposable into ``segments``, approximate.
    ``provider_input_tokens``
        The provider's. Authoritative, opaque, and ``None`` when the provider
        reported no usage for the call.
    ``undeclared_tokens``
        Measured bytes matching no declaration. **Expected 0**; anything above it
        is a first-party contract defect, not a rounding artifact. Already
        included in ``estimated_input_tokens`` — this field names the offending
        subset, it does not carve it out.
    ``unattributed_delta``
        ``provider_input_tokens - estimated_input_tokens``, **signed**. Provider
        wire overhead and tokenizer drift live here; negative means we
        over-counted. Expected small and bounded, never zero-by-construction.

    ``free_tokens`` is ``None`` — never ``0`` — when the model is absent from the
    pricing catalog. Zero would assert a full window; ``None`` states that the
    denominator is unknown, which is the honest claim. It is also computed
    **within one ``graph_scope`` only** (§6.2): a subagent has its own window, so
    a parent's free space is unaffected by what a child put in its own.

    ``unreadable_segment_count`` is normally ``0``. A non-zero value means this
    server read a row whose decomposition it does not fully understand — almost
    always a reader older than the writer that produced it. The rollup fields
    above remain exact regardless, because they are stored columns rather than
    sums over the list.
    """

    SCHEMA_VERSION: ClassVar[int] = 1

    class Limits:
        """Identifier widths, mirroring the columns these fields are read from.

        Load-bearing for more than tidiness. This contract is also the *inbound*
        validator for the ``context_occupancy`` stream payload — the projector in
        ``runtime_api.schemas.events`` validates-and-re-dumps rather than
        allow-listing keys, on the stated reasoning that "the shape already *is*
        the allow-list". That reasoning only holds if the shape is bounded, and
        three of these fields were bare ``str``: an unbounded identifier on an
        event payload is a text channel through a §6.5 surface, no matter how
        strict the fields around it are.
        """

        MAX_IDENTIFIER = RuntimeContextOccupancyRecord.Limits.MAX_IDENTIFIER_CHARS
        MAX_PROVIDER = RuntimeContextOccupancyRecord.Limits.MAX_PROVIDER_CHARS

    schema_version: Literal[1] = 1
    model_call_id: Annotated[str, Field(min_length=1, max_length=Limits.MAX_IDENTIFIER)]
    attempt_ordinal: PositiveInt = 1
    assembly_record_id: str | None = Field(
        default=None, max_length=Limits.MAX_IDENTIFIER
    )
    graph_scope: RuntimeContextGraphScope
    provider: Annotated[str, Field(min_length=1, max_length=Limits.MAX_PROVIDER)]
    model_family: Annotated[str, Field(min_length=1, max_length=Limits.MAX_IDENTIFIER)]
    measured_at: datetime
    context_window_tokens: NonNegativeInt | None = None
    estimated_input_tokens: NonNegativeInt = 0
    provider_input_tokens: NonNegativeInt | None = None
    cached_input_tokens: NonNegativeInt = 0
    cache_creation_input_tokens: NonNegativeInt = 0
    undeclared_tokens: NonNegativeInt = 0
    unattributed_delta: int = 0
    free_tokens: int | None = None
    segments: tuple[ContextOccupancySegment, ...] = ()
    unreadable_segment_count: NonNegativeInt = 0

    @classmethod
    def from_record(
        cls,
        record: RuntimeContextOccupancyRecord,
    ) -> "ContextOccupancySnapshotPayload":
        """Project one durable row onto its public shape, dropping nothing silently.

        Field-for-field except in three places, each deliberate:

        - ``org_id`` is **not** projected. Tenant identifiers do not ride
          user-visible payloads; the caller's own identity already scoped the
          read that produced this row.
        - ``free_tokens`` is taken from the record's derived property rather than
          recomputed here, so the read API can never report a different number
          than the snapshot that produced the row.
        - ``segments`` are parsed defensively (see the module docstring), with
          the unparseable count surfaced instead of swallowed.
        """

        segments, unreadable = cls._segments_of(record)
        return cls(
            model_call_id=record.model_call_id,
            attempt_ordinal=record.attempt_ordinal,
            assembly_record_id=record.assembly_record_id,
            graph_scope=record.graph_scope,
            provider=record.provider,
            model_family=record.model_family,
            measured_at=record.created_at,
            context_window_tokens=record.context_window_tokens,
            estimated_input_tokens=record.estimated_input_tokens,
            provider_input_tokens=record.provider_input_tokens,
            cached_input_tokens=record.cached_input_tokens,
            cache_creation_input_tokens=record.cache_creation_input_tokens,
            undeclared_tokens=record.undeclared_tokens,
            unattributed_delta=record.unattributed_delta,
            free_tokens=record.free_tokens,
            segments=segments,
            unreadable_segment_count=unreadable,
        )

    @classmethod
    def _segments_of(
        cls,
        record: RuntimeContextOccupancyRecord,
    ) -> tuple[tuple[ContextOccupancySegment, ...], int]:
        """Parse the stored decomposition, returning what parsed and what did not.

        The read path mirrors the write path's fail-open posture (§6.4). One
        segment carrying a field this build does not know must not cost the
        caller the other thirty-nine, and must not cost them the row's totals —
        which are exact either way.
        """

        parsed: list[ContextOccupancySegment] = []
        unreadable = 0
        for stored in record.segments:
            try:
                parsed.append(ContextOccupancySegment.from_stored(stored))
            except ValidationError:
                unreadable += 1
        if unreadable:
            _LOGGER.warning(
                "Dropped %d unreadable context occupancy segment(s) for model "
                "call %s; the snapshot totals remain exact.",
                unreadable,
                record.model_call_id,
            )
        return tuple(parsed), unreadable


class ContextOccupancyResponse(RuntimeContract):
    """Response for ``GET /v1/agent/runs/{run_id}/context/occupancy``.

    The per-turn series for one run, oldest-first, so a client renders the shape
    of a conversation filling its window over time without re-sorting.

    ``graph_scope`` echoes the filter that was applied (``None`` = every scope),
    because the series is only summable *within* a scope. A subagent runs against
    its own window, so a client that receives an unfiltered series and adds it up
    will report utilization above 100% on any run that delegates (§6.2). Echoing
    the filter makes that a visible property of the response rather than
    something the caller has to remember it asked for.

    An empty ``snapshots`` tuple is the answer for a run that has no occupancy —
    an unknown run, a run in another tenant, a run belonging to another user, or
    a genuine run measured before this ledger existed. Those cases are
    **deliberately indistinguishable**: a 404 for "wrong tenant" and a 200 for
    "no rows yet" would turn this endpoint into an existence oracle for run ids
    in other organizations.
    """

    run_id: str
    graph_scope: RuntimeContextGraphScope | None = None
    snapshots: tuple[ContextOccupancySnapshotPayload, ...] = ()


class ConversationContextOccupancyResponse(RuntimeContract):
    """Response for ``GET /v1/agent/conversations/{conversation_id}/context/occupancy``.

    "What is in context right now" — the newest **root-scope** snapshot across the
    conversation's runs, plus the run it came from.

    Root-scope only, and not by omission. A subagent's last model call is often
    the most recent snapshot in wall-clock terms, but it describes a different
    window that has since been discarded. Returning it as "what is in context"
    would be a confident answer to a different question.

    ``snapshot`` is ``None`` when the conversation has no measured root-scope
    occupancy, which covers the same deliberately-indistinguishable set as the
    run endpoint: unknown, another tenant's, another user's, or simply not yet
    measured.
    """

    conversation_id: str
    run_id: str | None = None
    snapshot: ContextOccupancySnapshotPayload | None = None


__all__ = (
    "ContextOccupancyResponse",
    "ContextOccupancySegment",
    "ContextOccupancySnapshotPayload",
    "ConversationContextOccupancyResponse",
)
