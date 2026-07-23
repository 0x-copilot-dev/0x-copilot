"""WriteStager + StagedWriteFold — the single-artifact staged-write engine (PRD-D1).

A write proposal (a draft the agent authored, then a user optionally edits)
becomes a **staged surface** with numbered revisions and typed decisions, all
recorded on the run's append-only Work Ledger as ``write.staged`` /
``revision.added`` / ``decision.recorded`` events. State is a **pure fold** of
those events (:class:`StagedWriteFold`) — no new table, rebuildable on replay
(SDR §6). Revision *content* lives in the existing draft rows (``DraftStorePort``);
each ``revision.added`` carries a ``proposal_ref`` naming that row and inline
``authorship_spans`` the server computed by diffing the user's whole-body edit
against the previous revision.

**Fail-closed core (SDR §10, the D1 DoD):** this engine NEVER executes anything.
It emits exactly the three event types above; ``write.applied`` is PRD-D2's
CommitEngine's sole output. A ``decision.recorded{approve}`` here records intent
and nothing more — the draft's status is untouched, no MCP client is called,
nothing sends. Every 4xx path emits **no** ledger event (the ledger records only
what happened).

Layering: this module is pure domain. It never imports ``runtime_api`` — emission
+ event reads ride an injected :class:`StageLedgerPort` (a duck-typed adapter the
API layer builds over ``RuntimeEventProducer``), mirroring how ``GateLedger``
keeps the gate's payload logic out of the transport layer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import uuid4

from pydantic import PositiveInt

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.persistence.ports import DraftStorePort, OptimisticConflict
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from agent_runtime.surfaces_v2.constants import Keys, Messages, Titles, Values
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from agent_runtime.surfaces_v2.revision_diff import AuthorshipSpan, RevisionDiffer
from agent_runtime.surfaces_v2.rowset import (
    AgentHold,
    RowCounts,
    RowFieldChange,
    RowState,
    RowStance,
    RowsetValidationError,
    RowsetValidator,
    StagedRow,
)


# ---------------------------------------------------------------------------
# Ports (structural — the API layer supplies concrete adapters)
# ---------------------------------------------------------------------------


@runtime_checkable
class _LedgerEventLike(Protocol):
    """Envelope-lite shape the fold + stager read (never imports the transport)."""

    event_type: object
    sequence_no: int
    payload: Mapping[str, object]


@runtime_checkable
class StageLedgerPort(Protocol):
    """Emit a v2 ledger event / list a run's events, without a transport import.

    The concrete adapter (``agent_runtime.api.stage_ledger.RuntimeStageLedger``)
    maps the raw ``LedgerEventType`` string to the transport enum + appends via
    ``RuntimeEventProducer`` (whose projector re-filters the payload), and reads
    events via ``EventStorePort.list_events_after``. ``run`` is the opaque
    ``RunRecord`` the append needs; the stager never inspects it.
    """

    async def emit(
        self,
        *,
        run: object,
        event_type_value: str,
        payload: Mapping[str, object],
        summary: str | None,
    ) -> _LedgerEventLike:
        """Append one event; return the persisted envelope (post-projection)."""

    async def list_events(
        self, *, org_id: str, run_id: str
    ) -> Sequence[_LedgerEventLike]:
        """Return every persisted event for a run, ascending by ``sequence_no``."""


@runtime_checkable
class StageCommitQueuePort(Protocol):
    """Enqueue a durable stage-commit command (PRD-D2), without a transport import.

    The concrete adapter (``agent_runtime.api.stage_commit_queue``) builds the
    ``RuntimeStageCommitCommand`` + trace-propagation carrier and appends it via
    ``RuntimeQueuePort.enqueue_stage_commit``. The stager calls this with
    primitives only, so ``surfaces_v2.staging`` stays free of ``runtime_api``.
    An approve fires exactly one enqueue; nothing else ever enqueues.
    """

    async def enqueue_stage_commit(
        self,
        *,
        stage_id: str,
        run_id: str,
        org_id: str,
        user_id: str,
        conversation_id: str,
        rev: int,
        decision_seq: int,
        row_keys: tuple[str, ...] | None = None,
    ) -> None:
        """Enqueue the commit command for one approved ``(stage_id, rev)``.

        ``row_keys`` is ``None`` for a single-artifact (D1) commit and the exact
        approved row set for a row-set (D3) apply — the worker gate re-checks it.
        """


@runtime_checkable
class WritePolicyResolverPort(Protocol):
    """Resolve whether an allow-always policy auto-applies a ``(connector, op)``.

    Injected additively (PRD-D3, FR-C8). The adapter composes PRD-C1's
    ``EffectiveActionPolicyResolver`` + classifier and returns ``bypass``. The
    stager calls it once at the end of ``stage_rowset``; ``None`` (unwired) ⇒
    nothing auto-applies (fail-closed to ask-first). ``agent_holds`` are never
    passed here — there is no code path from a pre-held row to auto-approval.
    """

    def bypass_for(self, *, connector: str, op: str) -> bool:
        """Return ``True`` iff an allow-always override auto-applies this op."""


# ---------------------------------------------------------------------------
# Typed domain errors (fail-closed; routes map ``.code`` → HTTP status)
# ---------------------------------------------------------------------------


class StagedWriteError(Exception):
    """Base for staged-write domain failures. Carries only a safe public message."""

    code: str = "staged_write_error"
    safe_message: str = "The staged write could not be processed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.safe_message)
        if message is not None:
            self.safe_message = message


class StageNotFound(StagedWriteError):
    code = "stage_not_found"
    safe_message = "No staged write was found for this scope."


class StageForbidden(StagedWriteError):
    code = "stage_forbidden"
    safe_message = "You cannot decide on this staged write."


class StaleRevision(StagedWriteError):
    code = "stale_revision"
    safe_message = "The draft changed; review the latest revision before approving."


class StageFrozen(StagedWriteError):
    code = "stage_frozen"
    safe_message = "This staged write is already decided and cannot change."


class EditConflict(StagedWriteError):
    code = "edit_conflict"
    safe_message = "The draft changed while you were editing; refresh and retry."


class UnsupportedDecision(StagedWriteError):
    code = "unsupported_decision"
    safe_message = "That decision is not available for a single-artifact write."


class MalformedDecision(StagedWriteError):
    code = "malformed_decision"
    safe_message = "This decision requires the revision you are deciding on."


class UnknownRowKey(StagedWriteError):
    """A decision / apply referenced a row that is not in the staged set (404)."""

    code = "unknown_row_key"
    safe_message = "One or more rows are not part of this staged write."


class ApplySetMismatch(StagedWriteError):
    """The applied set does not equal the current will-apply set (409, WYSIWYG)."""

    code = "apply_set_mismatch"
    safe_message = "The rows changed; review again before applying."


class InvalidRowset(StagedWriteError):
    """A proposed row-set is malformed / over caps (422; no event emitted)."""

    code = "rowset_invalid"
    safe_message = "The proposed row-set is invalid."


# ---------------------------------------------------------------------------
# Fold output contracts
# ---------------------------------------------------------------------------


class StagedWriteStatus(StrEnum):
    STAGED = "staged"
    REJECTED = "rejected"
    APPROVED = "approved"
    # PRD-D2: terminal — the CommitEngine sent exactly the approved rev.
    APPLIED = "applied"
    # PRD-D3: apply decided (frozen), ``write.applied`` not yet folded.
    APPLY_PENDING = "apply_pending"
    # PRD-D3: some approved rows failed mid-apply (terminal in D3).
    PARTIALLY_APPLIED = "partially_applied"
    # PRD-D2 defensive: a ``write.applied`` folded onto a stage that was not in a
    # matching APPROVED state (unreachable absent a bug — D1 freezes approved
    # stages and the handler's approval gate refuses stale commands). Tests
    # assert this is unreachable on every legitimate sequence.
    CORRUPT = "corrupt"


class RevisionSummary(RuntimeContract):
    """One folded revision: its number, author, snapshot ref, spans, seq."""

    rev: PositiveInt
    author: str
    proposal_ref: str
    diff_ref: str
    authorship_spans: tuple[AuthorshipSpan, ...]
    sequence_no: int


class DecisionSummary(RuntimeContract):
    """One folded decision: kind, rev it scoped, actor, seq.

    PRD-D3 adds the row scope (``scope_row_keys``) and the ``apply`` flag: an
    apply-scoped approve (``apply=True``) authorizes exactly that row set to
    execute; a plain row approve/hold (``apply=False``) is a stance toggle only.
    """

    decision: str
    scope_rev: PositiveInt | None
    actor: str
    sequence_no: int
    scope_row_keys: tuple[str, ...] = ()
    apply: bool = False


class StagedWriteState(RuntimeContract):
    """Pure fold output for one stage. Rebuildable from the run's ledger."""

    stage_id: str
    surface_id: str
    draft_id: str
    target_connector: str
    target_op: str
    latest_rev: int
    approved_rev: PositiveInt | None
    status: StagedWriteStatus
    revisions: tuple[RevisionSummary, ...]
    decisions: tuple[DecisionSummary, ...]
    first_sequence_no: int
    last_sequence_no: int
    # PRD-D2 — the last ``write.applied`` outcome folded onto this stage, or
    # ``None`` until the CommitEngine reports one. ``apply_result`` is
    # ``"applied"`` (⇒ APPLIED terminal) or ``"failed"`` (⇒ held, approval
    # consumed); ``apply_failure_code`` names the refusal on a failed apply.
    apply_result: str | None = None
    apply_failure_code: str | None = None
    # PRD-D3 — row-set stages. ``rows`` / ``row_counts`` are ``None`` for a
    # single-artifact (D1) stage. ``staged_rows`` is the DOMAIN-only content
    # (title / target_args / changes) the worker handler dispatches from and the
    # wire view renders; it never carries onto the D1 wire projection.
    rows: tuple[RowState, ...] | None = None
    row_counts: RowCounts | None = None
    staged_rows: tuple[StagedRow, ...] | None = None

    def is_rowset(self) -> bool:
        """Whether this stage is a bulk row-set (vs a single-artifact draft)."""

        return self.rows is not None

    def will_apply_keys(self) -> tuple[str, ...]:
        """Row keys whose current stance is ``WILL_APPLY`` (empty for D1 stages)."""

        if self.rows is None:
            return ()
        return tuple(
            row.row_key for row in self.rows if row.stance is RowStance.WILL_APPLY
        )

    def staged_row(self, row_key: str) -> StagedRow | None:
        """Return the staged row content for ``row_key`` (or ``None``)."""

        for row in self.staged_rows or ():
            if row.row_key == row_key:
                return row
        return None

    def latest_revision(self) -> RevisionSummary | None:
        """Return the highest-``rev`` revision summary, or ``None`` when empty."""

        if not self.revisions:
            return None
        return max(self.revisions, key=lambda revision: revision.rev)


