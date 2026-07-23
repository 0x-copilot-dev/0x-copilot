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
    ) -> None:
        """Enqueue the commit command for one approved ``(stage_id, rev)``."""


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


# ---------------------------------------------------------------------------
# Fold output contracts
# ---------------------------------------------------------------------------


class StagedWriteStatus(StrEnum):
    STAGED = "staged"
    REJECTED = "rejected"
    APPROVED = "approved"
    # PRD-D2: terminal — the CommitEngine sent exactly the approved rev.
    APPLIED = "applied"
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
    """One folded decision: kind, rev it scoped, actor, seq."""

    decision: str
    scope_rev: PositiveInt | None
    actor: str
    sequence_no: int


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

    def to_state(self) -> StagedWriteState:
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
        stages[stage_id] = _StageAccumulator(
            stage_id=stage_id,
            surface_id=surface_id,
            draft_id=draft_id,
            target_connector=cls._str_or(target.get(Keys.Field.CONNECTOR), ""),
            target_op=cls._str_or(target.get(Keys.Field.OP), ""),
            first_sequence_no=seq,
            last_sequence_no=seq,
        )

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
        accumulator.decisions.append(
            DecisionSummary(
                decision=decision,
                scope_rev=scope_rev,
                actor=cls._str_or(payload.get(Keys.Field.ACTOR), ""),
                sequence_no=seq,
            )
        )
        if decision == Values.DECISION_APPROVE:
            accumulator.status = StagedWriteStatus.APPROVED
            accumulator.approved_rev = scope_rev
        elif decision == Values.DECISION_REJECT:
            accumulator.status = StagedWriteStatus.REJECTED
            accumulator.approved_rev = None
        elif decision == Values.DECISION_RESTORE:
            accumulator.status = StagedWriteStatus.STAGED
            accumulator.approved_rev = None
        accumulator.last_sequence_no = max(accumulator.last_sequence_no, seq)

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

    # -- helpers ------------------------------------------------------------

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
    "AuthorshipSpan",
    "DecisionSummary",
    "DraftRef",
    "EditConflict",
    "MalformedDecision",
    "RevisionSummary",
    "StageCommitQueuePort",
    "StageForbidden",
    "StageFrozen",
    "StageLedgerPort",
    "StageNotFound",
    "StaleRevision",
    "StagedWriteError",
    "StagedWriteFold",
    "StagedWriteState",
    "StagedWriteStatus",
    "UnsupportedDecision",
    "WriteStager",
]
