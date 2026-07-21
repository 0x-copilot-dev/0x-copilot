"""Gated commit executor for approved (optionally edited) surface proposals (PRD-09b).

This module is the **action-safety core** of the edit-and-commit journey: the
only place a reviewer-approved proposal turns into a real external side effect
(send an email draft, write a record field). Every side effect is guarded by
four fail-closed invariants, applied in order on :meth:`SurfaceCommitExecutor.commit`:

1. **Server-side merge.** The reviewer's edits are *deltas*
   (:class:`SurfaceEdits` — ``body`` / ``fields`` / ``accepted_hunk_ids``), never
   a pre-merged artifact. :class:`SurfaceEditMerger` re-derives the final payload
   ``proposal ⊕ edits`` from the server-held :class:`CommitProposal`; the
   connector, tool, run scope, and identity always come from the server base and
   can never be injected through the edit deltas (``SurfaceEdits`` forbids extra
   keys, and unknown ``fields`` keys are rejected).
2. **Idempotency.** The commit is keyed by ``approval_id``. A ledger claim is
   written *before* the side-effecting call (check-then-act), so a retry or
   crash-replay can never double-send: a replay observes the existing claim and
   performs zero additional connector calls. This is deliberately at-most-once —
   a crash between claim and completion means the send does not fire again,
   which is the fail-closed choice for irreversible actions.
3. **Precondition re-check.** When the proposal captured a remote precondition
   (draft version / record fingerprint), the connector re-reads it at commit
   time. On drift the commit aborts with **no write**, emits a re-propose event,
   and marks the approval superseded.
4. **Audit.** The commit (or the drift-abort) is appended to the audit path via
   the existing ``write_audit_log`` mechanism.

The module is pure domain: it depends only on ``agent_runtime`` contracts and
stdlib. Concrete event/audit emission and the connector are injected through the
Protocol ports below, so nothing real ever fires under test — fakes assert that
the committed tool-call arguments carry the edited values and that no side
effect happens on drift, replay, or a missing approval.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field

from agent_runtime.execution.contracts import (
    JsonObject,
    RuntimeContract,
    RuntimeErrorCode,
)
from agent_runtime.execution.errors import AgentRuntimeError


class CommitKind(StrEnum):
    """Which underlying tool call a commit drives.

    ``DRAFT_SEND`` sends a persisted draft through its target connector.
    ``FIELD_WRITE`` writes edited record fields back through the originating MCP
    tool. Both flow through the same executor; the connector interprets ``kind``.
    """

    DRAFT_SEND = "draft_send"
    FIELD_WRITE = "field_write"


class SurfaceEdits(RuntimeContract):
    """Reviewer edit deltas applied server-side onto a pending proposal.

    Byte-mirror of the frozen api-types contract (PRD-09a):
    ``{ fields?: Record<string,string>, body?: string, accepted_hunk_ids?: string[] }``.

    The client sends **deltas only** — never a merged final artifact. ``extra``
    is forbidden by :class:`RuntimeContract`, so a client cannot smuggle a
    ``target_connector`` / ``tool_name`` / ``run_id`` override in through the
    edit object. The server re-derives the final payload from its own held
    proposal (see :class:`SurfaceEditMerger`).
    """

    fields: dict[str, str] | None = None
    body: str | None = None
    accepted_hunk_ids: tuple[str, ...] | None = None

    def is_empty(self) -> bool:
        """Return whether the reviewer supplied no actual edit (equivalent to a plain approve)."""

        return not self.fields and self.body is None and not self.accepted_hunk_ids


class RemoteState(RuntimeContract):
    """Opaque precondition token captured at propose-time and re-read at commit-time.

    Equality is structural: any difference between the captured token and the
    freshly re-read token is drift. ``version`` fits draft-send (monotonic draft
    version); ``fingerprint`` fits record writes (a hash of the captured fields).
    A connector may populate either or both.
    """

    version: int | None = None
    fingerprint: str | None = None


class CommitProposal(RuntimeContract):
    """The server-held proposal that a reviewer approved, resolved from the approval record.

    Every authority-bearing field (connector, tool, run/tenant scope) lives here
    and comes from the server, not the client. The reviewer's edits may only
    replace ``base_body`` and the ``editable_fields`` subset of ``base_fields``.
    """

    approval_id: str = Field(min_length=1)
    org_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    conversation_id: str = ""
    user_id: str = ""
    kind: CommitKind
    target_connector: str = Field(min_length=1)
    tool_name: str | None = None
    base_body: str | None = None
    base_fields: dict[str, str] = Field(default_factory=dict)
    # Keys the reviewer is permitted to override via ``SurfaceEdits.fields``.
    # Any ``edits.fields`` key outside this set is rejected as an unknown edit.
    editable_fields: frozenset[str] = frozenset()
    target_metadata: JsonObject = Field(default_factory=dict)
    # Captured remote state at propose time; ``None`` means the connector does
    # not support precondition reads for this resource (drift check is skipped).
    precondition: RemoteState | None = None
    summary: str = ""


class CommitRequest(RuntimeContract):
    """The server-derived final payload (``proposal ⊕ edits``) the connector executes.

    This is the *only* object the connector sees. Its ``body`` / ``fields`` carry
    the merged (edited) values; a fake connector asserts against them to prove the
    reviewer's edits reached the committed tool call.
    """

    approval_id: str
    org_id: str
    run_id: str
    conversation_id: str = ""
    user_id: str = ""
    kind: CommitKind
    target_connector: str
    tool_name: str | None = None
    body: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)
    accepted_hunk_ids: tuple[str, ...] = ()
    target_metadata: JsonObject = Field(default_factory=dict)

    def tool_arguments(self) -> JsonObject:
        """Return the concrete argument bag for the underlying tool call.

        Body and fields are the edited values; ``target_metadata`` (recipient,
        channel, etc.) rides through from the server-held proposal. Used for
        auditing and by connectors that dispatch a generic MCP tool.
        """

        args: JsonObject = {}
        if self.body is not None:
            args["body"] = self.body
        if self.fields:
            args["fields"] = dict(self.fields)
        if self.target_metadata:
            args["target_metadata"] = dict(self.target_metadata)
        return args


class ConnectorCommitResult(RuntimeContract):
    """Outcome of the connector's side-effecting call."""

    status: str = "sent"
    external_ref: str | None = None
    detail: JsonObject = Field(default_factory=dict)


