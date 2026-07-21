"""Unit tests for the PRD-09b gated commit executor (``capabilities/surfaces/commit.py``).

This is the action-safety core: the tests use FAKE connectors and assert that
NOTHING real is ever sent on a bad path (drift, replay, missing approval) and
that reviewer edits reach the committed tool-call arguments on the good path.
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.surfaces.commit import (
    CommitKind,
    CommitOutcome,
    CommitProposal,
    CommitRequest,
    CommitStatus,
    ConnectorCommitResult,
    InMemoryCommitLedger,
    RemoteState,
    SurfaceCommitExecutor,
    SurfaceEditMerger,
    SurfaceEdits,
)
from agent_runtime.execution.errors import AgentRuntimeError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _FakeConnector:
    """Records calls; performs NO real side effect. Asserts the committed args."""

    def __init__(self, *, remote_state: RemoteState | None = None) -> None:
        self._remote_state = remote_state
        self.execute_calls: list[CommitRequest] = []
        self.read_calls: list[CommitRequest] = []

    async def read_remote_state(self, request: CommitRequest) -> RemoteState | None:
        self.read_calls.append(request)
        return self._remote_state

    async def execute(self, request: CommitRequest) -> ConnectorCommitResult:
        # NOTHING real happens here — we only record the request the executor
        # derived and return a synthetic result.
        self.execute_calls.append(request)
        return ConnectorCommitResult(
            status="sent",
            external_ref=f"ext-{len(self.execute_calls)}",
        )


class _FakeEventSink:
    def __init__(self) -> None:
        self.tool_result_calls: list[tuple[CommitRequest, ConnectorCommitResult]] = []
        self.committed_calls: list[tuple[CommitRequest, ConnectorCommitResult]] = []
        self.re_propose_calls: list[tuple[CommitProposal, RemoteState]] = []
        self.superseded_calls: list[tuple[CommitProposal, RemoteState]] = []

    async def tool_result(self, *, request, result) -> None:
        self.tool_result_calls.append((request, result))

    async def committed(self, *, request, result) -> None:
        self.committed_calls.append((request, result))

    async def re_propose(self, *, proposal, remote_state) -> None:
        self.re_propose_calls.append((proposal, remote_state))

    async def superseded(self, *, proposal, remote_state) -> None:
        self.superseded_calls.append((proposal, remote_state))


class _FakeAuditSink:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict]] = []

    async def record(self, *, action, proposal, metadata) -> None:
        self.records.append((action, dict(metadata)))


def _draft_proposal(**overrides: object) -> CommitProposal:
    base: dict[str, object] = {
        "approval_id": "appr_1",
        "org_id": "org_acme",
        "run_id": "run_1",
        "conversation_id": "conv_1",
        "user_id": "user_sarah",
        "kind": CommitKind.DRAFT_SEND,
        "target_connector": "gmail",
        "tool_name": "gmail.send",
        "base_body": "Original body.",
        "base_fields": {},
        "editable_fields": frozenset(),
        "target_metadata": {"to": "vip@acme.test", "subject": "Launch"},
        "summary": "Send Launch to gmail",
    }
    base.update(overrides)
    return CommitProposal(**base)  # type: ignore[arg-type]


def _record_proposal(**overrides: object) -> CommitProposal:
    base: dict[str, object] = {
        "approval_id": "appr_rec",
        "org_id": "org_acme",
        "run_id": "run_1",
        "kind": CommitKind.FIELD_WRITE,
        "target_connector": "linear",
        "tool_name": "linear.update_issue",
        "base_fields": {"status": "open", "priority": "low"},
        "editable_fields": frozenset({"status", "priority"}),
    }
    base.update(overrides)
    return CommitProposal(**base)  # type: ignore[arg-type]


def _executor(
    connector: _FakeConnector,
    *,
    ledger: InMemoryCommitLedger | None = None,
    events: _FakeEventSink | None = None,
    audit: _FakeAuditSink | None = None,
) -> SurfaceCommitExecutor:
    return SurfaceCommitExecutor(
        connector=connector,
        ledger=ledger or InMemoryCommitLedger(),
        events=events,
        audit=audit,
    )


class TestServerSideMerge:
    async def test_approve_with_edits_body_reaches_committed_args(self) -> None:
        connector = _FakeConnector()
        events = _FakeEventSink()
        executor = _executor(connector, events=events)

        outcome = await executor.commit(
            proposal=_draft_proposal(base_body="Original body."),
            edits=SurfaceEdits(body="Edited by reviewer."),
        )

        assert outcome.status is CommitStatus.COMMITTED
        assert len(connector.execute_calls) == 1
        committed = connector.execute_calls[0]
        # The EDITED body (not the base) is what the connector executes.
        assert committed.body == "Edited by reviewer."
        # Server-held identity/routing is untouched by the client edits.
        assert committed.target_connector == "gmail"
        assert committed.target_metadata == {"to": "vip@acme.test", "subject": "Launch"}
        # tool_result + terminal events emitted.
        assert len(events.tool_result_calls) == 1
        assert len(events.committed_calls) == 1

    async def test_approve_with_edits_field_reaches_committed_args(self) -> None:
        connector = _FakeConnector()
        executor = _executor(connector)

        outcome = await executor.commit(
            proposal=_record_proposal(),
            edits=SurfaceEdits(fields={"status": "closed"}),
        )

        assert outcome.status is CommitStatus.COMMITTED
        committed = connector.execute_calls[0]
        # Edited field overrides the base; untouched field rides through.
        assert committed.fields == {"status": "closed", "priority": "low"}
        assert committed.tool_arguments()["fields"] == {
            "status": "closed",
            "priority": "low",
        }

    async def test_plain_approve_commits_unedited_proposal(self) -> None:
        connector = _FakeConnector()
        executor = _executor(connector)

        outcome = await executor.commit(
            proposal=_draft_proposal(base_body="Original body."),
            edits=None,
        )

        assert outcome.status is CommitStatus.COMMITTED
        committed = connector.execute_calls[0]
        assert committed.body == "Original body."

    async def test_unknown_edit_field_is_rejected(self) -> None:
        connector = _FakeConnector()
        executor = _executor(connector)

        with pytest.raises(AgentRuntimeError) as exc:
            await executor.commit(
                proposal=_record_proposal(editable_fields=frozenset({"status"})),
                edits=SurfaceEdits(fields={"assignee": "mallory"}),
            )

        assert exc.value.code.value == "validation_error"
        # No side effect fired for a rejected edit.
        assert connector.execute_calls == []

    def test_merger_never_trusts_client_connector_override(self) -> None:
        # SurfaceEdits forbids extra keys — a client cannot smuggle routing.
        with pytest.raises(Exception):
            SurfaceEdits(body="x", target_connector="attacker")  # type: ignore[call-arg]
        # And the merged request always sources routing from the proposal.
        request = SurfaceEditMerger.merge(
            _draft_proposal(target_connector="gmail"),
            SurfaceEdits(body="x"),
        )
        assert request.target_connector == "gmail"


class TestIdempotency:
    async def test_replay_performs_zero_additional_side_effects(self) -> None:
        connector = _FakeConnector()
        ledger = InMemoryCommitLedger()
        executor = _executor(connector, ledger=ledger)
        proposal = _draft_proposal()

        first = await executor.commit(proposal=proposal, edits=None)
        assert first.status is CommitStatus.COMMITTED
        assert len(connector.execute_calls) == 1

        # Replay the SAME approval_id — must not send again.
        second = await executor.commit(proposal=proposal, edits=None)
        assert second.status is CommitStatus.IDEMPOTENT_REPLAY
        assert len(connector.execute_calls) == 1  # unchanged
        # Replay returns the stored result.
        assert second.result is not None
        assert second.result.external_ref == "ext-1"

    async def test_claim_written_before_side_effect(self) -> None:
        # A ledger claim exists for the approval_id after commit, proving the
        # key was stored around the side-effecting call.
        connector = _FakeConnector()
        ledger = InMemoryCommitLedger()
        executor = _executor(connector, ledger=ledger)
        proposal = _draft_proposal()

        await executor.commit(proposal=proposal, edits=None)
        entry = await ledger.load(approval_id=proposal.approval_id)
        assert entry is not None
        assert entry.committed is True


class TestPrecondition:
    async def test_drift_aborts_no_write_reproposes_and_supersedes(self) -> None:
        # Captured version 2; remote now reports version 5 → drift.
        connector = _FakeConnector(remote_state=RemoteState(version=5))
        events = _FakeEventSink()
        audit = _FakeAuditSink()
        ledger = InMemoryCommitLedger()
        executor = _executor(connector, ledger=ledger, events=events, audit=audit)
        proposal = _draft_proposal(precondition=RemoteState(version=2))

        outcome = await executor.commit(proposal=proposal, edits=None)

        assert outcome.status is CommitStatus.SUPERSEDED
        assert outcome.remote_state == RemoteState(version=5)
        # NO write happened.
        assert connector.execute_calls == []
        # Re-propose + supersede emitted.
        assert len(events.re_propose_calls) == 1
        assert len(events.superseded_calls) == 1
        assert events.re_propose_calls[0][1] == RemoteState(version=5)
        # No ledger claim was written (nothing to be idempotent about).
        assert await ledger.load(approval_id=proposal.approval_id) is None
        # Drift is audited.
        assert any(
            action == SurfaceCommitExecutor.AUDIT_ABORTED_DRIFT
            for action, _ in audit.records
        )

    async def test_precondition_match_commits(self) -> None:
        connector = _FakeConnector(remote_state=RemoteState(version=2))
        executor = _executor(connector)
        proposal = _draft_proposal(precondition=RemoteState(version=2))

        outcome = await executor.commit(proposal=proposal, edits=None)

        assert outcome.status is CommitStatus.COMMITTED
        assert len(connector.execute_calls) == 1

    async def test_no_precondition_skips_remote_read(self) -> None:
        connector = _FakeConnector(remote_state=RemoteState(version=99))
        executor = _executor(connector)
        proposal = _draft_proposal(precondition=None)

        outcome = await executor.commit(proposal=proposal, edits=None)

        assert outcome.status is CommitStatus.COMMITTED
        # No precondition ⇒ connector.read_remote_state is never consulted.
        assert connector.read_calls == []


class TestAudit:
    async def test_commit_audit_records_edit_provenance(self) -> None:
        connector = _FakeConnector()
        audit = _FakeAuditSink()
        executor = _executor(connector, audit=audit)

        await executor.commit(
            proposal=_record_proposal(),
            edits=SurfaceEdits(fields={"status": "closed"}),
        )

        actions = [action for action, _ in audit.records]
        assert SurfaceCommitExecutor.AUDIT_COMMITTED in actions
        _, metadata = next(
            row
            for row in audit.records
            if row[0] == SurfaceCommitExecutor.AUDIT_COMMITTED
        )
        assert metadata["edited"] is True
        assert metadata["edited_fields"] == ["status"]


class TestFailClosed:
    async def test_commit_without_proposal_raises_and_sends_nothing(self) -> None:
        connector = _FakeConnector()
        executor = _executor(connector)

        with pytest.raises(AgentRuntimeError):
            await executor.commit(proposal=None, edits=SurfaceEdits(body="x"))

        assert connector.execute_calls == []

    async def test_commit_for_approval_without_stored_approval_raises(self) -> None:
        connector = _FakeConnector()
        executor = _executor(connector)

        async def _resolver(_approval: object) -> CommitProposal:
            # Would build a proposal — must NOT be reached when approval is None.
            raise AssertionError("resolver should not run for a missing approval")

        with pytest.raises(AgentRuntimeError):
            await executor.commit_for_approval(
                approval=None,
                proposal_resolver=_resolver,
                edits=None,
            )

        assert connector.execute_calls == []

    async def test_commit_for_approval_happy_path(self) -> None:
        connector = _FakeConnector()
        executor = _executor(connector)
        stored_approval = object()

        async def _resolver(approval: object) -> CommitProposal:
            assert approval is stored_approval
            return _draft_proposal()

        outcome: CommitOutcome = await executor.commit_for_approval(
            approval=stored_approval,
            proposal_resolver=_resolver,
            edits=SurfaceEdits(body="Edited."),
        )
        assert outcome.status is CommitStatus.COMMITTED
        assert connector.execute_calls[0].body == "Edited."
