"""Row-set contracts + validator for bulk staged writes (PRD-D3).

A row-set is one staged write carrying N per-row changes, each individually
decidable. The agent proposes it (per row: a stable ``row_key``, a human
``title``, the EXACT connector-op args for that row, and old→new field diffs)
and may **pre-hold** risky rows with a visible reason that survives a user
override (FR-C7). The fold turns the ledger into per-row :class:`RowState` +
:class:`RowCounts`; the shared D2 dispatcher executes ONLY the approved rows.

Pure domain: these are ``RuntimeContract`` models + a total validator. Tool
input (rows, reasons, target args) is untrusted until :class:`RowsetValidator`
runs — validation failure is a typed domain error, and NO event is emitted
(the ledger records only what happened).

**The one invariant this module now enforces, for every staging lane.**

    The object the user approves must be the object that is sent.

``target_args`` is what dispatches and is server-only: ``StageRowView`` is
``extra="forbid"`` and deliberately omits it, and the client's ledger
projection reads the row's display half alone. So anything reaching
``target_args`` without a counterpart in what the user SEES is invisible at the
approval gate — which is how an entire model-authored email body once shipped
under a one-line ``cc`` diff, and how a four-field overwrite against a
different record shipped under a one-line ``priority`` diff.

:attr:`StagedRow.sends` closes that by construction: it is a server-computed,
ORDERED, TOTAL account of ``target_args``, keyed by the connector's own arg
name, and :class:`RowsetValidator` refuses any row where the two disagree.
``changes`` stops being an independently-constructed display artifact that a
future producer can desynchronise from the wire — it is now a strict subset
view, cross-checked value by value.

Every staging lane already passes through :meth:`RowsetValidator.validate`
(``WriteStager.stage_rowset``, ``runtime_worker/rowset_effect_staging.py``,
``runtime_worker/builtin_effect_executor.py``,
``runtime_adapters/rowset_effect_review.py``), so the accounting rule lives
there and nowhere else.

**Staging is the FIRST moment the rule runs, not the last.** The fold rebuilds
every row from an untrusted ledger payload (``StagedWriteFold._staged_row_of``),
and :meth:`RowsetValidator.sends_of` is deliberately all-or-nothing — a payload
whose ``sends`` key is absent or unparseable yields ``()``. So a row that was
staged before this contract existed, or one whose account did not survive the
round trip, comes back with NO account at all. Re-establishing it is therefore a
precondition of every later step that acts on the row, and
:meth:`RowsetValidator.first_unaccounted` is the one entry point those steps use:
``WriteStager.record_row_decision`` (a row may not be APPROVED unaccounted),
``WriteStager.apply_rows`` (an apply may not be authorized unaccounted), and
``RuntimeStageCommitHandler._handle_rowset`` (the last point before dispatch).
An unaccounted row **refuses the whole action** — it is never skipped so the
siblings can proceed, because a silently-shrunk batch means the action the user
took ("apply these N rows") quietly became a different one, and because refusing
loudly is what makes tampering visible. Holding a row stays available: a hold
only ever removes a row from the outbound set, so it is the remedy, not a risk.

**Migration is deliberately fail-closed.** A proposal persisted before this
change carries no ``sends``, so the validator refuses it and an in-flight staged
write becomes unapprovable. A write proposed under the old accounting should not
be approvable under the new one; that is the correct answer, not a bug.

**What ``old`` is, and what it is not.** ``StagedArg.old`` / ``RowFieldChange.old``
are the "Currently" column a reviewer reads. Nothing in this module verifies
them against the record: this validator is pure and total over ONE payload — it
holds no store, no surface snapshot and no connector handle, and neither
producer re-reads the record at this point (the agent lane's tool owns no MCP
client by construction, and the write-back lane composes ``old`` and ``new``
from the same submitted surface payload). So the ``old`` halves are checked for
AGREEMENT with each other, not for truth, and the per-arg :class:`ArgOrigin` is
what carries the provenance honestly — an ``old`` on a ``proposed`` arg is the
agent's claim about the record, and says so. The one ``old`` rule with real
teeth is A6: an arg labelled ``carried`` claims it is sending the record's own
value back unchanged, which is falsifiable against its own ``new``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from enum import StrEnum

from pydantic import Field

from agent_runtime.execution.contracts import JsonObject, JsonValue, RuntimeContract


class _Limits:
    """Caps for a proposed row-set (untrusted tool input — enforced fail-closed)."""

    MAX_ROWS = 200
    MAX_CHANGES_PER_ROW = 20
    #: Distinct args ONE row may send. Pinned to ``write_mapping._Limits.MAX_ARGS``
    #: — the widest answer the write-back lane can compose — so a row this
    #: validator accepts is a row that lane can also produce.
    MAX_SENDS_PER_ROW = 40
    REASON_MAX = 200
    TITLE_MAX = 200
    ROW_KEY_MAX = 200
    FIELD_MAX = 200


class ValueFingerprint:
    """Type-tagged canonical form of a JSON value, for exact identity tests.

    Plain ``==`` is the wrong test twice over: ``True == 1`` and ``1 == 1.0``
    both hold in Python, so a producer could show a boolean and send a count
    and the accounting would call them the same value. Tagging the type and
    canonicalising the JSON closes both, and makes nested containers comparable
    without a recursive walk at compare time.

    Lives here rather than in :mod:`..capabilities.surfaces.write_mapping`
    (where it began as ``_ValueFingerprint``) because ``surfaces_v2`` must not
    import ``capabilities``, so the move direction is forced. ONE definition —
    the value-identity rule the audit turns on cannot have a twin.
    """

    @classmethod
    def of(cls, value: object) -> str:
        return f"{type(value).__name__}:{cls._canonical(value)}"

    @staticmethod
    def _canonical(value: object) -> str:
        try:
            return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # pragma: no cover - default= covers these
            return repr(value)


class RowFieldChange(RuntimeContract):
    """One field's old→new diff for a row (``old``/``new`` = value as read/proposed)."""

    field: str = Field(min_length=1, max_length=_Limits.FIELD_MAX)
    old: JsonValue | None = None  # value before (as read; None = absent)
    new: JsonValue | None = None  # proposed value