class CommitStatus(StrEnum):
    """Terminal disposition of a commit attempt."""

    COMMITTED = "committed"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    SUPERSEDED = "superseded"


class CommitOutcome(RuntimeContract):
    """What the executor did. ``result`` is populated on COMMITTED / replay-of-committed."""

    status: CommitStatus
    approval_id: str
    result: ConnectorCommitResult | None = None
    remote_state: RemoteState | None = None


class CommitLedgerEntry(RuntimeContract):
    """One idempotency ledger row keyed by ``approval_id``."""

    approval_id: str
    committed: bool = False
    result: ConnectorCommitResult | None = None


# --------------------------------------------------------------------------- #
# Ports (dependency inversion). Fakes implement these under test; production
# adapters bind them to the real connector / event producer / persistence.
# --------------------------------------------------------------------------- #


@runtime_checkable
class SurfaceCommitConnector(Protocol):
    """The side-effecting boundary. The ONLY object that touches an external system."""

    async def read_remote_state(self, request: CommitRequest) -> RemoteState | None:
        """Re-read the remote resource's precondition token, or ``None`` if unsupported."""

    async def execute(self, request: CommitRequest) -> ConnectorCommitResult:
        """Perform the underlying tool call (draft send / field write) and return its result."""


@runtime_checkable
class CommitLedgerPort(Protocol):
    """Idempotency ledger keyed by ``approval_id``; ``claim`` is atomic check-then-act."""

    async def load(self, *, approval_id: str) -> CommitLedgerEntry | None:
        """Return the ledger row for ``approval_id``, or ``None`` if never claimed."""

    async def claim(self, *, approval_id: str) -> bool:
        """Atomically create a claim row. Return ``True`` iff this caller created it.

        A ``False`` return means another attempt already claimed this
        ``approval_id`` — the caller MUST NOT perform the side effect.
        """

    async def complete(
        self, *, approval_id: str, result: ConnectorCommitResult
    ) -> None:
        """Stamp the claim as committed and store the connector result."""


