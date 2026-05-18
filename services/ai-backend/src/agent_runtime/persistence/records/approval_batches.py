"""ApprovalBatch — first-class persistence entity 1:1 with a LangGraph interrupt.

The LangGraph interrupt is the single source of truth for "what does the graph
need to resume?". An ``ApprovalBatch`` is the persistence projection of that
interrupt: it owns a tuple of ``ApprovalItem`` rows (one per ``action_request``)
and a single ``status`` that gates the resume. The batch is what we lock when
the last item is resolved — not the individual item — so concurrent decisions
on the same batch never race two resumes.

This module defines only the value types. Atomic transition semantics live on
the persistence adapter (in-memory: ``asyncio.Lock`` per ``batch_id``;
postgres: ``SELECT ... FOR UPDATE`` on the batch row).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import Field

from agent_runtime.execution.contracts import JsonObject, RuntimeContract


class BatchItemDecision(StrEnum):
    """The set of decisions a user (or the sweeper) can record on one item.

    Values mirror ``runtime_api.schemas.common.BatchItemDecision`` so cross-
    layer conversion is a no-op string round-trip. This enum is owned by the
    persistence layer so the records package has no dependency on
    ``runtime_api.schemas`` (and the import-cycle that would otherwise
    follow). ``FORWARDED`` is included for parity even though forwarding
    never reaches the batch primitive — forwarded decisions stop at the API
    layer.
    """

    APPROVED = "approved"
    REJECTED = "rejected"
    FORWARDED = "forwarded"


class ApprovalBatchStatus(StrEnum):
    """Lifecycle status of one ApprovalBatch.

    ``PENDING`` — at least one item is unresolved; the batch gates the run.
    ``RESUMING`` — the last item resolved and a worker has taken the resume
        lock; LangGraph is being invoked with the aligned ``decisions`` list.
        Only one worker can flip ``PENDING -> RESUMING`` per batch.
    ``RESOLVED`` — the resume completed (either continued the run or terminated
        it). Subsequent decisions on items are no-ops.
    ``EXPIRED`` — the sweeper marked the batch expired because at least one item
        passed ``expires_at`` while still ``PENDING``. Treated like ``RESOLVED``
        by the resume gate.
    """

    PENDING = "pending"
    RESUMING = "resuming"
    RESOLVED = "resolved"
    EXPIRED = "expired"


class BatchOutcomeStatus(StrEnum):
    """Result of an atomic per-item decision + batch-transition attempt.

    ``BATCH_INCOMPLETE`` — the item decision was recorded but the batch still
        has unresolved siblings; the handler should not resume.
    ``READY_TO_RESUME`` — this caller atomically flipped the batch to
        ``RESUMING`` and now owns the resume invocation. The outcome carries the
        loaded batch + ordered items so the caller can build the aligned
        ``decisions`` list without re-reading.
    ``LOST_RACE`` — another worker already flipped the batch past ``PENDING``
        (or the batch has expired). The caller must no-op.
    """

    BATCH_INCOMPLETE = "batch_incomplete"
    READY_TO_RESUME = "ready_to_resume"
    LOST_RACE = "lost_race"


class ApprovalBatchRecord(RuntimeContract):
    """One ApprovalBatch row — 1:1 with a single LangGraph interrupt."""

    batch_id: str
    """Same value as the native LangGraph interrupt id. The batch is the
    interrupt's persistence projection, so reusing the id keeps the inverse
    lookup (event_envelope.batch_id -> batch row) a single key access."""
    run_id: str
    org_id: str
    status: ApprovalBatchStatus = ApprovalBatchStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)


class ApprovalBatchItemRecord(RuntimeContract):
    """One ApprovalItem row — a single user-visible approval card.

    Each item corresponds to exactly one position in the interrupt's
    ``action_requests`` list. ``index`` is the *typed* position (not parsed
    from a string id) and drives ``decisions_in_order``.

    Every other per-item field (tool name, server name, arguments, risk
    level, structured params, ...) continues to live on the existing
    ``ApprovalRequestRecord`` (renamed conceptually to "item" but
    structurally unchanged so audit / inbox / forwarding paths keep working).
    This record carries only the batch-membership identity and the decision.
    """

    item_id: str
    """Presentation key. Same string the frontend uses today
    (``<batch_id>:<index>``). N=1 and N=N follow the same format — there is no
    special case for single-item batches."""
    batch_id: str
    """Foreign key into ``ApprovalBatchRecord.batch_id``."""
    index: int
    """0-based position within ``action_requests``. Typed; never parsed."""
    decision: BatchItemDecision | None = None
    """``None`` while the item is unresolved. Set when the user (or sweeper)
    resolves it."""


class BatchTransitionOutcome(RuntimeContract):
    """Result of ``record_item_decision_and_maybe_lock_batch``.

    The handler reads ``status`` to decide whether to resume. When the status
    is ``READY_TO_RESUME``, ``batch`` and ``items`` carry the loaded state so
    the handler can build the aligned ``decisions`` list without a second
    persistence round-trip.
    """

    status: BatchOutcomeStatus
    batch: ApprovalBatchRecord | None = None
    items: tuple[ApprovalBatchItemRecord, ...] = ()

    def decisions_in_order(self) -> tuple[BatchItemDecision, ...]:
        """Return the per-item decisions aligned to ``action_requests`` order.

        Caller invariants:
        - ``status`` must be ``READY_TO_RESUME``.
        - Every item must carry a non-null ``decision`` (the atomic transition
          guarantees this; an unresolved item would have produced
          ``BATCH_INCOMPLETE``).
        """
        if self.status is not BatchOutcomeStatus.READY_TO_RESUME:
            raise ValueError(
                "decisions_in_order is only defined for READY_TO_RESUME outcomes"
            )
        ordered = sorted(self.items, key=lambda item: item.index)
        decisions: list[BatchItemDecision] = []
        for item in ordered:
            if item.decision is None:
                raise ValueError(
                    f"ApprovalBatchItem {item.item_id} has no decision; "
                    "READY_TO_RESUME contract violated"
                )
            decisions.append(item.decision)
        return tuple(decisions)


class ApprovalBatchSpec(RuntimeContract):
    """Insertion bundle — one batch plus its ordered items.

    Adapters accept this rather than two separate arguments so the
    "insert batch + N items in one atomic write" contract has a single
    signature.
    """

    batch: ApprovalBatchRecord
    items: tuple[ApprovalBatchItemRecord, ...]

    @classmethod
    def build(
        cls,
        *,
        batch: ApprovalBatchRecord,
        items: Sequence[ApprovalBatchItemRecord],
    ) -> ApprovalBatchSpec:
        """Validate that every item's ``batch_id`` matches the batch's id."""
        for item in items:
            if item.batch_id != batch.batch_id:
                raise ValueError(
                    f"ApprovalBatchItem {item.item_id} has batch_id "
                    f"{item.batch_id!r} but the batch is {batch.batch_id!r}"
                )
        indices = sorted(item.index for item in items)
        expected = list(range(len(items)))
        if indices != expected:
            raise ValueError(
                f"ApprovalBatchItem indices must be 0..N-1 contiguous; got {indices}"
            )
        return cls(batch=batch, items=tuple(items))