class ArgOrigin(StrEnum):
    """Who authored the value one outbound arg carries.

    This is also the provenance of the arg's ``old`` half — the "Currently"
    value a reviewer reads — and it is the ONLY provenance signal on the wire,
    because no lane re-reads the record at validation time (see the module
    docstring). ``proposed`` means both halves are the agent's claim; a client
    that renders ``old`` as fact on a ``proposed`` arg is overstating it.
    """

    #: The user typed this value into a cell in THIS row.
    EDITED = "edited"
    #: Read from this record and sent back unchanged.
    CARRIED = "carried"
    #: The agent authored this value (agent-staged lane only).
    PROPOSED = "proposed"

    @property
    def claims_unchanged(self) -> bool:
        """Whether this origin asserts ``old`` and ``new`` are the same value.

        Only ``carried`` does — it means "read from this record and sent back
        unchanged", which A6 checks. The other two make no such claim, and no
        rule in this module can check their ``old`` against anything.
        """

        return self is ArgOrigin.CARRIED


class StagedArg(RuntimeContract):
    """One entry of a row's total account of what it will send.

    ``arg`` is the CONNECTOR's own name for the slot — the key in
    ``target_args`` — not the surface column it came from. That distinction is
    the point: a rename (``body`` displayed, ``bcc`` dispatched) is invisible in
    a column-keyed diff and is spelled out here.
    """

    arg: str = Field(min_length=1, max_length=_Limits.FIELD_MAX)
    origin: ArgOrigin
    #: The surface column / row field the value came from; ``None`` when none.
    column: str | None = Field(default=None, max_length=_Limits.FIELD_MAX)
    #: The value the producer says this field currently holds (``None`` =
    #: absent). NOT re-read from the record here — read :attr:`origin` for whose
    #: claim it is, and the module docstring for why no rule can verify it.
    old: JsonValue | None = None
    #: The value that will be sent — identical to ``target_args[arg]``.
    new: JsonValue | None = None