@runtime_checkable
class CommitEventSink(Protocol):
    """Emits the runtime events a commit produces (``tool_result`` + terminal + re-propose)."""

    async def tool_result(
        self, *, request: CommitRequest, result: ConnectorCommitResult
    ) -> None:
        """Emit a ``tool_result`` event for the executed underlying tool call."""

    async def committed(
        self, *, request: CommitRequest, result: ConnectorCommitResult
    ) -> None:
        """Emit the terminal approval/commit event."""

    async def re_propose(
        self, *, proposal: CommitProposal, remote_state: RemoteState
    ) -> None:
        """Emit a re-propose event after a precondition-drift abort."""

    async def superseded(
        self, *, proposal: CommitProposal, remote_state: RemoteState
    ) -> None:
        """Mark the drifted approval superseded (no write happened)."""


@runtime_checkable
class CommitAuditSink(Protocol):
    """Appends commit / drift-abort records to the audit path."""

    async def record(
        self, *, action: str, proposal: CommitProposal, metadata: JsonObject
    ) -> None:
        """Write one audit record for a commit-lifecycle event."""


# Resolves a stored approval record into a :class:`CommitProposal`. Kept as a
# callable so the executor stays free of any store/connector-specific imports.
CommitProposalResolver = Callable[[object], Awaitable[CommitProposal]]


class SurfaceEditMerger:
    """Derives the final :class:`CommitRequest` = ``proposal ⊕ edits``, server-side.

    Never trusts a client-sent merged artifact: the base is always the
    server-held proposal, and only whitelisted edit keys are applied. Unknown
    ``fields`` keys (not in ``proposal.editable_fields``) are rejected with a
    typed domain error carrying a safe message.
    """

    _UNKNOWN_FIELDS_MESSAGE = (
        "One or more edited fields are not editable for this approval."
    )

    @classmethod
    def merge(
        cls, proposal: CommitProposal, edits: SurfaceEdits | None
    ) -> CommitRequest:
        """Apply ``edits`` onto ``proposal`` and return the final commit request."""

        body = proposal.base_body
        fields = dict(proposal.base_fields)
        accepted_hunk_ids: tuple[str, ...] = ()
        if edits is not None:
            cls._reject_unknown_fields(proposal=proposal, edits=edits)
            if edits.body is not None:
                body = edits.body
            if edits.fields:
                fields.update(edits.fields)
            if edits.accepted_hunk_ids is not None:
                accepted_hunk_ids = tuple(edits.accepted_hunk_ids)
        return CommitRequest(
            approval_id=proposal.approval_id,
            org_id=proposal.org_id,
            run_id=proposal.run_id,
            conversation_id=proposal.conversation_id,
            user_id=proposal.user_id,
            kind=proposal.kind,
            target_connector=proposal.target_connector,
            tool_name=proposal.tool_name,
            body=body,
            fields=fields,
            accepted_hunk_ids=accepted_hunk_ids,
            target_metadata=dict(proposal.target_metadata),
        )

    @classmethod
    def _reject_unknown_fields(
        cls, *, proposal: CommitProposal, edits: SurfaceEdits
    ) -> None:
        """Raise when the reviewer tried to edit a field outside the editable allowlist."""

        if not edits.fields:
            return
        unknown = set(edits.fields.keys()) - set(proposal.editable_fields)
        if unknown:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                cls._UNKNOWN_FIELDS_MESSAGE,
                retryable=False,
            )


class InMemoryCommitLedger:
    """Process-local idempotency ledger with an ``asyncio.Lock`` around the claim.

    Suitable for the in-process worker and every test. A production multi-worker
    deployment injects a store-backed ledger with the same contract (the claim
    row is written under the same transactional guarantee the batch primitive
    already relies on).
    """

    def __init__(self) -> None:
        self._entries: dict[str, CommitLedgerEntry] = {}
        self._lock = asyncio.Lock()

    async def load(self, *, approval_id: str) -> CommitLedgerEntry | None:
        return self._entries.get(approval_id)

    async def claim(self, *, approval_id: str) -> bool:
        async with self._lock:
            if approval_id in self._entries:
                return False
            self._entries[approval_id] = CommitLedgerEntry(approval_id=approval_id)
            return True

    async def complete(
        self, *, approval_id: str, result: ConnectorCommitResult
    ) -> None:
        async with self._lock:
            self._entries[approval_id] = CommitLedgerEntry(
                approval_id=approval_id, committed=True, result=result
            )


