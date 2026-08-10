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

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from agent_runtime.surfaces_v2.ledger_ids import LedgerIdCodec
from agent_runtime.surfaces_v2.staging import StagedWriteState

__all__ = [
    "StageApplyRequest",
    "StageAuthorshipSpanView",
    "StageDecisionRequest",
    "StageDecisionView",
    "StageRevisionRequest",
    "StageRevisionView",
    "StageRowChangeView",
    "StageRowCountsView",
    "StageRowView",
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

    At most one scope: ``rev`` (single-artifact D1; whole-stage reject/restore on
    a row-set) OR ``row_keys`` (row-set stance toggle — PRD-D3), never both. The
    domain enforces the per-verb rules — a rev-scoped ``hold`` / row-set approve
    422s, a missing-rev approve 422s, ``restore`` ignores rev (D1). This validator
    only rejects supplying BOTH scopes or an empty ``row_keys``.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject", "hold", "restore"]
    rev: PositiveInt | None = None
    row_keys: list[str] | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _at_most_one_scope(self) -> "StageDecisionRequest":
        if self.rev is not None and self.row_keys is not None:
            raise ValueError("Provide `rev` or `row_keys`, not both.")
        if self.row_keys is not None and not self.row_keys:
            raise ValueError("`row_keys` must be a non-empty list.")
        return self


class StageApplyRequest(BaseModel):
    """Body for ``POST /stages/{stage_id}/apply`` — apply exactly the named rows.

    The applied set must equal the current will-apply set exactly (the server
    re-checks; a mismatch is a 409). Held rows can never be named.
    """

    model_config = ConfigDict(extra="forbid")

    rev: PositiveInt
    row_keys: list[str] = Field(min_length=1, max_length=200)


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


class StageRowChangeView(BaseModel):
    """One field diff on a staged row (display only)."""

    model_config = ConfigDict(extra="forbid")

    field: str
    old: object | None = None
    new: object | None = None


class StageRowView(BaseModel):
    """One staged row on the wire: content (title/diffs) + folded state.

    ``target_args`` is server-only (never surfaced) — the client renders diffs
    from ``changes`` and re-sends only ``row_key`` on apply (WYSIWYG).
    """

    model_config = ConfigDict(extra="forbid")

    row_key: str
    title: str
    changes: tuple[StageRowChangeView, ...]
    stance: str
    agent_hold_reason: str | None = None
    decided_by: str | None = None
    apply_outcome: str | None = None


class StageRowCountsView(BaseModel):
    """Row-count summary for a staged row-set (counts header + apply label)."""

    model_config = ConfigDict(extra="forbid")

    total: int
    will_apply: int
    held: int
    applied: int
    failed: int


class StagedWriteView(BaseModel):
    """Wire projection of a staged write (the route response for all routes).

    ``rows`` / ``row_counts`` are ``None`` for a single-artifact (D1) stage and
    populated for a bulk row-set (D3).
    """

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
    rows: tuple[StageRowView, ...] | None = None
    row_counts: StageRowCountsView | None = None

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
            rows=cls._rows_view(state),
            row_counts=cls._counts_view(state),
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

    @classmethod
    def _rows_view(cls, state: StagedWriteState) -> tuple[StageRowView, ...] | None:
        """Compose the wire rows (state + content) for a row-set, else ``None``."""

        if state.rows is None:
            return None
        by_key = {row.row_key: row for row in state.staged_rows or ()}
        views: list[StageRowView] = []
        for row in state.rows:
            content = by_key.get(row.row_key)
            title = content.title if content is not None else row.row_key
            changes = content.changes if content is not None else ()
            views.append(
                StageRowView(
                    row_key=row.row_key,
                    title=title,
                    changes=tuple(
                        StageRowChangeView(
                            field=change.field, old=change.old, new=change.new
                        )
                        for change in changes
                    ),
                    stance=row.stance.value,
                    agent_hold_reason=row.agent_hold_reason,
                    decided_by=row.decided_by,
                    apply_outcome=row.apply_outcome,
                )
            )
        return tuple(views)

    @staticmethod
    def _counts_view(state: StagedWriteState) -> StageRowCountsView | None:
        """Project the row-count summary for a row-set stage, else ``None``."""

        counts = state.row_counts
        if counts is None:
            return None
        return StageRowCountsView(
            total=counts.total,
            will_apply=counts.will_apply,
            held=counts.held,
            applied=counts.applied,
            failed=counts.failed,
        )

    @staticmethod
    def _ledger_id(run_id: str, sequence_no: int) -> str:
        """Format the user-visible ledger id, degrading safely on a bad seq."""

        try:
            return LedgerIdCodec.format(run_id, sequence_no)
        except Exception:  # noqa: BLE001 - presentation only; never fail the view
            return f"r{run_id}·{sequence_no}"