class StagedRow(RuntimeContract):
    """One proposed row change — the WYSIWYG unit a user approves/holds.

    ``target_args`` are the EXACT connector-op args the shared dispatcher sends
    for THIS row, verbatim (FR-C3). ``changes`` are display diffs only.
    ``sends`` is the ordered, total account of ``target_args`` — the half a
    human reads at the gate, complete by construction.

    ``sends`` defaults to ``()`` so no construction site breaks at import;
    :class:`RowsetValidator` is what refuses an unaccounted row, in one place,
    on every lane.
    """

    row_key: str = Field(min_length=1, max_length=_Limits.ROW_KEY_MAX)
    title: str = Field(min_length=1, max_length=_Limits.TITLE_MAX)
    target_args: JsonObject = Field(default_factory=dict)
    changes: tuple[RowFieldChange, ...] = ()
    sends: tuple[StagedArg, ...] = ()


class ProposedRow(RuntimeContract):
    """The row shape a MODEL may author — deliberately without ``sends``.

    ``RuntimeContract`` is ``extra="forbid"``, so a model that emits ``sends``
    gets a ``ValidationError`` rather than a hand-authored account of its own
    write. The account is derived server-side by
    :meth:`StagedRowAccounting.for_proposed`, from ``target_args``, in
    ``target_args`` key order.
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


class _Messages:
    """Safe public rejections. Constants only — shared by every rule below.

    One definition rather than one per class: the deriver and the validator
    refuse the SAME shapes at two different moments (proposal time and staging
    time), and two copies of *"the diff does not match what would be sent"* is
    exactly the drift this module exists to remove.
    """

    #: Every per-row accounting refusal ends with this clause, and
    #: :meth:`UnaccountedRow.message` strips it to re-tense the SAME sentence for
    #: the decision / dispatch boundary, where the row was already staged and
    #: what did not happen is the send. One definition, so the two cannot drift.
    NOTHING_STAGED = " Nothing was staged."
    NOTHING_SENT = " Nothing was sent."

    EMPTY = "A row-set must contain at least one row."
    TOO_MANY_ROWS = "The row-set exceeds the maximum row count."
    TOO_MANY_CHANGES = "A row exceeds the maximum number of field changes."
    DUPLICATE_ROW_KEY = "Row keys must be unique within a staged write."
    HOLD_UNKNOWN_ROW = "An agent hold references a row that is not in the set."
    DUPLICATE_HOLD = "Each row may be pre-held at most once."
    DUPLICATE_FIELD = (
        "A row lists the same field twice, so only one of the two values would "
        "be sent." + NOTHING_STAGED
    )
    NO_TARGET_ARGS = "A staged row would send no field at all." + NOTHING_STAGED
    TOO_MANY_SENDS = "A row exceeds the maximum number of outbound fields."
    UNACCOUNTED_ARGS = (
        "A staged row does not disclose every field it would send, so what you "
        "would approve and what would be sent are not the same write." + NOTHING_STAGED
    )
    ARG_VALUE_MISMATCH = (
        "A staged row would send a value different from the one it discloses."
        + NOTHING_STAGED
    )
    UNSHOWN_CHANGE = (
        "A staged row lists a change for a field it would not send." + NOTHING_STAGED
    )
    CHANGE_VALUE_MISMATCH = (
        "A staged row's diff does not match the field it would send." + NOTHING_STAGED
    )
    CHANGE_OLD_MISMATCH = (
        "A staged row's diff and its account disagree about the value a field "
        "currently holds, so the change on screen is not the size of the change "
        "that would be made." + NOTHING_STAGED
    )
    CARRIED_ARG_CHANGED = (
        "A staged row shows a field as carried through unchanged but would send "
        "a value different from the one it read." + NOTHING_STAGED
    )
    ROW_CONTENT_MISSING = (
        "A decided row has no staged content left, so what it would send cannot "
        "be established at all." + NOTHING_STAGED
    )
    #: The frame :meth:`UnaccountedRow.message` composes. It names the row and
    #: carries the rule's own sentence, so the reader is told which row and why.
    ROW_REFUSAL = 'Row "{row_key}" cannot be applied. {clause}' + NOTHING_SENT


class RowsetValidationError(Exception):
    """A proposed row-set is malformed / over caps. Carries only a safe message."""

    code: str = "rowset_invalid"
    safe_message: str = "The proposed row-set is invalid."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.safe_message)
        if message is not None:
            self.safe_message = message


class UnaccountedRow(RuntimeContract):
    """The first row of a decided set whose account could not be re-established.

    Returned rather than raised because the three call sites need it in three
    shapes: the stager turns it into a typed 409, and the commit handler turns
    it into a ``write.applied{failed}`` the user can read. Both go through
    :meth:`message`, so a reviewer is told WHICH row and WHY in one sentence.
    """

    row_key: str = Field(min_length=1, max_length=_Limits.ROW_KEY_MAX)
    #: The validator's own safe message for the rule this row failed.
    reason: str = Field(min_length=1)

    @classmethod
    def for_missing_content(cls, row_key: str) -> UnaccountedRow:
        """A decided row whose staged content is gone from the folded stage."""

        return cls(
            row_key=row_key[: _Limits.ROW_KEY_MAX],
            reason=_Messages.ROW_CONTENT_MISSING,
        )

    def message(self) -> str:
        """The safe public sentence: which row, why, and that nothing was sent."""

        return _Messages.ROW_REFUSAL.format(
            row_key=self.row_key,
            clause=self.reason.removesuffix(_Messages.NOTHING_STAGED),
        )


class RowSendPlan(RuntimeContract):
    """What one decided set of row keys is allowed to touch — rows, or a refusal.

    Exactly one half is populated. ``rows`` carries the staged content for
    EVERY named key, in the order they were named, and only when every one of
    them still proves what it would send; otherwise ``refusal`` names the first
    that does not and ``rows`` is empty. There is no third state, which is the
    point: a caller cannot end up iterating a silently-shortened batch.
    """

    rows: tuple[StagedRow, ...] = ()
    refusal: UnaccountedRow | None = None


class StagedRowAccounting:
    """Derive a row's outbound account SERVER-SIDE, never from model output.

    The agent lane's tool takes a :class:`ProposedRow` — ``row_key`` / ``title``
    / ``target_args`` / ``changes`` — and this turns it into a
    :class:`StagedRow` whose ``sends`` is a total account of ``target_args``, in
    ``target_args`` key order. Every value comes from ``target_args`` itself, so
    nothing here is a channel for a value the model did not already put on the
    wire; what it adds is that the wire is now fully DISPLAYED.

    Two refusals, both of which used to be silent drift between the diff and the
    request:

    * a ``changes`` entry naming a field no arg carries — the diff describes a
      write the wire does not make;
    * a ``changes`` entry whose ``new`` is not the value the matching arg
      carries — the diff shows one value and the wire sends another.
    """

    @classmethod
    def for_proposed(cls, row: ProposedRow) -> StagedRow:
        """Return the fully-accounted :class:`StagedRow`, or raise."""

        by_field = {change.field: change for change in row.changes}
        unknown = set(by_field) - set(row.target_args)
        if unknown:
            raise RowsetValidationError(_Messages.UNSHOWN_CHANGE)
        sends: list[StagedArg] = []
        for arg, value in row.target_args.items():
            change = by_field.get(arg)
            if change is not None and ValueFingerprint.of(
                change.new
            ) != ValueFingerprint.of(value):
                raise RowsetValidationError(_Messages.CHANGE_VALUE_MISMATCH)
            sends.append(
                StagedArg(
                    arg=arg,
                    origin=ArgOrigin.PROPOSED,
                    column=change.field if change is not None else None,
                    # The model's claim about the record, copied — this lane
                    # reads nothing, so ``origin=proposed`` is the whole
                    # provenance of this value and the only honest label for it.
                    old=change.old if change is not None else None,
                    new=value,
                )
            )
        return StagedRow(
            row_key=row.row_key,
            title=row.title,
            target_args=dict(row.target_args),
            changes=row.changes,
            sends=tuple(sends),
        )


class RowsetValidator:
    """Validate a proposed ``(rows, agent_holds)`` fail-closed (pure, total).

    Enforces the caps, unique ``row_key`` within the stage, that every
    ``agent_holds.row_key`` references an actual row, and — the rule this
    module exists for — that each row's ``sends`` is a complete, faithful
    account of its ``target_args``:

    * **A1 ordered bijection** — ``[s.arg for s in sends]`` equals
      ``list(target_args)``: same names, same order, no duplicates. Ordered so
      the ledger's canonical bytes and its digest stay stable.
    * **A2 value identity** — every ``s.new`` fingerprints identically to
      ``target_args[s.arg]``. Type-tagged, so ``True`` never passes for ``1``.
    * **A3 no unshown change** — every ``changes`` field is an arg the row
      sends and its ``new`` is the value that arg carries. A ``changes`` entry
      naming a field no arg carries is a REFUSAL, never a silently dropped
      display line.
    * **A4 uniqueness** — ``changes`` is field-unique and ``sends`` is
      arg-unique. A repeated field renders twice and sends once.
    * **A5 non-vacuous** — ``target_args`` is non-empty and within
      :attr:`_Limits.MAX_SENDS_PER_ROW`.
    * **A6 carried means unchanged** — an arg whose ``origin`` claims it is
      sending the record's own value back untouched must carry the same value
      in ``old`` and ``new``. This is the one rule about ``old`` that is
      falsifiable here, and it closes a real disguise: label the hostile arg
      ``carried`` and a UI that renders carried args as inert hides an
      overwrite in plain sight.
    * **A7 the two halves agree about ``old``** — a row's ``changes`` and its
      ``sends`` must report the same prior value for a field. This is an
      INTEGRITY check between two independently-parsed halves of one payload
      (which is exactly what the fold produces on replay), **not** verification
      of ``old`` against the record: no lane re-reads the record here, and on
      the agent lane :meth:`StagedRowAccounting.for_proposed` copies one half
      from the other, so A7 cannot fail on a freshly-derived agent row by
      construction. It fails on a hand-composed row and on a tampered ledger
      payload, which are the shapes it exists for. See the module docstring for
      why no stronger claim about ``old`` is available at this layer.

    Raises :class:`RowsetValidationError` with a safe message on any violation —
    the caller emits NO ledger event on failure.
    """

    @classmethod
    def validate(
        cls,
        *,
        rows: tuple[StagedRow, ...],
        agent_holds: tuple[AgentHold, ...],
    ) -> None:
        if not rows:
            raise RowsetValidationError(_Messages.EMPTY)
        if len(rows) > _Limits.MAX_ROWS:
            raise RowsetValidationError(_Messages.TOO_MANY_ROWS)

        seen: set[str] = set()
        for row in rows:
            if row.row_key in seen:
                raise RowsetValidationError(_Messages.DUPLICATE_ROW_KEY)
            seen.add(row.row_key)
            if len(row.changes) > _Limits.MAX_CHANGES_PER_ROW:
                raise RowsetValidationError(_Messages.TOO_MANY_CHANGES)
            cls._assert_accounted(row)

        held_seen: set[str] = set()
        for hold in agent_holds:
            if hold.row_key not in seen:
                raise RowsetValidationError(_Messages.HOLD_UNKNOWN_ROW)
            if hold.row_key in held_seen:
                raise RowsetValidationError(_Messages.DUPLICATE_HOLD)
            held_seen.add(hold.row_key)

    @classmethod
    def assert_accounted(cls, row: StagedRow) -> None:
        """Re-establish ONE already-staged row's account, or raise.

        The public entry point for the later moments — a decision, an apply
        authorization, the last step before dispatch — which each hold one row
        rebuilt from the ledger rather than a whole proposed set. Same rules,
        same messages, one definition.
        """

        cls._assert_accounted(row)

    @classmethod
    def first_unaccounted(cls, rows: Sequence[StagedRow]) -> UnaccountedRow | None:
        """Return the FIRST row that cannot prove what it would send, else ``None``.

        Total, in row order, and it stops at the first failure: the caller
        refuses the whole action anyway, and naming one row and one reason is
        what a reviewer can act on.
        """

        for row in rows:
            try:
                cls._assert_accounted(row)
            except RowsetValidationError as exc:
                return UnaccountedRow(row_key=row.row_key, reason=exc.safe_message)
        return None

    @classmethod
    def _assert_accounted(cls, row: StagedRow) -> None:
        """Refuse a row whose displayed account is not its outbound args."""

        if not row.target_args:  # A5
            raise RowsetValidationError(_Messages.NO_TARGET_ARGS)
        if len(row.sends) > _Limits.MAX_SENDS_PER_ROW:  # A5
            raise RowsetValidationError(_Messages.TOO_MANY_SENDS)
        if [item.arg for item in row.sends] != list(row.target_args):  # A1 + A4
            raise RowsetValidationError(_Messages.UNACCOUNTED_ARGS)
        for item in row.sends:
            if ValueFingerprint.of(item.new) != ValueFingerprint.of(  # A2
                row.target_args[item.arg]
            ):
                raise RowsetValidationError(_Messages.ARG_VALUE_MISMATCH)
            if item.origin.claims_unchanged and ValueFingerprint.of(  # A6
                item.old
            ) != ValueFingerprint.of(item.new):
                raise RowsetValidationError(_Messages.CARRIED_ARG_CHANGED)
        cls._assert_changes_shown(row)

    @classmethod
    def _assert_changes_shown(cls, row: StagedRow) -> None:
        """A3 + A4 + A7 over ``changes``: field-unique, a checked subset of ``sends``."""

        by_arg = {item.arg: item for item in row.sends}
        fields = {change.field for change in row.changes}
        if len(fields) != len(row.changes):  # A4
            raise RowsetValidationError(_Messages.DUPLICATE_FIELD)
        for change in row.changes:
            sent = by_arg.get(change.field)
            if sent is None:  # A3
                raise RowsetValidationError(_Messages.UNSHOWN_CHANGE)
            if ValueFingerprint.of(change.new) != ValueFingerprint.of(  # A3
                row.target_args[change.field]
            ):
                raise RowsetValidationError(_Messages.CHANGE_VALUE_MISMATCH)
            # A7 — the two halves must AGREE about ``old``. Read as integrity,
            # never as verification: see the rule's entry in the class docstring.
            if ValueFingerprint.of(change.old) != ValueFingerprint.of(sent.old):
                raise RowsetValidationError(_Messages.CHANGE_OLD_MISMATCH)

    @classmethod
    def sends_of(cls, raw: object) -> tuple[StagedArg, ...]:
        """Rebuild ``sends`` from an untrusted ledger payload, dropping nothing.

        Total: a member that does not parse yields an EMPTY account rather than
        a partial one, so a truncated payload refuses at
        :meth:`validate` instead of quietly under-disclosing.
        """

        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            return ()
        parsed: list[StagedArg] = []
        for item in raw:
            if not isinstance(item, Mapping):
                return ()
            try:
                parsed.append(StagedArg.model_validate(dict(item)))
            except Exception:  # noqa: BLE001 - an unparseable account is no account
                return ()
        return tuple(parsed)


__all__ = [
    "AgentHold",
    "ArgOrigin",
    "ProposedRow",
    "RowCounts",
    "RowFieldChange",
    "RowSendPlan",
    "RowState",
    "RowStance",
    "RowsetValidationError",
    "RowsetValidator",
    "StagedArg",
    "StagedRow",
    "StagedRowAccounting",
    "UnaccountedRow",
    "ValueFingerprint",
]
