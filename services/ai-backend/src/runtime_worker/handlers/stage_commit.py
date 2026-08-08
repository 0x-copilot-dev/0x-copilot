"""Queued staged-write commit handling — the ONLY producer of ``write.applied`` (PRD-D2).

The API records an approve decision and enqueues a durable ``stage_commit_requested``
command; this handler consumes it. It is the single legitimate path to a
``write.applied`` event, and it never executes inline in the API.

``handle(command)`` orders four fail-closed invariants around the one side effect
(a bulk row-set apply adds a fifth — see ``_handle_rowset``: the row accounting is
re-established HERE, at the last point before dispatch, because the row the fold
hands back was rebuilt from an untrusted ledger payload):

1. **Approval gate (fail-closed).** Fold the run's ledger through ``StagedWriteFold``
   and refuse unless the stage is ``APPROVED``, ``approved_rev == command.rev``, and the
   approving ``decision.recorded`` sits at ``sequence_no == command.decision_seq``.
   Any mismatch ⇒ a warn-logged no-op with NO event (the ledger records only what
   happened; a stale command is unreachable absent a bug — D1 freezes approved stages).
2. **Local precondition.** The pinned draft must still be ``send_pending_approval``;
   drift ⇒ ``write.applied{failed, precondition_drift}`` + drift-abort audit, no send.
3. **Shared effect dispatch.** The handler converts the exact approved revision
   into a server-derived request and sends it through ``EffectDispatchCoordinator``
   via ``McpEffectExecutor``.  That canonical path owns prepare → durable claim
   → post-approval authorization → apply; this handler never owns an MCP client
   or a second side-effecting executor.
4. **Audit.** Every branch appends the matching audit action through the existing
   ``write_audit_log`` port.

The handler owns emission + draft mutation. The connector output is untrusted:
only the ``commit://`` receipt ref rides the event.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from agent_runtime.api.constants import Values as ApiValues
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.effects.dispatch import EffectDispatchResult
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.persistence.ports import OptimisticConflict
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.commit_engine import StageCommitRequest
from agent_runtime.surfaces_v2.constants import Keys, Messages, Values
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectOutcome,
    LedgerEventType,
)
from agent_runtime.surfaces_v2.rowset import RowStance, StagedRow
from agent_runtime.surfaces_v2.staging import (
    DraftRef,
    StagedWriteFold,
    StagedWriteState,
    StagedWriteStatus,
)
from runtime_api.schemas import (
    RunRecord,
    RuntimeApiEventType,
    RuntimeStageCommitCommand,
)
from runtime_worker.staged_write_effect_dispatch import (
    StagedWriteEffectDispatcher,
    StagedWriteEffectDispatcherFactory,
)

_LOGGER = logging.getLogger("runtime_worker.stage_commit")

# Audit action strings. The two shared v1 values are REDECLARED verbatim (never
# imported from the off-limits v1 island — a byte diff proves equality); the
# third is NEW in D2.
_AUDIT_COMMITTED = "surface.commit.committed"
_AUDIT_ABORTED_DRIFT = "surface.commit.aborted_precondition_drift"
_AUDIT_FAILED = "surface.commit.failed"
# D3 last-mile accounting refusal: a commanded row could not re-establish what
# it would send, so nothing was dispatched. Its own action string because it is
# not a connector failure and not target drift.
_AUDIT_ABORTED_UNACCOUNTED = "surface.commit.aborted_unaccounted_row"


class RuntimeStageCommitHandler:
    """Consume ``stage_commit_requested`` commands through the shared A5 dispatcher."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        draft_store: object | None = None,
        dispatcher_factory: StagedWriteEffectDispatcherFactory | None = None,
        settings: RuntimeSettings | None = None,
        on_event_appended: Callable[[str], None] | None = None,
    ) -> None:
        self.persistence = persistence
        self.event_store = event_store
        self.settings = settings or RuntimeSettings.load()
        self._draft_store = draft_store
        self.event_producer = RuntimeEventProducer(
            persistence=persistence,
            event_store=event_store,
            on_event_appended=on_event_appended,
        )
        # The worker composition root supplies the shared claim/dispatch factory.
        # ``None`` remains fail-closed for isolated/misconfigured construction:
        # approval can record but no external change can occur.
        self._dispatcher_factory = dispatcher_factory

    async def handle(self, command: RuntimeStageCommitCommand) -> None:
        """Re-validate the approval, dispatch exactly the approved rev, ledger the result."""

        run = await self.persistence.get_run(
            org_id=command.org_id, run_id=command.run_id
        )
        if run is None:
            _LOGGER.warning(
                "stage_commit.unknown_run stage_id=%s run_id=%s",
                command.stage_id,
                command.run_id,
            )
            return

        # PRD-D3 — a bulk row-set apply routes to the row-scoped pipeline. The
        # single-artifact (D2) path below is unchanged.
        if command.row_keys is not None:
            await self._handle_rowset(run=run, command=command)
            return

        state = await self._fold_stage(command)
        if state is None or not self._approval_gate(state, command):
            # Fail-closed: no matching approve decision ⇒ no event, nothing sends.
            _LOGGER.warning(
                "stage_commit.gate_refused stage_id=%s rev=%s decision_seq=%s",
                command.stage_id,
                command.rev,
                command.decision_seq,
            )
            return

        record = await self._resolve_pinned_draft(
            org_id=command.org_id, state=state, rev=command.rev
        )
        # Local precondition: the pinned draft must still be pending approval. A
        # missing snapshot or a status/version change ⇒ drift refusal (no send).
        if record is None or not await self._draft_still_pending(record):
            await self._emit_failed(
                run=run,
                command=command,
                failure_code=Values.FAILURE_PRECONDITION_DRIFT,
                audit_action=_AUDIT_ABORTED_DRIFT,
            )
            return

        request = self._build_request(command=command, record=record, state=state)
        dispatched = await self._dispatch(
            run=run,
            request=request,
            state=state,
            command=command,
        )
        if dispatched is None:
            return
        if dispatched.outcome is EffectOutcome.APPLIED:
            await self._on_committed(run=run, command=command, record=record)
            return
        if dispatched.safe_code in {"claimed", "applied"}:
            # The first attempt already emitted ``write.applied`` — full no-op.
            _LOGGER.info(
                "stage_commit.idempotent_replay stage_id=%s rev=%s",
                command.stage_id,
                command.rev,
            )
            return
        await self._emit_failed(
            run=run,
            command=command,
            failure_code=(
                Values.FAILURE_ATTEMPT_INDETERMINATE
                if dispatched.outcome is EffectOutcome.INDETERMINATE
                else Values.FAILURE_CONNECTOR_ERROR
            ),
            audit_action=_AUDIT_FAILED,
        )

    # -- PRD-D3 bulk row-set apply ------------------------------------------

    async def _handle_rowset(
        self, *, run: object, command: RuntimeStageCommitCommand
    ) -> None:
        """Dispatch ONLY the approved rows, per-row idempotent; ledger the outcomes.

        Two fail-closed gates, in this order, and neither can be the only one.

        1. **Authorization** (fold-based): the stage must be APPLY_PENDING, the
           apply decision at ``command.decision_seq`` must cover EXACTLY
           ``command.row_keys``, every key must fold ``will_apply``, and none may
           already have an outcome. Any mismatch ⇒ warn-log + no-op, NO event —
           an unauthorized command is not an outcome worth recording.
        2. **Accounting** (:meth:`StagedWriteState.row_send_plan`): every
           commanded row must still prove what it would send. This is the LAST
           point before dispatch, and it is separate from gate 1 on purpose —
           gate 1 asks "may this command run", gate 2 asks "can what it would
           send be shown to be what was reviewed". A refusal here is a real
           user-visible outcome, so unlike gate 1 it emits
           ``write.applied{failed, rowset_unaccounted}`` naming the row and the
           rule, and dispatches NOTHING — not the offending row, and not its
           siblings either. Shrinking the batch would turn the apply the user
           authorized into a different one and would let an account stripped
           from one row pass quietly.

        Held rows are never dispatched by either path.
        """

        state = await self._fold_stage(command)
        commanded = tuple(command.row_keys or ())
        if state is None or not self._rowset_gate(state, command, commanded):
            _LOGGER.warning(
                "stage_commit.rowset_gate_refused stage_id=%s decision_seq=%s",
                command.stage_id,
                command.decision_seq,
            )
            return

        plan = state.row_send_plan(commanded)
        if plan.refusal is not None:
            # The row key is safe to log (the producer chose it); no value is.
            _LOGGER.warning(
                "stage_commit.rowset_unaccounted stage_id=%s row_key=%s: nothing sent",
                command.stage_id,
                plan.refusal.row_key,
            )
            await self._emit_failed(
                run=run,
                command=command,
                failure_code=Values.FAILURE_ROWSET_UNACCOUNTED,
                audit_action=_AUDIT_ABORTED_UNACCOUNTED,
                detail=plan.refusal.message(),
                row_keys=commanded,
            )
            return

        row_results: list[dict[str, object]] = []
        applied = 0
        failed = 0
        for row in plan.rows:
            row_key = row.row_key
            request = self._build_rowset_request(command=command, state=state, row=row)
            dispatched = await self._dispatch(
                run=run,
                request=request,
                state=state,
                command=command,
            )
            row_applied = (
                dispatched is not None and dispatched.outcome is EffectOutcome.APPLIED
            )
            if row_applied:
                applied += 1
                row_outcome = Values.ROW_OUTCOME_APPLIED
            else:
                failed += 1
                row_outcome = Values.ROW_OUTCOME_FAILED
            row_results.append(
                {Keys.Field.ROW_KEY: row_key, Keys.Field.OUTCOME: row_outcome}
            )
            await self._write_audit(
                run=run,
                command=command,
                action=_AUDIT_COMMITTED if row_applied else _AUDIT_FAILED,
                metadata={
                    Keys.Field.ROW_KEY: row_key,
                    Keys.Field.OUTCOME: row_outcome,
                },
            )

        result = self._aggregate_result(applied=applied, failed=failed)
        await self._emit_rowset_applied(
            run=run,
            command=command,
            state=state,
            result=result,
            row_keys=commanded,
            row_results=row_results,
        )

    @staticmethod
    def _rowset_gate(
        state: StagedWriteState,
        command: RuntimeStageCommitCommand,
        commanded: tuple[str, ...],
    ) -> bool:
        """Whether the folded state authorizes EXACTLY this row-set apply command."""

        if state.status is not StagedWriteStatus.APPLY_PENDING:
            return False
        if not commanded:
            return False
        commanded_set = set(commanded)
        # The apply decision at this exact seq must cover exactly the commanded set.
        apply_ok = any(
            decision.apply
            and decision.decision == Values.DECISION_APPROVE
            and decision.sequence_no == command.decision_seq
            and set(decision.scope_row_keys) == commanded_set
            for decision in state.decisions
        )
        if not apply_ok:
            return False
        by_key = {row.row_key: row for row in state.rows or ()}
        prior_outcomes: set[str | None] = set()
        for row_key in commanded:
            row = by_key.get(row_key)
            # Every commanded row must still be approved. A fresh apply covers
            # rows with no outcome; a recovery apply covers rows whose prior
            # outcome is failed. Never mix the two modes, and never redispatch
            # a row that already applied.
            if row is None or row.stance is not RowStance.WILL_APPLY:
                return False
            if row.apply_outcome not in (None, Values.ROW_OUTCOME_FAILED):
                return False
            prior_outcomes.add(row.apply_outcome)
        return len(prior_outcomes) == 1

    def _build_rowset_request(
        self,
        *,
        command: RuntimeStageCommitCommand,
        state: StagedWriteState,
        row: StagedRow,
    ) -> StageCommitRequest:
        """Build the connector request for ONE row — ``row_args`` verbatim (FR-C3).

        ``target_args`` dispatches unchanged, which is only safe because the row
        reached here through :meth:`StagedWriteState.row_send_plan`: every key in
        this dict has a matching ``sends`` entry that the reviewer saw.
        """

        return StageCommitRequest(
            org_id=command.org_id,
            user_id=command.user_id,
            run_id=command.run_id,
            conversation_id=command.conversation_id,
            stage_id=command.stage_id,
            rev=command.rev,
            decision_seq=command.decision_seq,
            target_connector=state.target_connector,
            target_op=state.target_op,
            body="",
            row_key=row.row_key,
            row_args=dict(row.target_args),
        )

    @staticmethod
    def _aggregate_result(*, applied: int, failed: int) -> str:
        """Aggregate per-row outcomes into the ``write.applied.result`` value."""

        if failed == 0:
            return Values.RESULT_APPLIED
        if applied == 0:
            return Values.RESULT_FAILED
        return Values.RESULT_PARTIAL

    async def _emit_rowset_applied(
        self,
        *,
        run: object,
        command: RuntimeStageCommitCommand,
        state: StagedWriteState,
        result: str,
        row_keys: tuple[str, ...],
        row_results: list[dict[str, object]],
    ) -> None:
        """Emit the single terminal ``write.applied`` for a row-set apply."""

        actor = self._apply_actor(state, command)
        receipt_ref = self._receipt_ref(command)
        payload: dict[str, object] = {
            Keys.Field.RESULT: result,
            Keys.Field.ROW_KEYS: list(row_keys),
            Keys.Field.ROW_RESULTS: row_results,
            Keys.Field.DECIDED_BY: {
                Keys.Field.ACTOR: actor,
                Keys.Field.DECISION_SEQ: command.decision_seq,
            },
        }
        if result != Values.RESULT_FAILED:
            payload[Keys.Field.CONNECTOR_RECEIPT_REF] = receipt_ref
        await self._emit_write_applied(
            run=run,
            command=command,
            payload=payload,
            summary=Messages.ROWSET_APPLIED,
        )

    @staticmethod
    def _apply_actor(
        state: StagedWriteState, command: RuntimeStageCommitCommand
    ) -> str:
        """Return the apply decision's actor (``user`` or ``policy``)."""

        for decision in state.decisions:
            if decision.apply and decision.sequence_no == command.decision_seq:
                return decision.actor or Values.DECIDED_BY_ACTOR_USER
        return Values.DECIDED_BY_ACTOR_USER

    # -- fold + gate ---------------------------------------------------------

    async def _fold_stage(
        self, command: RuntimeStageCommitCommand
    ) -> StagedWriteState | None:
        """Fold the run's ledger and return the command's stage state, or ``None``."""

        events = await self.event_store.list_events_after(
            org_id=command.org_id, run_id=command.run_id, after_sequence=0
        )
        states = StagedWriteFold.fold(events)
        return states.get(command.stage_id)

    @staticmethod
    def _approval_gate(
        state: StagedWriteState, command: RuntimeStageCommitCommand
    ) -> bool:
        """Return whether the folded state authorizes EXACTLY this command.

        Fail-closed: the stage must be ``APPROVED`` on ``command.rev`` AND the
        approving ``decision.recorded{approve}`` must sit at ``command.decision_seq``
        (the exact event that produced this command). This is what makes the
        engine execute ONLY approved revisions — nothing else can reach ``commit``.
        """

        if state.status is not StagedWriteStatus.APPROVED:
            return False
        if state.approved_rev != command.rev:
            return False
        return any(
            decision.decision == Values.DECISION_APPROVE
            and decision.scope_rev == command.rev
            and decision.sequence_no == command.decision_seq
            for decision in state.decisions
        )

    # -- draft resolution + precondition ------------------------------------

    async def _resolve_pinned_draft(
        self, *, org_id: str, state: StagedWriteState, rev: int
    ) -> DraftRecord | None:
        """Resolve the approved rev's draft snapshot (the verbatim body to send)."""

        if self._draft_store is None:
            return None
        revision = next((r for r in state.revisions if r.rev == rev), None)
        if revision is None:
            return None
        parsed = DraftRef.parse_proposal(revision.proposal_ref)
        if parsed is None:
            return None
        draft_id, version = parsed
        return await self._draft_store.get_version(
            org_id=org_id, draft_id=draft_id, version=version
        )

    async def _draft_still_pending(self, record: DraftRecord) -> bool:
        """Return whether the draft is STILL ``send_pending_approval`` (no drift).

        Checks the CURRENT latest version — an out-of-band edit/discard inserts a
        newer version with a different status, which this catches as drift.
        """

        if self._draft_store is None:
            return False
        latest = await self._draft_store.latest(
            org_id=record.org_id, draft_id=record.draft_id
        )
        return latest is not None and latest.status is DraftStatus.SEND_PENDING_APPROVAL

    def _build_request(
        self,
        *,
        command: RuntimeStageCommitCommand,
        record: DraftRecord,
        state: StagedWriteState,
    ) -> StageCommitRequest:
        """Build the connector request — ``body`` is the approved rev verbatim (FR-C3)."""

        return StageCommitRequest(
            org_id=command.org_id,
            user_id=command.user_id,
            run_id=command.run_id,
            conversation_id=command.conversation_id,
            stage_id=command.stage_id,
            rev=command.rev,
            decision_seq=command.decision_seq,
            target_connector=state.target_connector,
            target_op=state.target_op,
            body=record.content_text,
            title=record.title,
            target_metadata=dict(record.target_metadata or {}),
        )

    async def _dispatch(
        self,
        *,
        run: object,
        request: StageCommitRequest,
        state: StagedWriteState,
        command: RuntimeStageCommitCommand,
    ) -> EffectDispatchResult | None:
        """Enter the shared dispatcher using the exact folded revision only."""

        if self._dispatcher_factory is None or not isinstance(run, RunRecord):
            _LOGGER.error(
                "stage_commit.dispatch_unavailable stage_id=%s run_id=%s",
                command.stage_id,
                command.run_id,
            )
            return None
        revision = next(
            (item for item in state.revisions if item.rev == command.rev),
            None,
        )
        if revision is None:
            _LOGGER.warning(
                "stage_commit.revision_unavailable stage_id=%s rev=%s",
                command.stage_id,
                command.rev,
            )
            return None
        try:
            actor = EffectActor(self._apply_actor(state, command))
        except ValueError:
            _LOGGER.warning(
                "stage_commit.actor_invalid stage_id=%s decision_seq=%s",
                command.stage_id,
                command.decision_seq,
            )
            return None
        dispatcher: StagedWriteEffectDispatcher = self._dispatcher_factory.for_run(
            run=run
        )
        return await dispatcher.dispatch(
            request=request,
            proposal_ref=revision.proposal_ref,
            actor=actor,
        )

    # -- emission + draft flip ----------------------------------------------

    async def _on_committed(
        self,
        *,
        run: object,
        command: RuntimeStageCommitCommand,
        record: DraftRecord,
    ) -> None:
        """Flip the draft to SENT, emit ``write.applied{applied}``, audit committed."""

        await self._flip_draft_sent(run=run, record=record)
        receipt_ref = self._receipt_ref(command)
        await self._emit_write_applied(
            run=run,
            command=command,
            payload={
                Keys.Field.RESULT: Values.RESULT_APPLIED,
                Keys.Field.CONNECTOR_RECEIPT_REF: receipt_ref,
                Keys.Field.DECIDED_BY: {
                    Keys.Field.ACTOR: Values.DECIDED_BY_ACTOR_USER,
                    Keys.Field.DECISION_SEQ: command.decision_seq,
                },
            },
            summary=Messages.APPLIED_TITLE,
        )
        await self._write_audit(
            run=run,
            command=command,
            action=_AUDIT_COMMITTED,
            metadata={
                Keys.Field.RESULT: Values.RESULT_APPLIED,
                Keys.Field.CONNECTOR_RECEIPT_REF: receipt_ref,
                "target_connector": record.target_connector,
            },
        )

    async def _emit_failed(
        self,
        *,
        run: object,
        command: RuntimeStageCommitCommand,
        failure_code: str,
        audit_action: str,
        detail: str | None = None,
        row_keys: tuple[str, ...] | None = None,
    ) -> None:
        """Emit ``write.applied{failed, failure{code, detail?}}`` + the audit row.

        The draft status is left untouched, so a fresh approve can retry.
        ``detail`` is a safe public sentence (never connector output) and rides
        the ``failure`` block the projector already allow-lists, so a refusal
        that names one row reaches the reader instead of only the log.
        ``row_keys`` scopes a row-set refusal to the set that did not send.
        """

        failure: dict[str, object] = {Keys.Field.CODE: failure_code}
        if detail is not None:
            failure[Keys.Field.DETAIL] = detail
        payload: dict[str, object] = {
            Keys.Field.RESULT: Values.RESULT_FAILED,
            Keys.Field.FAILURE: failure,
        }
        if row_keys is not None:
            payload[Keys.Field.ROW_KEYS] = list(row_keys)
        await self._emit_write_applied(
            run=run,
            command=command,
            payload=payload,
            summary=Messages.FAILED_TITLE,
        )
        metadata: dict[str, object] = {
            Keys.Field.RESULT: Values.RESULT_FAILED,
            Keys.Field.CODE: failure_code,
        }
        if detail is not None:
            metadata[Keys.Field.DETAIL] = detail
        await self._write_audit(
            run=run,
            command=command,
            action=audit_action,
            metadata=metadata,
        )

    async def _flip_draft_sent(self, *, run: object, record: DraftRecord) -> None:
        """Insert a new SENT version of the draft. ``OptimisticConflict`` ⇒ log + continue.

        The send already happened by this point, so a lost flip race must NEVER
        suppress the ``write.applied{applied}`` event — it is only logged.
        """

        if self._draft_store is None:
            return
        latest = await self._draft_store.latest(
            org_id=record.org_id, draft_id=record.draft_id
        )
        run_user_id = getattr(run, "user_id", None)
        if (
            latest is None
            or latest.user_id != run_user_id
            or latest.status is not DraftStatus.SEND_PENDING_APPROVAL
        ):
            return
        next_record = latest.model_copy(
            update={
                "id": uuid4().hex,
                "version": latest.version + 1,
                "status": DraftStatus.SENT,
                "created_at": datetime.now(timezone.utc),
            }
        )
        try:
            await self._draft_store.insert_version(next_record)
        except (OptimisticConflict, KeyError):
            _LOGGER.warning(
                "stage_commit.draft_flip_conflict draft_id=%s — send already applied",
                record.draft_id,
            )

    async def _emit_write_applied(
        self,
        *,
        run: object,
        command: RuntimeStageCommitCommand,
        payload: dict[str, object],
        summary: str,
    ) -> None:
        """Append the ``write.applied`` ledger event (the SOLE producer; v:1, SYSTEM)."""

        body: dict[str, object] = {
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.STAGE_ID: command.stage_id,
            Keys.Field.REV: command.rev,
        }
        body.update(payload)
        await self.event_producer.append_api_event(
            run=run,  # type: ignore[arg-type]
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType(LedgerEventType.WRITE_APPLIED.value),
            payload=body,
            summary=summary,
            status=ApiValues.Status.COMPLETED,
        )

    async def _write_audit(
        self,
        *,
        run: object,
        command: RuntimeStageCommitCommand,
        action: str,
        metadata: dict[str, object],
    ) -> None:
        """Append one audit row through the duck-typed ``write_audit_log`` port."""

        write_audit = getattr(self.persistence, "write_audit_log", None)
        if write_audit is None:
            return
        record: dict[str, object] = {
            "org_id": command.org_id,
            "user_id": command.user_id,
            "resource_type": "stage",
            "resource_id": command.stage_id,
            "run_id": command.run_id,
            "outcome": "success",
            "metadata": dict(metadata),
        }
        await write_audit(event_type=action, record=record)

    @staticmethod
    def _receipt_ref(command: RuntimeStageCommitCommand) -> str:
        """``commit://<stage_id>/<decision_seq>`` — resolves to the persisted result."""

        return f"{Values.COMMIT_REF_PREFIX}{command.stage_id}/{command.decision_seq}"


__all__ = ["RuntimeStageCommitHandler"]
