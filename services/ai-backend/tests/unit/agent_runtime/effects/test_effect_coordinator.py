from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import hashlib

import pytest

from agent_runtime.effects.claims import EffectClaimState
from agent_runtime.effects.contracts import EffectCommitCommand
from agent_runtime.effects.coordinator import (
    EffectCoordinator,
    EffectCoordinatorStatus,
    EffectExecutionScope,
    EffectReconcileCommand,
)
from agent_runtime.effects.executor import PreparedEffect, RecordingEffectExecutor
from agent_runtime.effects.executor_registry import EffectExecutorRegistry
from agent_runtime.effects.staging import EffectStager
from agent_runtime.surfaces_v2.entities import EffectExecutionResult
from agent_runtime.surfaces_v2.ledger_models import (
    EffectDecisionKind,
    EffectExecutorKind,
    EffectOutcome,
)
from runtime_adapters.in_memory.effect_claim_store import InMemoryEffectClaimStore
from tests.unit.agent_runtime.effects.fakes import (
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
    policy_snapshot,
    proposal,
    scope,
    user,
)


class _ScopeResolver:
    def __init__(self) -> None:
        self.scope = EffectExecutionScope(
            org_id="org_effect_test",
            user_id="user-1",
            conversation_id="conv-effect-test",
            run_id=scope().run_id,
            owner_ref=scope().owner_ref,
        )

    async def resolve(self, *, run_id: str) -> EffectExecutionScope | None:
        return self.scope if run_id == self.scope.run_id else None