# ---------------------------------------------------------------------------
# Draft ref codec — ``draft://<draft_id>/v<version>`` and diff variant
# ---------------------------------------------------------------------------


class DraftRef:
    """Builds/parses the ``draft://`` refs a stage's snapshots + diffs carry."""

    SCHEME = "draft://"

    @classmethod
    def proposal(cls, *, draft_id: str, version: int) -> str:
        return f"{cls.SCHEME}{draft_id}/v{version}"

    @classmethod
    def diff(cls, *, draft_id: str, from_version: int, to_version: int) -> str:
        return f"{cls.SCHEME}{draft_id}/v{from_version}..v{to_version}"

    @classmethod
    def parse_proposal(cls, ref: object) -> tuple[str, int] | None:
        """Return ``(draft_id, version)`` for a proposal ref, or ``None``."""

        if not isinstance(ref, str) or not ref.startswith(cls.SCHEME):
            return None
        body = ref[len(cls.SCHEME) :]
        slash = body.rfind("/v")
        if slash < 0:
            return None
        draft_id = body[:slash]
        version_text = body[slash + 2 :]
        if not draft_id or not version_text.isdigit():
            return None
        return draft_id, int(version_text)


class StageRef:
    """Builds/parses the ``stage://<stage_id>/v<rev>`` refs a row-set rev carries.

    Mirrors :class:`DraftRef` but for row-sets, which have no draft row — the
    logical address of a rev's inline rowset, resolvable by folding the ledger.
    """

    SCHEME = "stage://"

    @classmethod
    def proposal(cls, *, stage_id: str, rev: int) -> str:
        return f"{cls.SCHEME}{stage_id}/v{rev}"


# ---------------------------------------------------------------------------
# Pure fold
# ---------------------------------------------------------------------------


@dataclass
class _StageAccumulator:
    stage_id: str
    surface_id: str
    draft_id: str
    target_connector: str
    target_op: str
    first_sequence_no: int
    last_sequence_no: int
    latest_rev: int = 0
    approved_rev: int | None = None
    status: StagedWriteStatus = StagedWriteStatus.STAGED
    revisions: list[RevisionSummary] = field(default_factory=list)
    decisions: list[DecisionSummary] = field(default_factory=list)
    apply_result: str | None = None
    apply_failure_code: str | None = None
    # PRD-D3 row-set fold state. ``is_rowset`` is set by ``write.staged.rows``;
    # ``staged_rows`` is populated by ``revision.added.rowset`` (in row order).
    is_rowset: bool = False
    row_order: list[str] = field(default_factory=list)
    staged_rows: dict[str, StagedRow] = field(default_factory=dict)
    agent_hold_reasons: dict[str, str] = field(default_factory=dict)
    row_stances: dict[str, RowStance] = field(default_factory=dict)
    row_decided_by: dict[str, str] = field(default_factory=dict)
    row_apply_outcomes: dict[str, str] = field(default_factory=dict)

    def to_state(self) -> StagedWriteState:
        rows: tuple[RowState, ...] | None = None
        row_counts: RowCounts | None = None
        staged_rows: tuple[StagedRow, ...] | None = None
        if self.is_rowset:
            rows = tuple(self._row_state(key) for key in self.row_order)
            row_counts = self._counts(rows)
            staged_rows = tuple(
                self.staged_rows[key]
                for key in self.row_order
                if key in self.staged_rows
            )
        return StagedWriteState(
            stage_id=self.stage_id,
            surface_id=self.surface_id,
            draft_id=self.draft_id,
            target_connector=self.target_connector,
            target_op=self.target_op,
            latest_rev=self.latest_rev,
            approved_rev=self.approved_rev,
            status=self.status,
            revisions=tuple(self.revisions),
            decisions=tuple(self.decisions),
            first_sequence_no=self.first_sequence_no,
            last_sequence_no=self.last_sequence_no,
            apply_result=self.apply_result,
            apply_failure_code=self.apply_failure_code,
            rows=rows,
            row_counts=row_counts,
            staged_rows=staged_rows,
        )

    def _row_state(self, row_key: str) -> RowState:
        stance = self.row_stances.get(row_key, RowStance.WILL_APPLY)
        return RowState(
            row_key=row_key,
            stance=stance,
            agent_hold_reason=self.agent_hold_reasons.get(row_key),
            decided_by=self.row_decided_by.get(row_key),
            apply_outcome=self.row_apply_outcomes.get(row_key),
        )

    @staticmethod
    def _counts(rows: tuple[RowState, ...]) -> RowCounts:
        return RowCounts(
            total=len(rows),
            will_apply=sum(1 for r in rows if r.stance is RowStance.WILL_APPLY),
            held=sum(1 for r in rows if r.stance is RowStance.HELD),
            applied=sum(
                1 for r in rows if r.apply_outcome == Values.ROW_OUTCOME_APPLIED
            ),
            failed=sum(1 for r in rows if r.apply_outcome == Values.ROW_OUTCOME_FAILED),
        )


