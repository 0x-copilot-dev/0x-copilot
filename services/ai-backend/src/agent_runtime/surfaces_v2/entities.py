"""Projection entity twins for the Work Ledger (PRD-A1 D2/D3).

Pydantic mirrors of the six api-types entity types
(``packages/api-types/src/ledger.ts``): the projection outputs later endpoints
serve (A3 ``GET /v1/agent/runs/{id}/surfaces``, D-wave decisions, E-wave
receipt). Same field names / optionality as the TypeScript. Values reuse the
enums + value objects from ``ledger_models`` so the vocabulary is defined once.

Note (2026-07-23 close-out): ``Surface`` here is the **ledger entity** (the
richer canvas/B-E-wave entity). A3's ``SurfaceSnapshot`` (the surfaces-fold
output) is a distinct **fold projection** that coexists additively — A3 does not
edit this model. Nothing here is wired into the runtime yet (contracts only).

Wire-shape tenancy rule: no ``org_id`` / ``user_id`` on any entity — attribution
rides the run envelope server-side.
"""

from __future__ import annotations

from enum import StrEnum

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_models import (
    AgentHold,
    DecisionActor,
    DecisionKind,
    DecisionScope,
    LedgerEventType,
    LedgerOpRef,
    RevisionAuthor,
    SurfaceKind,
    UsagePurpose,
    ViewBasis,
    ViewKeep,
    ViewTier,
)


class ReceiptAttribution(StrEnum):
    """How a receipt row is attributed (FR-E2 wording, wire-safe; A1-defined).

    Not a ledger event type and not in the SSOT ``enums`` block — a
    receipt-format construct the E-wave receipt fold assigns per row.
    """

    AUTO_RAN = "auto_ran"
    APPROVED = "approved"
    HELD = "held"
    REJECTED = "rejected"
    AUTO_APPLIED = "auto_applied"
    NO_VIEW_FIT = "no_view_fit"


class Revision(RuntimeContract):
    """One staged-write revision (draft snapshot), folded from ``revision.added``."""

    rev: int
    author: RevisionAuthor
    diff_ref: str
    created_at: str
    ledger_id: str


class Decision(RuntimeContract):
    """One recorded decision, folded from ``decision.recorded``."""

    decision: DecisionKind
    scope: DecisionScope
    actor: DecisionActor
    decided_at: str
    ledger_id: str


class SurfaceView(RuntimeContract):
    """The current view state of a surface (folded from ``view.*`` events)."""

    tier: ViewTier
    basis: ViewBasis
    spec_ref: str | None = None
    preference: ViewKeep | None = None


class Surface(RuntimeContract):
    """A live artifact surface, folded from ``surface.created`` + ``view.*``.

    ``view`` is required-nullable (present, ``None`` until a view is derived),
    mirroring the ts ``view: {...} | null``.
    """

    surface_id: str
    run_id: str
    kind: SurfaceKind
    title: str
    source: LedgerOpRef
    payload_ref: str
    ledger_id: str
    created_at: str
    view: SurfaceView | None


class StagedWrite(RuntimeContract):
    """A staged write with its revisions + decisions, folded from ``write.*``."""

    stage_id: str
    surface_id: str
    run_id: str
    target: LedgerOpRef
    proposal_ref: str
    rows: int | None
    agent_holds: tuple[AgentHold, ...]
    revisions: tuple[Revision, ...]
    decisions: tuple[Decision, ...]
    latest_rev: int


class UsageRecord(RuntimeContract):
    """One metered usage row, folded from ``usage.recorded`` (FR-G)."""

    purpose: UsagePurpose
    model: str
    tokens_in: int
    tokens_out: int
    run_id: str
    conversation_id: str
    surface_id: str | None = None
    created_at: str
    ledger_id: str


class RunReceiptTiles(RuntimeContract):
    """The receipt's headline counters."""

    reads_auto_ran: int
    writes_proposed: int
    writes_approved: int
    holds_untouched: int


class RunReceiptRow(RuntimeContract):
    """One line of a run receipt (fold, not narrative)."""

    ledger_id: str
    event_type: LedgerEventType
    title: str
    attribution: ReceiptAttribution
    at: str


class RunReceipt(RuntimeContract):
    """The folded run receipt (E-wave), mirroring the ts ``RunReceipt``."""

    run_id: str
    surface_id: str
    fold_ref: str
    generated_at: str
    tiles: RunReceiptTiles
    rows: tuple[RunReceiptRow, ...]


__all__ = [
    "Decision",
    "ReceiptAttribution",
    "Revision",
    "RunReceipt",
    "RunReceiptRow",
    "RunReceiptTiles",
    "StagedWrite",
    "Surface",
    "SurfaceView",
    "UsageRecord",
]