class _References:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values

    def open(
        self, *, scope: EffectExecutionScope, reference: str
    ) -> AsyncIterator[bytes]:
        del scope

        async def _stream() -> AsyncIterator[bytes]:
            value = self.values[reference]
            yield value[: max(1, len(value) // 2)]
            if len(value) > 1:
                yield value[max(1, len(value) // 2) :]

        return _stream()


class _Cancelled:
    async def is_cancelled(
        self, *, scope: EffectExecutionScope, command: EffectCommitCommand
    ) -> bool:
        del scope, command
        return True


def _matching_executor(
    *,
    on_apply: Callable[[PreparedEffect], Awaitable[EffectExecutionResult]]
    | None = None,
    on_reconcile: Callable[[object], Awaitable[EffectExecutionResult]] | None = None,
) -> RecordingEffectExecutor:
    async def _on_prepare(request: object) -> PreparedEffect:
        assert hasattr(request, "stage_id")
        return PreparedEffect(
            request=request,  # type: ignore[arg-type]
            observed_precondition_digest="c" * 64,
        )

    return RecordingEffectExecutor(
        on_prepare=_on_prepare,
        on_apply=on_apply,
        on_reconcile=on_reconcile,  # type: ignore[arg-type]
    )


async def _approved_command(
    *,
    executor: EffectExecutorKind = EffectExecutorKind.BUILTIN,
) -> tuple[EffectCommitCommand, FakeLedger, _References]:
    proposal_bytes = b'{"exact":"approved proposal"}'
    target_bytes = b'{"target":"immutable target"}'
    proposed = proposal(
        executor=executor,
        proposal_digest=hashlib.sha256(proposal_bytes).hexdigest(),
        target_digest=hashlib.sha256(target_bytes).hexdigest(),
    )
    ledger = FakeLedger()
    outbox = FakeOutbox()
    stager = EffectStager(
        ledger=ledger,
        outbox=outbox,
        clock=FakeClock(),
        stage_ids=FakeStageIds(),
    )
    staged = await stager.stage(
        scope=scope(),
        proposed_effect=proposed,
        policy_snapshot=policy_snapshot(),
        actor=user(),
        idempotency_key="stage-effect-1",
    )
    await stager.decide(
        scope=scope(),
        stage_id=staged.stage_id,
        revision=staged.current_revision.revision,
        decision=EffectDecisionKind.APPROVE,
        proposal_digest=staged.current_revision.proposal_digest,
        target_digest=staged.target_digest,
        actor=user(),
        idempotency_key="decide-effect-1",
    )
    command = next(iter(outbox.commands.values()))
    return (
        command,
        ledger,
        _References(
            {
                proposed.proposal_content_ref: proposal_bytes,
                proposed.target.target_ref: target_bytes,
            }
        ),
    )


def _coordinator(
    *,
    ledger: FakeLedger,
    references: _References,
    claims: InMemoryEffectClaimStore,
    executor: RecordingEffectExecutor,
    cancellation: object | None = None,
) -> EffectCoordinator:
    registry = EffectExecutorRegistry({EffectExecutorKind.BUILTIN: lambda _: executor})
    return EffectCoordinator(
        ledger=ledger,
        claims=claims,
        scopes=_ScopeResolver(),
        references=references,
        executors=registry,
        cancellation=cancellation,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_prepare_claim_apply_complete_in_that_order() -> None:
    command, ledger, references = await _approved_command()
    claims = InMemoryEffectClaimStore()

    async def _on_apply(prepared: PreparedEffect) -> EffectExecutionResult:
        claim = await claims.get(
            org_id="org_effect_test",
            executor=EffectExecutorKind.BUILTIN,
            idempotency_key=prepared.request.idempotency_key,
        )
        assert claim is not None
        assert claim.state is EffectClaimState.CLAIMED
        return EffectExecutionResult(outcome=EffectOutcome.APPLIED, retryable=False)

    executor = _matching_executor(on_apply=_on_apply)
    result = await _coordinator(
        ledger=ledger,
        references=references,
        claims=claims,
        executor=executor,
    ).handle(command)

    assert result.status is EffectCoordinatorStatus.APPLIED
    assert executor.calls == ["prepare", "apply"]
    assert executor.prepared_requests[0].proposal_ref == (
        f"proposal://{command.stage_id}/revisions/{command.revision}"
    )
    assert executor.prepared_requests[0].proposal_content_ref.startswith("artifact://")
    assert [event.event_type for event in ledger.events_by_stage[command.stage_id]] == [
        "effect.staged",
        "effect.decision_recorded",
        "effect.claimed",
        "effect.applied",
    ]
    claim = await claims.get(
        org_id="org_effect_test",
        executor=EffectExecutorKind.BUILTIN,
        idempotency_key=command.idempotency_key,
    )
    assert claim is not None and claim.state is EffectClaimState.COMPLETED


@pytest.mark.asyncio
async def test_duplicate_delivery_never_calls_apply_twice() -> None:
    command, ledger, references = await _approved_command()
    claims = InMemoryEffectClaimStore()
    executor = _matching_executor()
    coordinator = _coordinator(
        ledger=ledger,
        references=references,
        claims=claims,
        executor=executor,
    )

    first = await coordinator.handle(command)
    replay = await coordinator.handle(command)

    assert first.status is EffectCoordinatorStatus.APPLIED
    assert replay.status is EffectCoordinatorStatus.REPLAYED
    assert executor.calls.count("prepare") == 1
    assert executor.calls.count("apply") == 1
    assert [
        event.event_type for event in ledger.events_by_stage[command.stage_id]
    ].count("effect.applied") == 1


@pytest.mark.asyncio
async def test_prepare_drift_aborts_without_claim_or_apply() -> None:
    command, ledger, references = await _approved_command()
    claims = InMemoryEffectClaimStore()

    async def _on_prepare(request: object) -> PreparedEffect:
        assert hasattr(request, "stage_id")
        return PreparedEffect(
            request=request,  # type: ignore[arg-type]
            observed_precondition_digest="different-precondition",
        )

    executor = RecordingEffectExecutor(on_prepare=_on_prepare)
    result = await _coordinator(
        ledger=ledger,
        references=references,
        claims=claims,
        executor=executor,
    ).handle(command)

    assert result.status is EffectCoordinatorStatus.PRECONDITION_DRIFT
    assert executor.calls == ["prepare", "abort"]
    assert await claims.list_incomplete() == ()
    event_types = [
        event.event_type for event in ledger.events_by_stage[command.stage_id]
    ]
    assert event_types[-1] == "effect.applied"
    assert ledger.events_by_stage[command.stage_id][-1].payload["outcome"] == (
        EffectOutcome.PRECONDITION_DRIFT.value
    )


@pytest.mark.asyncio
async def test_canonical_only_historical_stage_is_replayable_but_never_executable() -> (
    None
):
    command, ledger, references = await _approved_command()
    historical = ledger.events_by_stage[command.stage_id][0]
    payload = dict(historical.payload)
    payload.pop("proposal_content_ref")
    ledger.events_by_stage[command.stage_id][0] = historical.model_copy(
        update={"payload": payload}
    )
    claims = InMemoryEffectClaimStore()
    executor = _matching_executor()

    result = await _coordinator(
        ledger=ledger,
        references=references,
        claims=claims,
        executor=executor,
    ).handle(command)

    assert result.status is EffectCoordinatorStatus.REFUSED
    assert result.safe_code == "immutable_ref_mismatch"
    assert executor.calls == []
    assert await claims.list_incomplete() == ()


@pytest.mark.asyncio
async def test_timeout_is_indeterminate_and_reconcile_never_reapplies() -> None:
    command, ledger, references = await _approved_command()
    claims = InMemoryEffectClaimStore()

    async def _on_apply(_: PreparedEffect) -> EffectExecutionResult:
        raise asyncio.TimeoutError()

    async def _on_reconcile(claim: object) -> EffectExecutionResult:
        stage_id = getattr(claim, "stage_id")
        claim_id = getattr(claim, "claim_id")
        return EffectExecutionResult(
            outcome=EffectOutcome.APPLIED,
            receipt_ref=f"receipt://effects/{stage_id}/{claim_id}",
            retryable=False,
        )

    executor = _matching_executor(on_apply=_on_apply, on_reconcile=_on_reconcile)
    coordinator = _coordinator(
        ledger=ledger,
        references=references,
        claims=claims,
        executor=executor,
    )

    timed_out = await coordinator.handle(command)
    assert timed_out.status is EffectCoordinatorStatus.INDETERMINATE
    duplicate = await coordinator.handle(command)
    assert duplicate.status is EffectCoordinatorStatus.INDETERMINATE
    assert executor.calls.count("apply") == 1

    reconciled = await coordinator.reconcile(
        EffectReconcileCommand(
            org_id="org_effect_test", claim_id=timed_out.claim_id or ""
        )
    )
    assert reconciled.status is EffectCoordinatorStatus.APPLIED
    assert executor.calls.count("apply") == 1
    assert executor.calls.count("reconcile") == 1
    assert "effect.reconciled" in [
        event.event_type for event in ledger.events_by_stage[command.stage_id]
    ]


@pytest.mark.asyncio
async def test_stale_approval_and_changed_ref_refuse_before_prepare() -> None:
    command, ledger, references = await _approved_command()
    claims = InMemoryEffectClaimStore()
    executor = _matching_executor()
    coordinator = _coordinator(
        ledger=ledger,
        references=references,
        claims=claims,
        executor=executor,
    )

    stale = await coordinator.handle(
        command.model_copy(update={"decision_ledger_id": "rtest·999"})
    )
    assert stale.status is EffectCoordinatorStatus.REFUSED
    assert executor.calls == []

    references.values[next(iter(references.values))] = b"changed immutable bytes"
    changed = await coordinator.handle(command)
    assert changed.status is EffectCoordinatorStatus.REFUSED
    assert changed.safe_code == "immutable_ref_mismatch"
    assert executor.calls == []
    assert await claims.list_incomplete() == ()


@pytest.mark.asyncio
async def test_cancellation_after_claim_aborts_without_apply() -> None:
    command, ledger, references = await _approved_command()
    claims = InMemoryEffectClaimStore()
    executor = _matching_executor()

    result = await _coordinator(
        ledger=ledger,
        references=references,
        claims=claims,
        executor=executor,
        cancellation=_Cancelled(),
    ).handle(command)

    assert result.status is EffectCoordinatorStatus.CANCELLED
    assert executor.calls == ["prepare", "abort"]
    claim = await claims.get(
        org_id="org_effect_test",
        executor=EffectExecutorKind.BUILTIN,
        idempotency_key=command.idempotency_key,
    )
    assert claim is not None and claim.state is EffectClaimState.CANCELLED