class PersistenceCommitAuditSink:
    """Audit sink that appends commit records through an existing ``write_audit_log`` port.

    Duck-typed on ``persistence`` (any object exposing
    ``async write_audit_log(*, event_type, record)``) so it works against the
    in-memory store, the postgres adapter, and the worker's persistence port
    without a new cross-service channel.
    """

    _OUTCOME_SUCCESS = "success"

    def __init__(self, persistence: object) -> None:
        self._persistence = persistence

    async def record(
        self, *, action: str, proposal: CommitProposal, metadata: JsonObject
    ) -> None:
        write_audit = getattr(self._persistence, "write_audit_log", None)
        if write_audit is None:
            return
        await write_audit(
            event_type=action,
            record={
                "org_id": proposal.org_id,
                "user_id": proposal.user_id,
                "resource_type": "approval",
                "resource_id": proposal.approval_id,
                "run_id": proposal.run_id,
                "outcome": self._OUTCOME_SUCCESS,
                "metadata": dict(metadata),
            },
        )


class SurfaceCommitExecutor:
    """Executes an approved (optionally edited) proposal behind the fail-closed gate.

    Injected with a connector (the only side-effecting boundary), an idempotency
    ledger, and optional event/audit sinks. See the module docstring for the
    ordered invariants.
    """

    # Stable audit action strings written through the audit sink.
    AUDIT_COMMITTED = "surface.commit.committed"
    AUDIT_ABORTED_DRIFT = "surface.commit.aborted_precondition_drift"

    _NO_PROPOSAL_MESSAGE = (
        "Commit requires an approved proposal; refusing to perform any side effect."
    )
    _NO_APPROVAL_MESSAGE = (
        "Commit requires a stored approval record; refusing to perform any side effect."
    )

    class _AuditKeys:
        EDITED = "edited"
        EDITED_FIELDS = "edited_fields"
        BODY_EDITED = "body_edited"
        ACCEPTED_HUNK_IDS = "accepted_hunk_ids"
        TARGET_CONNECTOR = "target_connector"
        TOOL_NAME = "tool_name"
        KIND = "kind"
        STATUS = "status"
        EXTERNAL_REF = "external_ref"
        CAPTURED_VERSION = "captured_precondition"
        REMOTE_VERSION = "remote_precondition"

    def __init__(
        self,
        *,
        connector: SurfaceCommitConnector,
        ledger: CommitLedgerPort,
        events: CommitEventSink | None = None,
        audit: CommitAuditSink | None = None,
    ) -> None:
        self._connector = connector
        self._ledger = ledger
        self._events = events
        self._audit = audit

    async def commit(
        self,
        *,
        proposal: CommitProposal | None,
        edits: SurfaceEdits | None = None,
    ) -> CommitOutcome:
        """Commit an approved proposal. Fail-closed on missing proposal, drift, or replay."""

        # Fail-closed: no proposal (⇒ no approval was resolved) ⇒ no side effect.
        if proposal is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                self._NO_PROPOSAL_MESSAGE,
                retryable=False,
            )

        # (1) Server-side merge — re-derive the final payload from the server base.
        request = SurfaceEditMerger.merge(proposal, edits)

        # (2a) Idempotency CHECK — a prior claim short-circuits before any connector
        # call, so a retry/crash-replay performs zero additional side effects.
        existing = await self._ledger.load(approval_id=proposal.approval_id)
        if existing is not None:
            return CommitOutcome(
                status=CommitStatus.IDEMPOTENT_REPLAY,
                approval_id=proposal.approval_id,
                result=existing.result,
            )

        # (3) Precondition re-check — re-read remote state; drift ⇒ abort, no write.
        if proposal.precondition is not None:
            remote = await self._connector.read_remote_state(request)
            if remote is not None and remote != proposal.precondition:
                await self._abort_on_drift(proposal=proposal, remote=remote)
                return CommitOutcome(
                    status=CommitStatus.SUPERSEDED,
                    approval_id=proposal.approval_id,
                    remote_state=remote,
                )

        # (2b) Idempotency ACT — claim BEFORE the side-effecting call. A lost race
        # (concurrent worker won) also short-circuits without a second send.
        won = await self._ledger.claim(approval_id=proposal.approval_id)
        if not won:
            replayed = await self._ledger.load(approval_id=proposal.approval_id)
            return CommitOutcome(
                status=CommitStatus.IDEMPOTENT_REPLAY,
                approval_id=proposal.approval_id,
                result=replayed.result if replayed is not None else None,
            )

        # (4) Execute the underlying tool call — the single side effect.
        result = await self._connector.execute(request)
        await self._ledger.complete(approval_id=proposal.approval_id, result=result)

        # (5) Emit tool_result + terminal commit events.
        if self._events is not None:
            await self._events.tool_result(request=request, result=result)
            await self._events.committed(request=request, result=result)

        # (6) Audit the commit through the existing audit path.
        if self._audit is not None:
            await self._audit.record(
                action=self.AUDIT_COMMITTED,
                proposal=proposal,
                metadata=self._commit_audit_metadata(
                    proposal=proposal, request=request, edits=edits, result=result
                ),
            )
        return CommitOutcome(
            status=CommitStatus.COMMITTED,
            approval_id=proposal.approval_id,
            result=result,
        )

    async def commit_for_approval(
        self,
        *,
        approval: object | None,
        proposal_resolver: CommitProposalResolver,
        edits: SurfaceEdits | None = None,
    ) -> CommitOutcome:
        """Resolve a stored approval into a proposal and commit it.

        Fail-closed: a ``None`` approval (no stored record) raises before any
        proposal is built or connector touched — there is no commit path without
        an approval.
        """

        if approval is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                self._NO_APPROVAL_MESSAGE,
                retryable=False,
            )
        proposal = await proposal_resolver(approval)
        return await self.commit(proposal=proposal, edits=edits)

    async def _abort_on_drift(
        self, *, proposal: CommitProposal, remote: RemoteState
    ) -> None:
        """Emit re-propose + supersede + audit for a precondition-drift abort (no write)."""

        if self._events is not None:
            await self._events.re_propose(proposal=proposal, remote_state=remote)
            await self._events.superseded(proposal=proposal, remote_state=remote)
        if self._audit is not None:
            await self._audit.record(
                action=self.AUDIT_ABORTED_DRIFT,
                proposal=proposal,
                metadata={
                    self._AuditKeys.KIND: proposal.kind.value,
                    self._AuditKeys.TARGET_CONNECTOR: proposal.target_connector,
                    self._AuditKeys.CAPTURED_VERSION: (
                        proposal.precondition.model_dump(mode="json")
                        if proposal.precondition is not None
                        else None
                    ),
                    self._AuditKeys.REMOTE_VERSION: remote.model_dump(mode="json"),
                },
            )

    @classmethod
    def _commit_audit_metadata(
        cls,
        *,
        proposal: CommitProposal,
        request: CommitRequest,
        edits: SurfaceEdits | None,
        result: ConnectorCommitResult,
    ) -> JsonObject:
        """Build the audit metadata for a successful commit (records what was edited)."""

        edited = edits is not None and not edits.is_empty()
        return {
            cls._AuditKeys.KIND: proposal.kind.value,
            cls._AuditKeys.TARGET_CONNECTOR: proposal.target_connector,
            cls._AuditKeys.TOOL_NAME: proposal.tool_name,
            cls._AuditKeys.EDITED: edited,
            cls._AuditKeys.BODY_EDITED: bool(
                edits is not None and edits.body is not None
            ),
            cls._AuditKeys.EDITED_FIELDS: sorted((edits.fields or {}).keys())
            if edits is not None
            else [],
            cls._AuditKeys.ACCEPTED_HUNK_IDS: list(request.accepted_hunk_ids),
            cls._AuditKeys.STATUS: result.status,
            cls._AuditKeys.EXTERNAL_REF: result.external_ref,
        }
