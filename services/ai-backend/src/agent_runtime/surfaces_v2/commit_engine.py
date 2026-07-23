"""CommitEngine — execute EXACTLY the approved revision, and nothing else (PRD-D2).

This is the **action-safety core** of the v2 staged-write pipeline: the only
place a reviewer-approved revision turns into a real external side effect. It is
the single legitimate producer path behind ``write.applied`` (the worker handler
in ``runtime_worker/handlers/stage_commit.py`` owns the emission; this engine
owns the ordered fail-closed invariants around the side effect).

Every side effect is guarded by four fail-closed invariants, applied in order in
:meth:`CommitEngine.commit` (ported one-to-one from the v1 island's
``SurfaceCommitExecutor.commit``):

1. **Replay check.** ``ledger.load(commit_key)``: a committed entry short-circuits
   to ``IDEMPOTENT_REPLAY`` with **zero** connector calls; a claimed-but-incomplete
   entry (a prior attempt crashed mid-send) yields ``INDETERMINATE`` — at-most-once
   forbids resending — and stamps the row complete so this branch fires once.
2. **Precondition re-check.** When a precondition was captured, re-read the remote
   token; structural drift ⇒ ``DRIFT_ABORTED`` with **no claim, no write**.
3. **Claim before side effect.** ``ledger.claim(commit_key)`` is an atomic
   check-then-act written *before* the connector call, so a retry / crash-replay
   can never double-send. A lost race short-circuits to ``IDEMPOTENT_REPLAY``.
4. **Execute + complete.** Dispatch exactly the approved revision; a timeout maps
   to ``INDETERMINATE`` (the send may have left the building — never resend), any
   other connector error to ``FAILED{connector_error}``; success stamps the claim
   complete and returns ``COMMITTED``.

The engine performs **no event emission and no draft mutation** — the handler owns
those. It is a pure, fully-fakeable port-driven core: it depends only on
``agent_runtime`` contracts + stdlib, so nothing real ever fires under test.

``RemoteState`` / ``ConnectorCommitResult`` are imported (model-only) from the v1
island — they are safe to share (the PRD rule); the v2 idempotency row
(:class:`StageCommitLedgerEntry`) is NEW because it is keyed by ``commit_key``
(``stage_id:rev:decision_seq``), a different identity from v1's ``approval_id``.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field, PositiveInt

from agent_runtime.capabilities.surfaces.commit import (
    ConnectorCommitResult,
    RemoteState,
)
from agent_runtime.execution.contracts import JsonObject, RuntimeContract


# ---------------------------------------------------------------------------
# Typed connector errors (the connector raises these; the engine maps them to
# outcomes — this keeps the engine pure of any MCP import).
# ---------------------------------------------------------------------------


class StageCommitConnectorError(Exception):
    """Auth / connection / client / load failure from the connector dispatch.

    Maps to a ``FAILED{connector_error}`` outcome. Carries only a safe message.
    """

    safe_message: str = "The connector could not apply the write."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.safe_message)
        if message is not None:
            self.safe_message = message


class StageCommitTimeout(StageCommitConnectorError):
    """The dispatch timed out — the send MAY have left the building.

    Maps to an ``INDETERMINATE`` outcome: at-most-once forbids resending, so the
    engine never retries this attempt.
    """

    safe_message: str = "The connector dispatch timed out; the outcome is unknown."


# ---------------------------------------------------------------------------
# Request / outcome contracts
# ---------------------------------------------------------------------------


class StageCommitRequest(RuntimeContract):
    """The server-derived payload the connector executes for ONE approved commit.

    Every authority-bearing field (connector, op, tenant scope) comes from the
    server-held stage + draft row; ``body`` is the approved revision's
    ``content_text`` verbatim (byte-equal to what the user approved — FR-C3).
    """

    org_id: str
    user_id: str
    run_id: str
    conversation_id: str
    stage_id: str
    rev: PositiveInt
    # ``sequence_no`` of the approving ``decision.recorded`` — part of the
    # idempotency identity so one approve authorizes exactly one commit attempt.
    decision_seq: int
    target_connector: str
    target_op: str
    # The approved revision's content, verbatim — this is what sends.
    body: str
    title: str = ""
    target_metadata: JsonObject = Field(default_factory=dict)
    # PRD-D3 (additive) — a single row of a bulk row-set apply. ``row_key`` is
    # ``None`` for a single-artifact (D1) commit; when set, ``row_args`` is the
    # row's ``StagedRow.target_args`` verbatim (the WYSIWYG unit that sends).
    row_key: str | None = None
    row_args: JsonObject | None = None

    def commit_key(self) -> str:
        """Idempotency identity: exactly one attempt per approve decision.

        ``stage_id:rev:decision_seq`` (single artifact); a bulk row appends
        ``:{row_key}`` so each row of one apply claims its OWN idempotency row —
        one approve authorizes exactly one commit attempt per row.
        """

        base = f"{self.stage_id}:{self.rev}:{self.decision_seq}"
        return f"{base}:{self.row_key}" if self.row_key is not None else base

    def tool_arguments(self) -> JsonObject:
        """Return the concrete argument bag for the underlying tool call.

        For a bulk row (``row_args`` set) the row's args send verbatim (FR-C3).
        Otherwise mirror of ``CommitRequest.tool_arguments`` (v1 island): ``body``
        always, ``title`` / ``target_metadata`` only when present. Copies are made
        so the connector cannot mutate the request's held metadata.
        """

        if self.row_args is not None:
            return dict(self.row_args)
        args: JsonObject = {"body": self.body}
        if self.title:
            args["title"] = self.title
        if self.target_metadata:
            args["target_metadata"] = dict(self.target_metadata)
        return args


class StageCommitStatus(StrEnum):
    """Terminal disposition of a commit attempt."""

    COMMITTED = "committed"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    DRIFT_ABORTED = "drift_aborted"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class StageCommitOutcome(RuntimeContract):
    """What the engine did. ``result`` is populated on COMMITTED / replay-of-committed."""

    status: StageCommitStatus
    commit_key: str
    result: ConnectorCommitResult | None = None
    # One of the ``write.applied.failure.code`` values, on the failed branches.
    failure_code: str | None = None


# ---------------------------------------------------------------------------
# Ports (dependency inversion). Fakes implement these under test; production
# adapters bind them to the real MCP connector / durable claim store.
# ---------------------------------------------------------------------------


@runtime_checkable
class StageCommitConnector(Protocol):
    """The side-effecting boundary. The ONLY object that touches an external system."""

    async def read_remote_state(
        self, request: StageCommitRequest
    ) -> RemoteState | None:
        """Re-read the remote precondition token, or ``None`` if unsupported.

        ``None`` in D2 (draft-send has no remote precondition source — the local
        draft-status precondition in the handler does the work; this seam exists
        for D3 field-writes).
        """

    async def execute(self, request: StageCommitRequest) -> ConnectorCommitResult:
        """Perform the underlying tool call and return its result.

        Raises :class:`StageCommitTimeout` on a dispatch timeout (⇒ INDETERMINATE)
        and :class:`StageCommitConnectorError` on any other connector failure
        (⇒ FAILED{connector_error}).
        """


class StageCommitLedgerEntry(RuntimeContract):
    """One v2 idempotency row, keyed by ``commit_key`` (NEW in D2).

    Distinct from v1's ``CommitLedgerEntry`` (keyed by ``approval_id``): the v2
    identity is ``stage_id:rev:decision_seq``, so a re-staged draft cannot share a
    claim with a prior approve of a different rev.
    """

    commit_key: str
    committed: bool = False
    result: ConnectorCommitResult | None = None


@runtime_checkable
class StageCommitLedgerPort(Protocol):
    """Idempotency ledger keyed by ``commit_key``; ``claim`` is atomic check-then-act."""

    async def load(self, *, commit_key: str) -> StageCommitLedgerEntry | None:
        """Return the row for ``commit_key``, or ``None`` if never claimed."""

    async def claim(self, *, commit_key: str) -> bool:
        """Atomically create a claim row. Return ``True`` iff this caller created it.

        A ``False`` return means another attempt already claimed this
        ``commit_key`` — the caller MUST NOT perform the side effect.
        """

    async def complete(self, *, commit_key: str, result: ConnectorCommitResult) -> None:
        """Stamp the claim committed and store the connector result."""


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


class CommitEngine:
    """Executes exactly one approved revision behind the four fail-closed invariants.

    Holds two ports and nothing else. ``commit`` is ordered one-to-one with the
    v1 island's ``SurfaceCommitExecutor.commit``; the ordering IS the safety
    property (claim strictly precedes the side effect).
    """

    # Marker result stamped on the ledger when a crashed / timed-out attempt is
    # resolved indeterminate — at-most-once forbids ever resending it.
    _INDETERMINATE_STATUS = "indeterminate"

    # Failure codes (mirror ``surfaces_v2.constants.Values`` /
    # ``ledger_models.WriteFailureCode``; redeclared here so the engine stays
    # free of transport imports).
    _CODE_CONNECTOR_ERROR = "connector_error"
    _CODE_ATTEMPT_INDETERMINATE = "attempt_indeterminate"
    _CODE_PRECONDITION_DRIFT = "precondition_drift"

    def __init__(
        self,
        connector: StageCommitConnector,
        ledger: StageCommitLedgerPort,
    ) -> None:
        self._connector = connector
        self._ledger = ledger

    async def commit(
        self,
        request: StageCommitRequest,
        *,
        captured_precondition: RemoteState | None = None,
    ) -> StageCommitOutcome:
        """Commit exactly the approved revision. Fail-closed on replay/drift/error."""

        commit_key = request.commit_key()

        # (1) Replay check — a prior attempt short-circuits BEFORE any connector
        # call, so a retry / crash-replay performs zero additional side effects.
        existing = await self._ledger.load(commit_key=commit_key)
        if existing is not None:
            if existing.committed:
                return StageCommitOutcome(
                    status=StageCommitStatus.IDEMPOTENT_REPLAY,
                    commit_key=commit_key,
                    result=existing.result,
                )
            # Claimed-but-incomplete: a prior attempt claimed then crashed mid-send.
            # At-most-once forbids resending; stamp complete with an indeterminate
            # result so THIS branch fires exactly once (a later delivery replays).
            indeterminate = ConnectorCommitResult(status=self._INDETERMINATE_STATUS)
            await self._ledger.complete(commit_key=commit_key, result=indeterminate)
            return StageCommitOutcome(
                status=StageCommitStatus.INDETERMINATE,
                commit_key=commit_key,
                failure_code=self._CODE_ATTEMPT_INDETERMINATE,
            )

        # (2) Precondition re-check — re-read remote state; structural drift ⇒
        # abort with NO claim and NO write. Skipped when nothing was captured, or
        # when the connector reports no readable remote token (D2 draft-send).
        if captured_precondition is not None:
            remote = await self._connector.read_remote_state(request)
            if remote is not None and remote != captured_precondition:
                return StageCommitOutcome(
                    status=StageCommitStatus.DRIFT_ABORTED,
                    commit_key=commit_key,
                    failure_code=self._CODE_PRECONDITION_DRIFT,
                )

        # (3) Claim BEFORE the side effect. A lost race (concurrent worker won)
        # also short-circuits without a second send.
        won = await self._ledger.claim(commit_key=commit_key)
        if not won:
            replayed = await self._ledger.load(commit_key=commit_key)
            return StageCommitOutcome(
                status=StageCommitStatus.IDEMPOTENT_REPLAY,
                commit_key=commit_key,
                result=replayed.result if replayed is not None else None,
            )

        # (4) Execute the single side effect. The claim already exists, so any
        # failure here can never be retried into a second send.
        try:
            result = await self._connector.execute(request)
        except (StageCommitTimeout, asyncio.TimeoutError, TimeoutError):
            # The send may have left the building — never resend. The claim stays
            # incomplete; a redelivery hits the claimed-but-incomplete branch (1).
            return StageCommitOutcome(
                status=StageCommitStatus.INDETERMINATE,
                commit_key=commit_key,
                failure_code=self._CODE_ATTEMPT_INDETERMINATE,
            )
        except Exception:  # noqa: BLE001 — after a claim, never re-raise (no retry).
            return StageCommitOutcome(
                status=StageCommitStatus.FAILED,
                commit_key=commit_key,
                failure_code=self._CODE_CONNECTOR_ERROR,
            )

        # (5) Complete the claim and return COMMITTED.
        await self._ledger.complete(commit_key=commit_key, result=result)
        return StageCommitOutcome(
            status=StageCommitStatus.COMMITTED,
            commit_key=commit_key,
            result=result,
        )


# ---------------------------------------------------------------------------
# In-process idempotency ledger (tests + in-memory backend; clone of the v1
# island's InMemoryCommitLedger, keyed by ``commit_key``)
# ---------------------------------------------------------------------------


class InMemoryStageCommitLedger:
    """Process-local idempotency ledger with an ``asyncio.Lock`` around the claim.

    Suitable for the in-process worker (in-memory backend) and every test. A
    durable adapter (postgres / file) is injected for the real single- and
    multi-worker topologies — a process-local claim cannot survive a restart, so
    at-most-once across a crash needs the durable row.
    """

    def __init__(self) -> None:
        self._entries: dict[str, StageCommitLedgerEntry] = {}
        self._lock = asyncio.Lock()

    async def load(self, *, commit_key: str) -> StageCommitLedgerEntry | None:
        return self._entries.get(commit_key)

    async def claim(self, *, commit_key: str) -> bool:
        async with self._lock:
            if commit_key in self._entries:
                return False
            self._entries[commit_key] = StageCommitLedgerEntry(commit_key=commit_key)
            return True

    async def complete(self, *, commit_key: str, result: ConnectorCommitResult) -> None:
        async with self._lock:
            self._entries[commit_key] = StageCommitLedgerEntry(
                commit_key=commit_key, committed=True, result=result
            )


__all__ = [
    "CommitEngine",
    "ConnectorCommitResult",
    "InMemoryStageCommitLedger",
    "RemoteState",
    "StageCommitConnector",
    "StageCommitConnectorError",
    "StageCommitLedgerEntry",
    "StageCommitLedgerPort",
    "StageCommitOutcome",
    "StageCommitRequest",
    "StageCommitStatus",
    "StageCommitTimeout",
]
