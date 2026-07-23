"""Adversarial unit tests for the PRD-D2 CommitEngine (``surfaces_v2/commit_engine.py``).

The action-safety core: FAKE connectors + ledgers assert that NOTHING real is ever
sent on a bad path (replay, drift, lost race, timeout, error) and that the claim is
written strictly BEFORE the side effect. Mirrors the v1 island's adversarial suite
(``surfaces/test_commit_executor.py``).
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.surfaces.commit import (
    ConnectorCommitResult,
    RemoteState,
)
from agent_runtime.surfaces_v2.commit_engine import (
    CommitEngine,
    InMemoryStageCommitLedger,
    StageCommitConnectorError,
    StageCommitLedgerEntry,
    StageCommitRequest,
    StageCommitStatus,
    StageCommitTimeout,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _request(**overrides: object) -> StageCommitRequest:
    base: dict[str, object] = {
        "org_id": "org_acme",
        "user_id": "user_sarah",
        "run_id": "run_1",
        "conversation_id": "conv_1",
        "stage_id": "stage_abc",
        "rev": 2,
        "decision_seq": 7,
        "target_connector": "gmail",
        "target_op": "send",
        "body": "Dear team, launch Monday.",
        "title": "Launch email",
        "target_metadata": {"to": "vip@acme.test"},
    }
    base.update(overrides)
    return StageCommitRequest(**base)  # type: ignore[arg-type]


class _SpyConnector:
    """Records every call; performs NO real side effect."""

    def __init__(
        self,
        *,
        remote_state: RemoteState | None = None,
        raise_on_execute: Exception | None = None,
    ) -> None:
        self._remote_state = remote_state
        self._raise = raise_on_execute
        self.execute_calls: list[StageCommitRequest] = []
        self.read_calls: list[StageCommitRequest] = []

    async def read_remote_state(
        self, request: StageCommitRequest
    ) -> RemoteState | None:
        self.read_calls.append(request)
        return self._remote_state

    async def execute(self, request: StageCommitRequest) -> ConnectorCommitResult:
        self.execute_calls.append(request)
        if self._raise is not None:
            raise self._raise
        return ConnectorCommitResult(
            status="sent", external_ref=f"ext-{len(self.execute_calls)}"
        )


class _OrderRecordingLedger:
    """Wraps the in-memory ledger to record the order of claim vs the connector call."""

    def __init__(self, events: list[str]) -> None:
        self._inner = InMemoryStageCommitLedger()
        self._events = events

    async def load(self, *, commit_key: str):  # noqa: ANN201
        return await self._inner.load(commit_key=commit_key)

    async def claim(self, *, commit_key: str) -> bool:
        won = await self._inner.claim(commit_key=commit_key)
        if won:
            self._events.append("claim")
        return won

    async def complete(self, *, commit_key: str, result) -> None:  # noqa: ANN001
        self._events.append("complete")
        await self._inner.complete(commit_key=commit_key, result=result)


class _OrderRecordingConnector(_SpyConnector):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._order_events = events

    async def execute(self, request: StageCommitRequest) -> ConnectorCommitResult:
        self._order_events.append("execute")
        return await super().execute(request)


class TestIdempotency:
    async def test_replay_performs_zero_additional_side_effects(self) -> None:
        connector = _SpyConnector()
        ledger = InMemoryStageCommitLedger()
        engine = CommitEngine(connector, ledger)
        request = _request()

        first = await engine.commit(request)
        assert first.status is StageCommitStatus.COMMITTED
        assert len(connector.execute_calls) == 1

        second = await engine.commit(request)
        assert second.status is StageCommitStatus.IDEMPOTENT_REPLAY
        assert len(connector.execute_calls) == 1  # unchanged — zero re-sends
        assert second.result is not None
        assert second.result.external_ref == "ext-1"

    async def test_claim_written_before_side_effect(self) -> None:
        events: list[str] = []
        engine = CommitEngine(
            _OrderRecordingConnector(events), _OrderRecordingLedger(events)
        )
        await engine.commit(_request())
        # The claim strictly precedes the connector execute — the safety ordering.
        assert events == ["claim", "execute", "complete"]

    async def test_lost_claim_race_short_circuits(self) -> None:
        # The lost-race branch (step 3): the replay load sees nothing, but the
        # claim loses to a concurrent worker that just won. The engine must NOT
        # send; it replays the winner's stored result.
        class _LostRaceLedger:
            def __init__(self) -> None:
                self._winner = ConnectorCommitResult(
                    status="sent", external_ref="ext-winner"
                )
                self._loads = 0

            async def load(self, *, commit_key: str):  # noqa: ANN202
                self._loads += 1
                # First load (step 1 replay check) sees nothing; a later load
                # (the lost-race branch) returns the winner's completed row.
                if self._loads == 1:
                    return None
                return StageCommitLedgerEntry(
                    commit_key=commit_key, committed=True, result=self._winner
                )

            async def claim(self, *, commit_key: str) -> bool:
                return False  # a concurrent worker won the claim

            async def complete(self, *, commit_key: str, result) -> None:  # noqa: ANN001
                raise AssertionError("must not complete after losing the claim")

        connector = _SpyConnector()
        engine = CommitEngine(connector, _LostRaceLedger())

        outcome = await engine.commit(_request())
        assert outcome.status is StageCommitStatus.IDEMPOTENT_REPLAY
        assert connector.execute_calls == []  # never sent — lost the claim race
        assert (
            outcome.result is not None and outcome.result.external_ref == "ext-winner"
        )

    async def test_claimed_but_incomplete_entry_yields_indeterminate_exactly_once(
        self,
    ) -> None:
        connector = _SpyConnector()
        ledger = InMemoryStageCommitLedger()
        request = _request()
        # Simulate a prior attempt that claimed then crashed BEFORE completing.
        await ledger.claim(commit_key=request.commit_key())
        engine = CommitEngine(connector, ledger)

        first = await engine.commit(request)
        assert first.status is StageCommitStatus.INDETERMINATE
        assert connector.execute_calls == []  # never resent

        # The branch stamped the row complete, so a later delivery replays inert.
        entry = await ledger.load(commit_key=request.commit_key())
        assert entry is not None and entry.committed is True
        second = await engine.commit(request)
        assert second.status is StageCommitStatus.IDEMPOTENT_REPLAY
        assert connector.execute_calls == []


class TestPrecondition:
    async def test_remote_drift_aborts_no_write_no_claim(self) -> None:
        connector = _SpyConnector(remote_state=RemoteState(version=5))
        ledger = InMemoryStageCommitLedger()
        engine = CommitEngine(connector, ledger)
        request = _request()

        outcome = await engine.commit(
            request, captured_precondition=RemoteState(version=2)
        )
        assert outcome.status is StageCommitStatus.DRIFT_ABORTED
        assert outcome.failure_code == "precondition_drift"
        assert connector.execute_calls == []  # NO write
        # NO claim was written (nothing to be idempotent about).
        assert await ledger.load(commit_key=request.commit_key()) is None

    async def test_none_remote_state_skips_check(self) -> None:
        # Connector reports no readable remote token (D2 draft-send) — even with a
        # captured precondition, a None read is not drift; the send proceeds.
        connector = _SpyConnector(remote_state=None)
        engine = CommitEngine(connector, InMemoryStageCommitLedger())

        outcome = await engine.commit(
            _request(), captured_precondition=RemoteState(version=2)
        )
        assert outcome.status is StageCommitStatus.COMMITTED
        assert len(connector.execute_calls) == 1

    async def test_no_captured_precondition_never_reads_remote(self) -> None:
        connector = _SpyConnector(remote_state=RemoteState(version=99))
        engine = CommitEngine(connector, InMemoryStageCommitLedger())

        outcome = await engine.commit(_request(), captured_precondition=None)
        assert outcome.status is StageCommitStatus.COMMITTED
        # No captured precondition ⇒ read_remote_state is never consulted.
        assert connector.read_calls == []


class TestErrorMapping:
    async def test_timeout_yields_indeterminate_never_resends(self) -> None:
        connector = _SpyConnector(raise_on_execute=StageCommitTimeout())
        ledger = InMemoryStageCommitLedger()
        engine = CommitEngine(connector, ledger)
        request = _request()

        outcome = await engine.commit(request)
        assert outcome.status is StageCommitStatus.INDETERMINATE
        assert outcome.failure_code == "attempt_indeterminate"
        assert len(connector.execute_calls) == 1
        # The claim exists but stays incomplete; a redelivery is INDETERMINATE
        # (never a second send).
        entry = await ledger.load(commit_key=request.commit_key())
        assert entry is not None and entry.committed is False
        replay = await engine.commit(request)
        assert replay.status is StageCommitStatus.INDETERMINATE
        assert len(connector.execute_calls) == 1  # still one — never resent

    async def test_connector_error_yields_failed_connector_error(self) -> None:
        connector = _SpyConnector(
            raise_on_execute=StageCommitConnectorError("auth revoked")
        )
        engine = CommitEngine(connector, InMemoryStageCommitLedger())

        outcome = await engine.commit(_request())
        assert outcome.status is StageCommitStatus.FAILED
        assert outcome.failure_code == "connector_error"

    async def test_unexpected_exception_after_claim_maps_failed_never_raises(
        self,
    ) -> None:
        connector = _SpyConnector(raise_on_execute=RuntimeError("boom"))
        engine = CommitEngine(connector, InMemoryStageCommitLedger())

        outcome = await engine.commit(_request())
        # After a claim, the engine NEVER re-raises (that would retry the command
        # into a possible second send). It maps to FAILED{connector_error}.
        assert outcome.status is StageCommitStatus.FAILED
        assert outcome.failure_code == "connector_error"

    async def test_connector_never_called_without_a_claim(self) -> None:
        # Drift path (no claim) proves the connector.execute is unreachable without
        # a claim; the claim-before-execute ordering test covers the happy path.
        connector = _SpyConnector(remote_state=RemoteState(version=5))
        ledger = InMemoryStageCommitLedger()
        engine = CommitEngine(connector, ledger)
        request = _request()

        await engine.commit(request, captured_precondition=RemoteState(version=1))
        assert connector.execute_calls == []
        assert await ledger.load(commit_key=request.commit_key()) is None


class TestCommitKey:
    def test_commit_key_is_stage_rev_decision_seq(self) -> None:
        request = _request(stage_id="s1", rev=3, decision_seq=42)
        assert request.commit_key() == "s1:3:42"

    def test_tool_arguments_carry_body_verbatim(self) -> None:
        request = _request(body="EXACT BODY", title="T", target_metadata={"to": "x"})
        args = request.tool_arguments()
        assert args["body"] == "EXACT BODY"
        assert args["title"] == "T"
        assert args["target_metadata"] == {"to": "x"}


class TestLedgerEntry:
    async def test_in_memory_ledger_claim_is_once(self) -> None:
        ledger = InMemoryStageCommitLedger()
        assert await ledger.claim(commit_key="k") is True
        assert await ledger.claim(commit_key="k") is False  # atomic check-then-act
        entry = await ledger.load(commit_key="k")
        assert isinstance(entry, StageCommitLedgerEntry)
        assert entry.committed is False
