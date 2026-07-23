"""HTTP IO schemas for the single-artifact staged-write engine (PRD-D1).

Request bodies for ``POST /v1/agent/stages/{stage_id}/revisions`` and
``…/decisions`` plus the ``StagedWriteView`` response every stage route returns.
The view is the wire projection of the domain ``StagedWriteState`` (the pure
fold) — ``StagedWriteView.from_state`` is the mechanical field-by-field mapper
(the convention the draft-view projection uses).

``StageDecisionRequest.decision`` accepts the FULL SDR decision enum
(``approve|reject|hold|restore``) on purpose: ``hold`` must reach the domain so
``WriteStager.record_decision`` raises the typed 422 (``UnsupportedDecision``),
not be rejected at the pydantic boundary — the adversarial matrix test asserts
the typed error class.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from agent_runtime.surfaces_v2.ledger_ids import LedgerIdCodec
from agent_runtime.surfaces_v2.staging import StagedWriteState

__all__ = [
    "StageAuthorshipSpanView",
    "StageDecisionRequest",
    "StageDecisionView",
    "StageRevisionRequest",
    "StageRevisionView",
    "StageTargetView",
    "StagedWriteView",
]


class StageRevisionRequest(BaseModel):
    """Body for ``POST /stages/{stage_id}/revisions`` — a user free-form edit."""

    model_config = ConfigDict(extra="forbid")

    base_rev: PositiveInt
    content_text: str = Field(min_length=0, max_length=200_000)
    title: str | None = Field(default=None, max_length=240)


class StageDecisionRequest(BaseModel):
    """Body for ``POST /stages/{stage_id}/decisions``.

    ``rev`` is required for ``approve``/``reject`` (WYSIWYG — you decide on the
    rev you see) and optional/ignored for ``restore``; the domain enforces that
    (missing rev on approve/reject ⇒ typed 422), so it stays optional here.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject", "hold", "restore"]
    rev: PositiveInt | None = None


class StageTargetView(BaseModel):
    """The connector server + operation the staged write targets."""

    model_config = ConfigDict(extra="forbid")

    connector: str
    op: str


class StageAuthorshipSpanView(BaseModel):
    """A ``[start, end)`` char range of the revision's new text and its author."""

    model_config = ConfigDict(extra="forbid")

    start: int
    end: int
    author: Literal["agent", "user"]


class StageRevisionView(BaseModel):
    """One revision on the wire: number, author, snapshot ref, spans, ledger id."""

    model_config = ConfigDict(extra="forbid")

    rev: int
    author: str
    proposal_ref: str
    diff_ref: str
    authorship_spans: tuple[StageAuthorshipSpanView, ...]
    ledger_id: str


class StageDecisionView(BaseModel):
    """One recorded decision on the wire."""

    model_config = ConfigDict(extra="forbid")

    decision: str
    scope_rev: int | None
    actor: str
    ledger_id: str


class StagedWriteView(BaseModel):
    """Wire projection of a staged write (the route response for all three)."""

    model_config = ConfigDict(extra="forbid")

    stage_id: str
    surface_id: str
    run_id: str
    draft_id: str
    target: StageTargetView
    latest_rev: int
    approved_rev: int | None
    status: str
    revisions: tuple[StageRevisionView, ...]
    decisions: tuple[StageDecisionView, ...]

    @classmethod
    def from_state(cls, *, run_id: str, state: StagedWriteState) -> StagedWriteView:
        """Project the domain fold state onto the wire view (ledger ids added)."""

        return cls(
            stage_id=state.stage_id,
            surface_id=state.surface_id,
            run_id=run_id,
            draft_id=state.draft_id,
            target=StageTargetView(
                connector=state.target_connector, op=state.target_op
            ),
            latest_rev=state.latest_rev,
            approved_rev=state.approved_rev,
            status=state.status.value,
            revisions=tuple(
                StageRevisionView(
                    rev=revision.rev,
                    author=revision.author,
                    proposal_ref=revision.proposal_ref,
                    diff_ref=revision.diff_ref,
                    authorship_spans=tuple(
                        StageAuthorshipSpanView(
                            start=span.start, end=span.end, author=span.author
                        )
                        for span in revision.authorship_spans
                    ),
                    ledger_id=cls._ledger_id(run_id, revision.sequence_no),
                )
                for revision in state.revisions
            ),
            decisions=tuple(
                StageDecisionView(
                    decision=decision.decision,
                    scope_rev=decision.scope_rev,
                    actor=decision.actor,
                    ledger_id=cls._ledger_id(run_id, decision.sequence_no),
                )
                for decision in state.decisions
            ),
        )

    @staticmethod
    def _ledger_id(run_id: str, sequence_no: int) -> str:
        """Format the user-visible ledger id, degrading safely on a bad seq."""

        try:
            return LedgerIdCodec.format(run_id, sequence_no)
        except Exception:  # noqa: BLE001 - presentation only; never fail the view
            return f"r{run_id}·{sequence_no}"