class StagedWriteFold:
    """Pure fold from a run's ledger events to per-stage :class:`StagedWriteState`."""

    @classmethod
    def fold(cls, events: Sequence[_LedgerEventLike]) -> dict[str, StagedWriteState]:
        """Fold typed envelopes (or any object with the three fields)."""

        return cls.fold_raw(
            {
                _RawKey.EVENT_TYPE: cls._event_type_value(event.event_type),
                _RawKey.SEQUENCE_NO: event.sequence_no,
                _RawKey.PAYLOAD: event.payload,
            }
            for event in events
        )

    @classmethod
    def fold_raw(
        cls, events: "Sequence[Mapping[str, object]] | object"
    ) -> dict[str, StagedWriteState]:
        """Fold plain ``{event_type, sequence_no, payload}`` dicts.

        Deterministic + total: events are processed in ``sequence_no`` order;
        ``write.staged`` opens a stage; ``revision.added`` / ``decision.recorded``
        / ``write.applied`` mutate the matching stage (ignored if the stage is
        unseen); every other event type — present or future — is skipped.
        """

        ordered = sorted(events, key=cls._seq_of)  # type: ignore[arg-type]
        stages: dict[str, _StageAccumulator] = {}
        for event in ordered:
            event_type = str(event.get(_RawKey.EVENT_TYPE, ""))
            payload = event.get(_RawKey.PAYLOAD)
            payload = payload if isinstance(payload, Mapping) else {}
            seq = cls._seq_of(event)
            if event_type == LedgerEventType.WRITE_STAGED.value:
                cls._apply_write_staged(stages, seq=seq, payload=payload)
            elif event_type == LedgerEventType.REVISION_ADDED.value:
                cls._apply_revision_added(stages, seq=seq, payload=payload)
            elif event_type == LedgerEventType.DECISION_RECORDED.value:
                cls._apply_decision_recorded(stages, seq=seq, payload=payload)
            elif event_type == LedgerEventType.WRITE_APPLIED.value:
                cls._apply_write_applied(stages, seq=seq, payload=payload)
        return {
            stage_id: accumulator.to_state() for stage_id, accumulator in stages.items()
        }

    # -- reducers -----------------------------------------------------------

    @classmethod
    def _apply_write_staged(
        cls,
        stages: dict[str, _StageAccumulator],
        *,
        seq: int,
        payload: Mapping[str, object],
    ) -> None:
        stage_id = cls._str_or(payload.get(Keys.Field.STAGE_ID), "")
        surface_id = cls._str_or(payload.get(Keys.Field.SURFACE_ID), "")
        if not stage_id or stage_id in stages:
            return
        target = payload.get(Keys.Field.TARGET)
        target = target if isinstance(target, Mapping) else {}
        parsed = DraftRef.parse_proposal(payload.get(Keys.Field.PROPOSAL_REF))
        draft_id = parsed[0] if parsed is not None else ""
        accumulator = _StageAccumulator(
            stage_id=stage_id,
            surface_id=surface_id,
            draft_id=draft_id,
            target_connector=cls._str_or(target.get(Keys.Field.CONNECTOR), ""),
            target_op=cls._str_or(target.get(Keys.Field.OP), ""),
            first_sequence_no=seq,
            last_sequence_no=seq,
        )
        # PRD-D3 — a ``rows`` count marks this a row-set stage; ``agent_holds``
        # seed the sticky per-row pre-hold reasons (decided_by ``agent``). The
        # full row content arrives with the rev-1 ``revision.added.rowset``.
        rows_count = payload.get(Keys.Field.ROWS)
        if isinstance(rows_count, int) and not isinstance(rows_count, bool):
            accumulator.is_rowset = True
        holds = payload.get(Keys.Field.AGENT_HOLDS)
        if isinstance(holds, Sequence) and not isinstance(holds, (str, bytes)):
            for raw in holds:
                if not isinstance(raw, Mapping):
                    continue
                row_key = cls._str_or(raw.get(Keys.Field.ROW_KEY), "")
                reason = cls._str_or(raw.get(Keys.Field.REASON), "")
                if not row_key:
                    continue
                accumulator.is_rowset = True
                accumulator.agent_hold_reasons[row_key] = reason
                accumulator.row_stances[row_key] = RowStance.HELD
                accumulator.row_decided_by[row_key] = Values.AUTHOR_AGENT
        stages[stage_id] = accumulator

    @classmethod
    def _apply_revision_added(
        cls,
        stages: dict[str, _StageAccumulator],
        *,
        seq: int,
        payload: Mapping[str, object],
    ) -> None:
        stage_id = cls._str_or(payload.get(Keys.Field.STAGE_ID), "")
        accumulator = stages.get(stage_id)
        if accumulator is None:
            return
        rev = payload.get(Keys.Field.REV)
        if not isinstance(rev, int) or isinstance(rev, bool) or rev < 1:
            return
        accumulator.revisions.append(
            RevisionSummary(
                rev=rev,
                author=cls._str_or(payload.get(Keys.Field.AUTHOR), ""),
                proposal_ref=cls._str_or(payload.get(Keys.Field.PROPOSAL_REF), ""),
                diff_ref=cls._str_or(payload.get(Keys.Field.DIFF_REF), ""),
                authorship_spans=cls._spans_of(
                    payload.get(Keys.Field.AUTHORSHIP_SPANS)
                ),
                sequence_no=seq,
            )
        )
        accumulator.latest_rev = max(accumulator.latest_rev, rev)
        accumulator.last_sequence_no = max(accumulator.last_sequence_no, seq)
        # PRD-D3 — hydrate the inline row-set (full row content). Rows keep their
        # authored order; a row named in ``agent_holds`` stays HELD, every other
        # row defaults to WILL_APPLY (no explicit decision yet).
        rowset = payload.get(Keys.Field.ROWSET)
        if isinstance(rowset, Mapping):
            accumulator.is_rowset = True
            cls._hydrate_rowset(accumulator, rowset.get(Keys.Field.ROWS))

    @classmethod
    def _hydrate_rowset(cls, accumulator: _StageAccumulator, raw_rows: object) -> None:
        if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
            return
        for raw in raw_rows:
            row = cls._staged_row_of(raw)
            if row is None or row.row_key in accumulator.staged_rows:
                continue
            accumulator.staged_rows[row.row_key] = row
            accumulator.row_order.append(row.row_key)
            if row.row_key not in accumulator.row_stances:
                accumulator.row_stances[row.row_key] = RowStance.WILL_APPLY

    @staticmethod
    def _staged_row_of(raw: object) -> StagedRow | None:
        if not isinstance(raw, Mapping):
            return None
        row_key = raw.get(Keys.Field.ROW_KEY)
        title = raw.get(Keys.Field.TITLE)
        if not isinstance(row_key, str) or not row_key:
            return None
        if not isinstance(title, str) or not title:
            return None
        target_args = raw.get(Keys.Field.TARGET_ARGS)
        target_args = dict(target_args) if isinstance(target_args, Mapping) else {}
        changes_raw = raw.get(Keys.Field.CHANGES)
        changes: list[RowFieldChange] = []
        if isinstance(changes_raw, Sequence) and not isinstance(
            changes_raw, (str, bytes)
        ):
            for change in changes_raw:
                if not isinstance(change, Mapping):
                    continue
                field_name = change.get(Keys.Field.FIELD)
                if not isinstance(field_name, str) or not field_name:
                    continue
                changes.append(
                    RowFieldChange(
                        field=field_name,
                        old=change.get(Keys.Field.OLD),
                        new=change.get(Keys.Field.NEW),
                    )
                )
        try:
            return StagedRow(
                row_key=row_key,
                title=title,
                target_args=target_args,
                changes=tuple(changes),
            )
        except Exception:  # noqa: BLE001 — a malformed row is skipped, never fatal.
            return None

    @classmethod
    def _apply_decision_recorded(
        cls,
        stages: dict[str, _StageAccumulator],
        *,
        seq: int,
        payload: Mapping[str, object],
    ) -> None:
        stage_id = cls._str_or(payload.get(Keys.Field.STAGE_ID), "")
        accumulator = stages.get(stage_id)
        if accumulator is None:
            return
        decision = cls._str_or(payload.get(Keys.Field.DECISION), "")
        actor = cls._str_or(payload.get(Keys.Field.ACTOR), "")
        scope = payload.get(Keys.Field.SCOPE)
        scope = scope if isinstance(scope, Mapping) else {}
        scope_rev_raw = scope.get(Keys.Field.REV)
        scope_rev = (
            scope_rev_raw
            if isinstance(scope_rev_raw, int)
            and not isinstance(scope_rev_raw, bool)
            and scope_rev_raw >= 1
            else None
        )
        scope_row_keys = cls._str_tuple(scope.get(Keys.Field.ROW_KEYS))
        apply = payload.get(Keys.Field.APPLY) is True
        accumulator.decisions.append(
            DecisionSummary(
                decision=decision,
                scope_rev=scope_rev,
                actor=actor,
                sequence_no=seq,
                scope_row_keys=scope_row_keys,
                apply=apply,
            )
        )
        accumulator.last_sequence_no = max(accumulator.last_sequence_no, seq)

        # PRD-D3 — a row-scoped decision. ``apply=True`` is the frozen apply
        # decision (⇒ APPLY_PENDING); otherwise it is a stance toggle only (the
        # stage stays STAGED, nothing executes). The agent pre-hold reason is
        # STICKY — a user override flips the stance but never clears the reason.
        if scope_row_keys and accumulator.is_rowset:
            if apply:
                accumulator.status = StagedWriteStatus.APPLY_PENDING
                accumulator.approved_rev = accumulator.latest_rev
            else:
                cls._apply_row_stance(
                    accumulator,
                    decision=decision,
                    actor=actor,
                    row_keys=scope_row_keys,
                )
            return

        # Single-artifact (D1) rev-scoped path — unchanged.
        if decision == Values.DECISION_APPROVE:
            accumulator.status = StagedWriteStatus.APPROVED
            accumulator.approved_rev = scope_rev
        elif decision == Values.DECISION_REJECT:
            accumulator.status = StagedWriteStatus.REJECTED
            accumulator.approved_rev = None
        elif decision == Values.DECISION_RESTORE:
            accumulator.status = StagedWriteStatus.STAGED
            accumulator.approved_rev = None

    @staticmethod
    def _apply_row_stance(
        accumulator: _StageAccumulator,
        *,
        decision: str,
        actor: str,
        row_keys: tuple[str, ...],
    ) -> None:
        for row_key in row_keys:
            if row_key not in accumulator.staged_rows:
                continue  # tolerate an unknown key in a folded event
            if decision == Values.DECISION_APPROVE:
                accumulator.row_stances[row_key] = RowStance.WILL_APPLY
                accumulator.row_decided_by[row_key] = actor
            elif decision == Values.DECISION_HOLD:
                accumulator.row_stances[row_key] = RowStance.HELD
                accumulator.row_decided_by[row_key] = actor

    @classmethod
    def _apply_write_applied(
        cls,
        stages: dict[str, _StageAccumulator],
        *,
        seq: int,
        payload: Mapping[str, object],
    ) -> None:
        """Fold the single legitimate execution beat (PRD-D2 state machine).

        ``APPROVED (rev N)`` + ``applied {rev N}`` ⇒ ``APPLIED`` (terminal;
        further decisions/revisions are frozen by the existing matrix). ``APPROVED
        (rev N)`` + ``failed {rev N}`` ⇒ ``STAGED`` with ``approved_rev`` cleared —
        the approval is consumed; the surface shows held state and a fresh approve
        is required to retry. Any other current state ⇒ ``CORRUPT`` (defensive;
        unreachable absent a bug — asserted in tests). The result/failure ride the
        state so E1's receipt fold + the client render from exactly this.
        """

        stage_id = cls._str_or(payload.get(Keys.Field.STAGE_ID), "")
        accumulator = stages.get(stage_id)
        if accumulator is None:
            return
        accumulator.last_sequence_no = max(accumulator.last_sequence_no, seq)
        result = cls._str_or(payload.get(Keys.Field.RESULT), "")

        # PRD-D3 — a row-set apply terminal. Matched by the frozen APPLY_PENDING
        # state (not rev): applied ⇒ APPLIED, partial ⇒ PARTIALLY_APPLIED (both
        # terminal, per-row outcomes ride ``row_results``); failed ⇒ back to
        # STAGED with the apply consumed (stances intact, a fresh apply retries).
        if accumulator.is_rowset:
            cls._apply_rowset_terminal(accumulator, result=result, payload=payload)
            return

        rev = payload.get(Keys.Field.REV)
        rev = rev if isinstance(rev, int) and not isinstance(rev, bool) else None

        matches_approved = (
            accumulator.status is StagedWriteStatus.APPROVED
            and accumulator.approved_rev is not None
            and rev == accumulator.approved_rev
        )
        if not matches_approved:
            # A ``write.applied`` for a non-approved / rev-mismatched stage is a
            # bug (the handler's approval gate + D1's freeze make it unreachable);
            # fold to CORRUPT rather than silently accept an unauthorized send.
            accumulator.status = StagedWriteStatus.CORRUPT
            accumulator.apply_result = result or None
            return
        if result == Values.RESULT_APPLIED:
            accumulator.status = StagedWriteStatus.APPLIED
            accumulator.apply_result = Values.RESULT_APPLIED
            accumulator.apply_failure_code = None
        elif result == Values.RESULT_FAILED:
            # Approval consumed: back to STAGED (held), a fresh approve retries.
            accumulator.status = StagedWriteStatus.STAGED
            accumulator.approved_rev = None
            accumulator.apply_result = Values.RESULT_FAILED
            accumulator.apply_failure_code = cls._failure_code_of(
                payload.get(Keys.Field.FAILURE)
            )
        else:
            accumulator.status = StagedWriteStatus.CORRUPT
            accumulator.apply_result = result or None

    @classmethod
    def _apply_rowset_terminal(
        cls,
        accumulator: _StageAccumulator,
        *,
        result: str,
        payload: Mapping[str, object],
    ) -> None:
        """Fold a row-set ``write.applied`` (only legitimate from APPLY_PENDING)."""

        if accumulator.status is not StagedWriteStatus.APPLY_PENDING:
            # A ``write.applied`` for a non-pending row-set is a bug (the worker
            # gate refuses unless APPLY_PENDING); fold to CORRUPT, never accept it.
            accumulator.status = StagedWriteStatus.CORRUPT
            accumulator.apply_result = result or None
            return
        if result == Values.RESULT_APPLIED:
            accumulator.status = StagedWriteStatus.APPLIED
            accumulator.apply_result = Values.RESULT_APPLIED
            cls._apply_row_results(accumulator, payload.get(Keys.Field.ROW_RESULTS))
        elif result == Values.RESULT_PARTIAL:
            accumulator.status = StagedWriteStatus.PARTIALLY_APPLIED
            accumulator.apply_result = Values.RESULT_PARTIAL
            cls._apply_row_results(accumulator, payload.get(Keys.Field.ROW_RESULTS))
        elif result == Values.RESULT_FAILED:
            # Apply consumed: back to STAGED (stances intact), a fresh apply retries.
            accumulator.status = StagedWriteStatus.STAGED
            accumulator.approved_rev = None
            accumulator.apply_result = Values.RESULT_FAILED
        else:
            accumulator.status = StagedWriteStatus.CORRUPT
            accumulator.apply_result = result or None

    @classmethod
    def _apply_row_results(cls, accumulator: _StageAccumulator, value: object) -> None:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return
        for raw in value:
            if not isinstance(raw, Mapping):
                continue
            row_key = cls._str_or(raw.get(Keys.Field.ROW_KEY), "")
            outcome = cls._str_or(raw.get(Keys.Field.OUTCOME), "")
            if not row_key or row_key not in accumulator.staged_rows:
                continue
            if outcome in (Values.ROW_OUTCOME_APPLIED, Values.ROW_OUTCOME_FAILED):
                accumulator.row_apply_outcomes[row_key] = outcome

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _str_tuple(value: object) -> tuple[str, ...]:
        """Read a list-of-strings payload field into a tuple (drops non-strings)."""

        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return ()
        return tuple(item for item in value if isinstance(item, str) and item)

    @staticmethod
    def _spans_of(value: object) -> tuple[AuthorshipSpan, ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return ()
        spans: list[AuthorshipSpan] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            start = item.get(Keys.Field.START)
            end = item.get(Keys.Field.END)
            author = item.get(Keys.Field.AUTHOR)
            if (
                isinstance(start, int)
                and not isinstance(start, bool)
                and isinstance(end, int)
                and not isinstance(end, bool)
                and author in (Values.AUTHOR_AGENT, Values.AUTHOR_USER)
                and start >= 0
                and end >= start
            ):
                spans.append(AuthorshipSpan(start=start, end=end, author=author))
        return tuple(spans)

    @staticmethod
    def _failure_code_of(value: object) -> str | None:
        """Pull ``failure.code`` (a string) from a ``write.applied{failed}`` payload."""

        if not isinstance(value, Mapping):
            return None
        code = value.get(Keys.Field.CODE)
        return code if isinstance(code, str) and code else None

    @staticmethod
    def _seq_of(event: Mapping[str, object]) -> int:
        raw = event.get(_RawKey.SEQUENCE_NO, 0)
        if isinstance(raw, bool):
            return 0
        if isinstance(raw, int):
            return raw
        try:
            return int(str(raw))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _event_type_value(event_type: object) -> str:
        value = getattr(event_type, "value", None)
        if isinstance(value, str):
            return value
        return str(event_type)

    @staticmethod
    def _str_or(value: object, default: str) -> str:
        return value if isinstance(value, str) else default


class _RawKey:
    EVENT_TYPE = "event_type"
    SEQUENCE_NO = "sequence_no"
    PAYLOAD = "payload"


# ---------------------------------------------------------------------------
# WriteStager — emits the three events; folds to read current state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteStager:
    """Turns a draft-send proposal into a staged surface, and records decisions.

    Reads current state as a pure fold of the run's ledger; validates every
    transition fail-closed BEFORE emitting; emits only on success (so every 4xx
    leaves the ledger untouched). Never executes: ``write.applied`` is never
    emitted here and no MCP client is ever touched.
    """

    draft_store: DraftStorePort
    ledger: StageLedgerPort
    differ: type[RevisionDiffer] = RevisionDiffer
    # PRD-D2: optional, duck-typed on ``enqueue_stage_commit`` (same
    # optional-injection style as ``DraftService.event_producer``). When wired, a
    # NEW ``decision.recorded{approve}`` enqueues exactly one durable commit
    # command; ``None`` ⇒ the decision records and NOTHING executes (fail-open to
    # no-commit, never to execution). Idempotent re-approves + reject/restore
    # never enqueue.
    commit_queue: StageCommitQueuePort | None = None
    # PRD-D3 (FR-C8): resolves whether an allow-always connector policy auto-
    # applies a row-set's unflagged rows. ``None`` ⇒ nothing auto-applies. Pure
    # (no IO); agent pre-holds are NEVER passed here.
    policy_resolver: WritePolicyResolverPort | None = None

    # -- propose ------------------------------------------------------------

    async def stage(
        self,
        *,
        run: object,
        org_id: str,
        run_id: str,
        draft: DraftRecord,
        target_connector: str,
        target_op: str,
    ) -> StagedWriteState:
        """Stage a fresh single-artifact write for an agent-authored draft.

        Allocates a new ``stage_id`` + ``surface_id`` and emits, in order,
        ``surface.created`` (a fresh message surface — an agent draft never has a
        prior v2 read surface), ``write.staged``, then ``revision.added`` (rev 1,
        author ``agent``, empty spans). Returns the folded state.
        """

        stage_id = uuid4().hex
        surface_id = uuid4().hex
        proposal_ref = DraftRef.proposal(draft_id=draft.draft_id, version=draft.version)
        title = self._title_for(draft=draft, connector=target_connector, op=target_op)

        prior = await self.ledger.list_events(org_id=org_id, run_id=run_id)
        emitted: list[_LedgerEventLike] = []

        emitted.append(
            await self.ledger.emit(
                run=run,
                event_type_value=LedgerEventType.SURFACE_CREATED.value,
                payload={
                    Keys.Field.V: Values.PAYLOAD_V,
                    Keys.Field.SURFACE_ID: surface_id,
                    Keys.Field.KIND: Values.KIND_MESSAGE,
                    Keys.Field.SOURCE: {
                        Keys.Field.CONNECTOR: target_connector,
                        Keys.Field.OP: target_op,
                    },
                    Keys.Field.TITLE: title,
                    Keys.Field.PAYLOAD_REF: proposal_ref,
                },
                summary=Messages.SURFACE_CREATED,
            )
        )
        emitted.append(
            await self.ledger.emit(
                run=run,
                event_type_value=LedgerEventType.WRITE_STAGED.value,
                payload={
                    Keys.Field.V: Values.PAYLOAD_V,
                    Keys.Field.STAGE_ID: stage_id,
                    Keys.Field.SURFACE_ID: surface_id,
                    Keys.Field.TARGET: {
                        Keys.Field.CONNECTOR: target_connector,
                        Keys.Field.OP: target_op,
                    },
                    Keys.Field.PROPOSAL_REF: proposal_ref,
                },
                summary=Messages.WRITE_STAGED,
            )
        )
        emitted.append(
            await self.ledger.emit(
                run=run,
                event_type_value=LedgerEventType.REVISION_ADDED.value,
                payload=self._revision_payload(
                    stage_id=stage_id,
                    rev=1,
                    author=Values.AUTHOR_AGENT,
                    proposal_ref=proposal_ref,
                    diff_ref=DraftRef.diff(
                        draft_id=draft.draft_id,
                        from_version=draft.version,
                        to_version=draft.version,
                    ),
                    spans=(),
                ),
                summary=Messages.REVISION_ADDED,
            )
        )
        return self._require_state(self._fold([*prior, *emitted]), stage_id=stage_id)

    # -- propose (bulk row-set, PRD-D3) -------------------------------------

    async def stage_rowset(
        self,
        *,
        run: object,
        org_id: str,
        run_id: str,
        target_connector: str,
        target_op: str,
        rows: Sequence[StagedRow],
        agent_holds: Sequence[AgentHold] = (),
        title: str,
    ) -> StagedWriteState:
        """Stage a bulk row-set write (N per-row changes as one table surface).

        Validates fail-closed (caps, unique keys, holds ⊆ rows) BEFORE any emit —
        a violation raises :class:`InvalidRowset` (422) and NO event is emitted.
        On success emits ``surface.created {kind: table}`` → ``write.staged``
        (rows count + agent_holds) → ``revision.added`` (rev 1, author agent,
        inline rowset). Then, if an allow-always policy bypasses the write, emits
        the ``actor: policy`` apply decision + enqueues (FR-C8) — agent pre-holds
        are excluded unconditionally. NEVER touches an MCP client.
        """

        rows_t = tuple(rows)
        holds_t = tuple(agent_holds)
        try:
            RowsetValidator.validate(rows=rows_t, agent_holds=holds_t)
        except RowsetValidationError as exc:
            raise InvalidRowset(exc.safe_message) from exc

        stage_id = uuid4().hex
        surface_id = uuid4().hex
        proposal_ref = StageRef.proposal(stage_id=stage_id, rev=1)
        clean_title = (title or "").strip()[: Values.TITLE_MAX_LEN] or (
            f"{target_connector}{Titles.SEPARATOR}{target_op}"[: Values.TITLE_MAX_LEN]
        )

        prior = await self.ledger.list_events(org_id=org_id, run_id=run_id)
        emitted: list[_LedgerEventLike] = []
        emitted.append(
            await self.ledger.emit(
                run=run,
                event_type_value=LedgerEventType.SURFACE_CREATED.value,
                payload={
                    Keys.Field.V: Values.PAYLOAD_V,
                    Keys.Field.SURFACE_ID: surface_id,
                    Keys.Field.KIND: Values.KIND_TABLE,
                    Keys.Field.SOURCE: {
                        Keys.Field.CONNECTOR: target_connector,
                        Keys.Field.OP: target_op,
                    },
                    Keys.Field.TITLE: clean_title,
                    Keys.Field.PAYLOAD_REF: proposal_ref,
                },
                summary=Messages.SURFACE_CREATED,
            )
        )
        emitted.append(
            await self.ledger.emit(
                run=run,
                event_type_value=LedgerEventType.WRITE_STAGED.value,
                payload={
                    Keys.Field.V: Values.PAYLOAD_V,
                    Keys.Field.STAGE_ID: stage_id,
                    Keys.Field.SURFACE_ID: surface_id,
                    Keys.Field.TARGET: {
                        Keys.Field.CONNECTOR: target_connector,
                        Keys.Field.OP: target_op,
                    },
                    Keys.Field.PROPOSAL_REF: proposal_ref,
                    Keys.Field.ROWS: len(rows_t),
                    Keys.Field.AGENT_HOLDS: [
                        {
                            Keys.Field.ROW_KEY: hold.row_key,
                            Keys.Field.REASON: hold.reason,
                        }
                        for hold in holds_t
                    ],
                },
                summary=Messages.ROWSET_STAGED,
            )
        )
        emitted.append(
            await self.ledger.emit(
                run=run,
                event_type_value=LedgerEventType.REVISION_ADDED.value,
                payload={
                    Keys.Field.V: Values.PAYLOAD_V,
                    Keys.Field.STAGE_ID: stage_id,
                    Keys.Field.REV: 1,
                    Keys.Field.AUTHOR: Values.AUTHOR_AGENT,
                    Keys.Field.DIFF_REF: proposal_ref,
                    Keys.Field.PROPOSAL_REF: proposal_ref,
                    Keys.Field.ROWSET: self._rowset_payload(rows_t),
                },
                summary=Messages.REVISION_ADDED,
            )
        )

        # FR-C8 allow-always branch: unflagged rows auto-apply under an
        # allow-always connector policy; agent pre-holds STILL hold.
        held_keys = {hold.row_key for hold in holds_t}
        auto_keys = tuple(row.row_key for row in rows_t if row.row_key not in held_keys)
        if (
            auto_keys
            and self.policy_resolver is not None
            and self.commit_queue is not None
            and self.policy_resolver.bypass_for(
                connector=target_connector, op=target_op
            )
        ):
            approve = await self.ledger.emit(
                run=run,
                event_type_value=LedgerEventType.DECISION_RECORDED.value,
                payload=self._row_decision_payload(
                    stage_id=stage_id,
                    decision=Values.DECISION_APPROVE,
                    row_keys=auto_keys,
                    actor=Values.ACTOR_POLICY,
                    apply=True,
                ),
                summary=Messages.ROW_DECISION_RECORDED,
            )
            emitted.append(approve)
            await self.commit_queue.enqueue_stage_commit(
                stage_id=stage_id,
                run_id=run_id,
                org_id=org_id,
                user_id=self._run_attr(run, "user_id"),
                conversation_id=self._run_attr(run, "conversation_id"),
                rev=1,
                decision_seq=approve.sequence_no,
                row_keys=auto_keys,
            )

        return self._require_state(self._fold([*prior, *emitted]), stage_id=stage_id)

    # -- decide (bulk row-set) ---------------------------------------------

    async def record_row_decision(
        self,
        *,
        run: object,
        org_id: str,
        run_id: str,
        stage_id: str,
        decision: str,
        row_keys: Sequence[str],
    ) -> StagedWriteState:
        """Toggle per-row stance (approve/hold). NEVER enqueues, NEVER executes.

        Precondition: the stage is a row-set, status == STAGED, and every named
        key exists. A ``rev``-scoped approve/hold on a row-set is a 422
        (:class:`UnsupportedDecision`); a decided/frozen stage is a 409
        (:class:`StageFrozen`); an unknown key is a 404 (:class:`UnknownRowKey`).
        """

        keys_t = tuple(dict.fromkeys(row_keys))  # de-dupe, keep order
        prior = await self.ledger.list_events(org_id=org_id, run_id=run_id)
        state = self._require_state(self._fold(prior), stage_id=stage_id)

        if decision not in (Values.DECISION_APPROVE, Values.DECISION_HOLD):
            raise UnsupportedDecision()
        if not state.is_rowset():
            raise UnsupportedDecision()
        if state.status is not StagedWriteStatus.STAGED:
            raise StageFrozen()
        if not keys_t:
            raise UnknownRowKey()
        known = {row.row_key for row in state.rows or ()}
        if any(key not in known for key in keys_t):
            raise UnknownRowKey()

        emitted = await self.ledger.emit(
            run=run,
            event_type_value=LedgerEventType.DECISION_RECORDED.value,
            payload=self._row_decision_payload(
                stage_id=stage_id,
                decision=decision,
                row_keys=keys_t,
                actor=Values.ACTOR_USER,
                apply=False,
            ),
            summary=Messages.ROW_DECISION_RECORDED,
        )
        return self._require_state(self._fold([*prior, emitted]), stage_id=stage_id)

    async def apply_rows(
        self,
        *,
        run: object,
        org_id: str,
        run_id: str,
        stage_id: str,
        rev: int,
        row_keys: Sequence[str],
    ) -> StagedWriteState:
        """The ONLY row-set path to execution — emit the apply decision + enqueue.

        Precondition: row-set stage, status == STAGED, ``rev == latest_rev``, and
        ``row_keys`` equals the current will-apply set EXACTLY (WYSIWYG — you
        apply exactly the set you saw). A mismatched set is a 409
        (:class:`ApplySetMismatch`); a duplicate apply of the same rev+set while
        pending/applied is idempotent (200, no event, no enqueue). Held rows are
        never named — they cannot enter the apply set.
        """

        requested = frozenset(row_keys)
        prior = await self.ledger.list_events(org_id=org_id, run_id=run_id)
        state = self._require_state(self._fold(prior), stage_id=stage_id)
        if not state.is_rowset():
            raise UnsupportedDecision()

        # Idempotent duplicate apply: a prior apply decision already froze the
        # same set at this rev — return current state, emit nothing, enqueue nothing.
        if state.status in (
            StagedWriteStatus.APPLY_PENDING,
            StagedWriteStatus.APPLIED,
            StagedWriteStatus.PARTIALLY_APPLIED,
        ):
            applied = self._apply_decision_of(state)
            if (
                applied is not None
                and frozenset(applied.scope_row_keys) == requested
                and rev == state.latest_rev
            ):
                return state
            raise StageFrozen()

        if state.status is not StagedWriteStatus.STAGED:
            raise StageFrozen()
        if rev != state.latest_rev:
            raise StaleRevision()
        unknown = requested - {row.row_key for row in state.rows or ()}
        if unknown:
            raise UnknownRowKey()
        if requested != frozenset(state.will_apply_keys()):
            # WYSIWYG: you apply exactly the current will-apply set, nothing else.
            raise ApplySetMismatch()

        ordered = state.will_apply_keys()  # canonical order for the payload
        emitted = await self.ledger.emit(
            run=run,
            event_type_value=LedgerEventType.DECISION_RECORDED.value,
            payload=self._row_decision_payload(
                stage_id=stage_id,
                decision=Values.DECISION_APPROVE,
                row_keys=ordered,
                actor=Values.ACTOR_USER,
                apply=True,
            ),
            summary=Messages.ROW_DECISION_RECORDED,
        )
        if self.commit_queue is not None:
            await self.commit_queue.enqueue_stage_commit(
                stage_id=stage_id,
                run_id=run_id,
                org_id=org_id,
                user_id=self._run_attr(run, "user_id"),
                conversation_id=self._run_attr(run, "conversation_id"),
                rev=rev,
                decision_seq=emitted.sequence_no,
                row_keys=ordered,
            )
        return self._require_state(self._fold([*prior, emitted]), stage_id=stage_id)

    # -- edit ---------------------------------------------------------------

    async def add_user_revision(
        self,
        *,
        run: object,
        org_id: str,
        run_id: str,
        stage_id: str,
        base_rev: int,
        content_text: str,
        title: str | None = None,
    ) -> StagedWriteState:
        """Add a user free-form revision; server-diff produces authorship spans.

        Fails closed: 404 unknown stage; 409 ``stage_frozen`` unless STAGED; 409
        ``stale_revision`` unless ``base_rev == latest_rev``; 409 ``edit_conflict``
        on a concurrent draft-version race. On success inserts a new draft version
        and emits ``revision.added`` (author ``user``) with the diffed spans.
        """

        prior = await self.ledger.list_events(org_id=org_id, run_id=run_id)
        state = self._require_state(self._fold(prior), stage_id=stage_id)
        if state.status is not StagedWriteStatus.STAGED:
            raise StageFrozen()
        if base_rev != state.latest_rev:
            raise StaleRevision()
        base = state.latest_revision()
        if base is None:
            raise StageNotFound()
        base_parsed = DraftRef.parse_proposal(base.proposal_ref)
        if base_parsed is None:
            raise StageNotFound()
        base_draft_id, base_version = base_parsed

        base_record = await self.draft_store.get_version(
            org_id=org_id, draft_id=base_draft_id, version=base_version
        )
        if base_record is None:
            raise StageNotFound()

        new_record = self._next_draft_version(
            previous=base_record,
            run_id=run_id,
            content_text=content_text,
            title=title,
        )
        try:
            # Concurrent-edit guard: a racing edit that already claimed
            # ``new_version`` makes this insert raise OptimisticConflict → 409.
            await self.draft_store.expect_status(
                org_id=org_id,
                draft_id=base_draft_id,
                expected_version=base_version,
            )
            persisted = await self.draft_store.insert_version(new_record)
        except (OptimisticConflict, KeyError) as exc:
            raise EditConflict() from exc

        spans = self.differ.spans(
            old=base_record.content_text,
            new=content_text,
            author=Values.AUTHOR_USER,
        )
        new_rev = state.latest_rev + 1
        emitted = await self.ledger.emit(
            run=run,
            event_type_value=LedgerEventType.REVISION_ADDED.value,
            payload=self._revision_payload(
                stage_id=stage_id,
                rev=new_rev,
                author=Values.AUTHOR_USER,
                proposal_ref=DraftRef.proposal(
                    draft_id=persisted.draft_id, version=persisted.version
                ),
                diff_ref=DraftRef.diff(
                    draft_id=persisted.draft_id,
                    from_version=base_version,
                    to_version=persisted.version,
                ),
                spans=spans,
            ),
            summary=Messages.REVISION_ADDED,
        )
        return self._require_state(self._fold([*prior, emitted]), stage_id=stage_id)

    # -- decide -------------------------------------------------------------

    async def record_decision(
        self,
        *,
        run: object,
        org_id: str,
        run_id: str,
        stage_id: str,
        decision: str,
        rev: int | None,
    ) -> StagedWriteState:
        """Record an approve / reject / restore decision (fail-closed matrix).

        ``hold`` raises :class:`UnsupportedDecision` (422 — row-scoped, PRD-D3).
        ``approve`` on a non-latest rev raises :class:`StaleRevision` (409, no
        event) — the WYSIWYG pin. An already-decided stage is frozen (409) except
        an idempotent re-approve of the same rev (200, no duplicate event) and a
        ``restore`` of a rejected stage. NOTHING executes on approve.
        """

        prior = await self.ledger.list_events(org_id=org_id, run_id=run_id)
        state = self._require_state(self._fold(prior), stage_id=stage_id)

        if decision == Values.DECISION_HOLD:
            raise UnsupportedDecision()

        # PRD-D3 — on a row-set, a REV-scoped approve is a 422: row-set execution
        # is the ``/apply`` route only (rev-scoped approve/hold never applies a
        # row-set). Whole-stage reject/restore stay rev-scoped (D1 semantics).
        if state.is_rowset() and decision == Values.DECISION_APPROVE:
            raise UnsupportedDecision()

        if decision == Values.DECISION_RESTORE:
            scope_rev = self._decide_restore(state)
        elif decision == Values.DECISION_APPROVE:
            outcome = self._decide_approve(state, rev=rev)
            if outcome is None:
                # Idempotent re-approve of the same rev — no duplicate event.
                return state
            scope_rev = outcome
        elif decision == Values.DECISION_REJECT:
            scope_rev = self._decide_reject(state, rev=rev)
        else:
            raise UnsupportedDecision()

        emitted = await self.ledger.emit(
            run=run,
            event_type_value=LedgerEventType.DECISION_RECORDED.value,
            payload={
                Keys.Field.V: Values.PAYLOAD_V,
                Keys.Field.STAGE_ID: stage_id,
                Keys.Field.DECISION: decision,
                Keys.Field.SCOPE: {Keys.Field.REV: scope_rev},
                Keys.Field.ACTOR: Values.ACTOR_USER,
            },
            summary=Messages.DECISION_RECORDED,
        )
        # PRD-D2 — a NEW approve (this branch is unreachable for the idempotent
        # re-approve, which returned above) enqueues EXACTLY ONE durable commit
        # command. Reject / restore never enqueue. ``commit_queue is None`` ⇒ the
        # decision records and nothing executes (fail-open to no-commit).
        if decision == Values.DECISION_APPROVE and self.commit_queue is not None:
            await self.commit_queue.enqueue_stage_commit(
                stage_id=stage_id,
                run_id=run_id,
                org_id=org_id,
                user_id=self._run_attr(run, "user_id"),
                conversation_id=self._run_attr(run, "conversation_id"),
                rev=scope_rev,
                decision_seq=emitted.sequence_no,
            )
        return self._require_state(self._fold([*prior, emitted]), stage_id=stage_id)

    # -- read ---------------------------------------------------------------

    async def get_state(
        self, *, org_id: str, run_id: str, stage_id: str
    ) -> StagedWriteState:
        """Fold the run's ledger and return the stage's state (404 if unknown)."""

        events = await self.ledger.list_events(org_id=org_id, run_id=run_id)
        return self._require_state(self._fold(events), stage_id=stage_id)

    # -- decision matrix cells ----------------------------------------------

    @staticmethod
    def _decide_approve(state: StagedWriteState, *, rev: int | None) -> int | None:
        """Return the rev to pin, ``None`` for an idempotent no-op, else raise."""

        if rev is None:
            raise MalformedDecision()
        if state.status is StagedWriteStatus.APPROVED:
            if state.approved_rev == rev:
                return None  # idempotent re-approve of the same rev
            raise StageFrozen()
        if state.status is not StagedWriteStatus.STAGED:
            raise StageFrozen()
        if rev != state.latest_rev:
            raise StaleRevision()
        return rev

    @staticmethod
    def _decide_reject(state: StagedWriteState, *, rev: int | None) -> int:
        if rev is None:
            raise MalformedDecision()
        if state.status is not StagedWriteStatus.STAGED:
            raise StageFrozen()
        return rev

    @staticmethod
    def _decide_restore(state: StagedWriteState) -> int:
        if state.status is not StagedWriteStatus.REJECTED:
            raise StageFrozen()
        # restore re-pins the latest rev server-side (rev on the request ignored).
        return state.latest_rev

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _run_attr(run: object, name: str) -> str:
        """Read a string attribute off the opaque run record (``""`` when absent)."""

        value = getattr(run, name, "")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _fold(events: Sequence[_LedgerEventLike]) -> dict[str, StagedWriteState]:
        return StagedWriteFold.fold(events)

    @staticmethod
    def _require_state(
        states: Mapping[str, StagedWriteState], *, stage_id: str
    ) -> StagedWriteState:
        state = states.get(stage_id)
        if state is None:
            raise StageNotFound()
        return state

    @staticmethod
    def _revision_payload(
        *,
        stage_id: str,
        rev: int,
        author: str,
        proposal_ref: str,
        diff_ref: str,
        spans: tuple[AuthorshipSpan, ...],
    ) -> dict[str, object]:
        return {
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.STAGE_ID: stage_id,
            Keys.Field.REV: rev,
            Keys.Field.AUTHOR: author,
            Keys.Field.DIFF_REF: diff_ref,
            # Additive (SDR §5 note): the snapshot of THIS rev + the spans the
            # server diffed. Both ride inline for the client; the projector
            # allow-list keeps them.
            Keys.Field.PROPOSAL_REF: proposal_ref,
            Keys.Field.AUTHORSHIP_SPANS: [
                {
                    Keys.Field.START: span.start,
                    Keys.Field.END: span.end,
                    Keys.Field.AUTHOR: span.author,
                }
                for span in spans
            ],
        }

    @staticmethod
    def _rowset_payload(rows: tuple[StagedRow, ...]) -> dict[str, object]:
        """Build the inline ``revision.added.rowset`` payload (full row content)."""

        return {
            Keys.Field.ROWS: [
                {
                    Keys.Field.ROW_KEY: row.row_key,
                    Keys.Field.TITLE: row.title,
                    Keys.Field.TARGET_ARGS: dict(row.target_args),
                    Keys.Field.CHANGES: [
                        {
                            Keys.Field.FIELD: change.field,
                            Keys.Field.OLD: change.old,
                            Keys.Field.NEW: change.new,
                        }
                        for change in row.changes
                    ],
                }
                for row in rows
            ]
        }

    @staticmethod
    def _row_decision_payload(
        *,
        stage_id: str,
        decision: str,
        row_keys: tuple[str, ...],
        actor: str,
        apply: bool,
    ) -> dict[str, object]:
        """Build a row-scoped ``decision.recorded`` payload (stance or apply)."""

        payload: dict[str, object] = {
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.STAGE_ID: stage_id,
            Keys.Field.DECISION: decision,
            Keys.Field.SCOPE: {Keys.Field.ROW_KEYS: list(row_keys)},
            Keys.Field.ACTOR: actor,
        }
        if apply:
            payload[Keys.Field.APPLY] = True
        return payload

    @staticmethod
    def _apply_decision_of(state: StagedWriteState) -> DecisionSummary | None:
        """Return the latest apply-scoped approve decision on a row-set, or None."""

        for decision in reversed(state.decisions):
            if decision.apply and decision.decision == Values.DECISION_APPROVE:
                return decision
        return None

    @staticmethod
    def _title_for(*, draft: DraftRecord, connector: str, op: str) -> str:
        title = (draft.title or "").strip()
        if title:
            return title[: Values.TITLE_MAX_LEN]
        return f"{connector}{Titles.SEPARATOR}{op}"[: Values.TITLE_MAX_LEN]

    @staticmethod
    def _next_draft_version(
        *,
        previous: DraftRecord,
        run_id: str,
        content_text: str,
        title: str | None,
    ) -> DraftRecord:
        return previous.model_copy(
            update={
                "id": uuid4().hex,
                "version": previous.version + 1,
                "run_id": run_id,
                "content_text": content_text,
                "title": (title.strip()[:240] if title else previous.title),
                "status": DraftStatus.SEND_PENDING_APPROVAL,
            }
        )


__all__ = [
    "ApplySetMismatch",
    "AuthorshipSpan",
    "DecisionSummary",
    "DraftRef",
    "EditConflict",
    "InvalidRowset",
    "MalformedDecision",
    "RevisionSummary",
    "StageCommitQueuePort",
    "StageForbidden",
    "StageFrozen",
    "StageLedgerPort",
    "StageNotFound",
    "StageRef",
    "StaleRevision",
    "StagedWriteError",
    "StagedWriteFold",
    "StagedWriteState",
    "StagedWriteStatus",
    "UnknownRowKey",
    "UnsupportedDecision",
    "WritePolicyResolverPort",
    "WriteStager",
]
