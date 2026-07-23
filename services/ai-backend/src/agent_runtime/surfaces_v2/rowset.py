"""Row-set contracts + validator for bulk staged writes (PRD-D3).

A row-set is one staged write carrying N per-row changes, each individually
decidable. The agent proposes it (per row: a stable ``row_key``, a human
``title``, the EXACT connector-op args for that row, and old→new field diffs)
and may **pre-hold** risky rows with a visible reason that survives a user
override (FR-C7). The fold turns the ledger into per-row :class:`RowState` +
:class:`RowCounts`; the D2 CommitEngine executes ONLY the approved rows.

Pure domain: these are ``RuntimeContract`` models + a total validator. Tool
input (rows, reasons, target args) is untrusted until :class:`RowsetValidator`
runs — validation failure is a typed domain error, and NO event is emitted
(the ledger records only what happened).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from agent_runtime.execution.contracts import JsonObject, JsonValue, RuntimeContract


class _Limits:
    """Caps for a proposed row-set (untrusted tool input — enforced fail-closed)."""

    MAX_ROWS = 200
    MAX_CHANGES_PER_ROW = 20
    REASON_MAX = 200
    TITLE_MAX = 200
    ROW_KEY_MAX = 200
    FIELD_MAX = 200


class RowFieldChange(RuntimeContract):
    """One field's old→new diff for a row (``old``/``new`` = value as read/proposed)."""

    field: str = Field(min_length=1, max_length=_Limits.FIELD_MAX)
    old: JsonValue | None = None  # value before (as read; None = absent)
    new: JsonValue | None = None  # proposed value


class StagedRow(RuntimeContract):
    """One proposed row change — the WYSIWYG unit a user approves/holds.

    ``target_args`` are the EXACT connector-op args the CommitEngine dispatches
    for THIS row, verbatim (FR-C3). ``changes`` are display diffs only.
    """

    row_key: str = Field(min_length=1, max_length=_Limits.ROW_KEY_MAX)
    title: str = Field(min_length=1, max_length=_Limits.TITLE_MAX)
    target_args: JsonObject = Field(default_factory=dict)
    changes: tuple[RowFieldChange, ...] = ()


class AgentHold(RuntimeContract):
    """An agent pre-hold: a row deliberately withheld with a visible reason (FR-C7)."""

    row_key: str = Field(min_length=1, max_length=_Limits.ROW_KEY_MAX)
    reason: str = Field(min_length=1, max_length=_Limits.REASON_MAX)


class RowStance(StrEnum):
    """A row's current decision stance in the fold."""

    WILL_APPLY = "will_apply"
    HELD = "held"


class RowState(RuntimeContract):
    """Fold output, per row.

    ``agent_hold_reason`` is STICKY: a user override flips ``stance`` to
    ``WILL_APPLY`` but the reason stays visible (FR-C7). ``decided_by`` is
    ``"agent"`` for a ``write.staged`` pre-hold (not a ``decision.recorded``),
    or ``"user"`` / ``"policy"`` from the ``decision.recorded.actor``.
    """

    row_key: str
    stance: RowStance
    agent_hold_reason: str | None = None
    decided_by: str | None = None  # "agent" | "user" | "policy" | None
    apply_outcome: str | None = None  # "applied" | "failed" | None


class RowCounts(RuntimeContract):
    """Projection summary over a stage's rows (fold output, per stage)."""

    total: int = 0
    will_apply: int = 0
    held: int = 0
    applied: int = 0
    failed: int = 0


# ---------------------------------------------------------------------------
# Typed validation error (routes/tool map this to a 422; no event emitted)
# ---------------------------------------------------------------------------


class RowsetValidationError(Exception):
    """A proposed row-set is malformed / over caps. Carries only a safe message."""

    code: str = "rowset_invalid"
    safe_message: str = "The proposed row-set is invalid."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.safe_message)
        if message is not None:
            self.safe_message = message


class RowsetValidator:
    """Validate a proposed ``(rows, agent_holds)`` fail-closed (pure, total).

    Enforces the caps, unique ``row_key`` within the stage, and that every
    ``agent_holds.row_key`` references an actual row. Raises
    :class:`RowsetValidationError` with a safe message on any violation — the
    caller emits NO ledger event on failure.
    """

    class _Messages:
        EMPTY = "A row-set must contain at least one row."
        TOO_MANY_ROWS = "The row-set exceeds the maximum row count."
        TOO_MANY_CHANGES = "A row exceeds the maximum number of field changes."
        DUPLICATE_ROW_KEY = "Row keys must be unique within a staged write."
        HOLD_UNKNOWN_ROW = "An agent hold references a row that is not in the set."
        DUPLICATE_HOLD = "Each row may be pre-held at most once."

    @classmethod
    def validate(
        cls,
        *,
        rows: tuple[StagedRow, ...],
        agent_holds: tuple[AgentHold, ...],
    ) -> None:
        if not rows:
            raise RowsetValidationError(cls._Messages.EMPTY)
        if len(rows) > _Limits.MAX_ROWS:
            raise RowsetValidationError(cls._Messages.TOO_MANY_ROWS)

        seen: set[str] = set()
        for row in rows:
            if row.row_key in seen:
                raise RowsetValidationError(cls._Messages.DUPLICATE_ROW_KEY)
            seen.add(row.row_key)
            if len(row.changes) > _Limits.MAX_CHANGES_PER_ROW:
                raise RowsetValidationError(cls._Messages.TOO_MANY_CHANGES)

        held_seen: set[str] = set()
        for hold in agent_holds:
            if hold.row_key not in seen:
                raise RowsetValidationError(cls._Messages.HOLD_UNKNOWN_ROW)
            if hold.row_key in held_seen:
                raise RowsetValidationError(cls._Messages.DUPLICATE_HOLD)
            held_seen.add(hold.row_key)


__all__ = [
    "AgentHold",
    "RowCounts",
    "RowFieldChange",
    "RowState",
    "RowStance",
    "RowsetValidationError",
    "RowsetValidator",
    "StagedRow",
]
