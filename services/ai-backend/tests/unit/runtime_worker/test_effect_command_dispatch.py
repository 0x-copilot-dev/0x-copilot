"""Dispatch-only tests for the A5 effect command transport seams."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.records import RuntimeWorkerClaim
from runtime_api.schemas import (
    RuntimeEffectCommitCommand,
    RuntimeEffectReconcileCommand,
)
from runtime_worker.loop import RuntimeWorker

pytestmark = pytest.mark.anyio

_CREATED_AT = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _commit_command() -> RuntimeEffectCommitCommand:
    return RuntimeEffectCommitCommand(
        command_id="effect-commit-1",
        org_id="org_acme",
        user_id="user_sarah",
        conversation_id="conv_1",
        run_id="run_1",
        stage_id="stg_00000000-0000-4000-8000-000000000001",
        revision=2,
        decision_ledger_id="r123.7",
        proposal_digest="a" * 64,
        target_digest="b" * 64,
        idempotency_key="effect-commit-1",
        created_at=_CREATED_AT,
    )


def _reconcile_command() -> RuntimeEffectReconcileCommand:
    return RuntimeEffectReconcileCommand(
        command_id="effect-reconcile-1",
        org_id="org_acme",
        run_id="run_1",
        claim_id="clm_abc123",
        created_at=_CREATED_AT,
    )


class _CommitSpy:
    def __init__(self) -> None:
        self.commands: list[RuntimeEffectCommitCommand] = []

    async def handle(self, command: RuntimeEffectCommitCommand) -> None:
        self.commands.append(command)


class _ReconcileSpy:
    def __init__(self) -> None:
        self.commands: list[RuntimeEffectReconcileCommand] = []

    async def handle(self, command: RuntimeEffectReconcileCommand) -> None:
        self.commands.append(command)


def _claim(*, command_type: str, payload: dict[str, object]) -> RuntimeWorkerClaim:
    return RuntimeWorkerClaim(
        command_id=str(payload["command_id"]),
        command_type=command_type,
        org_id="org_acme",
        run_id="run_1",
        locked_by="worker_1",
        lock_expires_at=_CREATED_AT,
        payload=payload,
    )


async def test_worker_dispatches_validated_effect_commands_only_to_injected_handlers() -> (
    None
):
    commit = _commit_command()
    reconcile = _reconcile_command()
    commit_spy = _CommitSpy()
    reconcile_spy = _ReconcileSpy()
    worker = RuntimeWorker.__new__(RuntimeWorker)
    worker.effect_commit_handler = commit_spy
    worker.effect_reconcile_handler = reconcile_spy

    await worker._dispatch(
        _claim(
            command_type=PersistenceValues.EventType.EFFECT_COMMIT_REQUESTED,
            payload=commit.model_dump(mode="json"),
        )
    )
    await worker._dispatch(
        _claim(
            command_type=PersistenceValues.EventType.EFFECT_RECONCILE_REQUESTED,
            payload=reconcile.model_dump(mode="json"),
        )
    )

    assert commit_spy.commands == [commit]
    assert reconcile_spy.commands == [reconcile]


async def test_worker_rejects_legacy_reconcile_scope_fields_before_handler() -> None:
    reconcile = _reconcile_command()
    reconcile_spy = _ReconcileSpy()
    worker = RuntimeWorker.__new__(RuntimeWorker)
    worker.effect_reconcile_handler = reconcile_spy

    legacy_payload = {
        **reconcile.model_dump(mode="json"),
        "user_id": "user_sarah",
        "conversation_id": "conv_1",
        "stage_id": "stg_00000000-0000-4000-8000-000000000001",
    }

    with pytest.raises(ValidationError):
        await worker._dispatch(
            _claim(
                command_type=PersistenceValues.EventType.EFFECT_RECONCILE_REQUESTED,
                payload=legacy_payload,
            )
        )

    assert reconcile_spy.commands == []


async def test_worker_rejects_reconcile_payload_outside_durable_queue_scope() -> None:
    reconcile = _reconcile_command()
    reconcile_spy = _ReconcileSpy()
    worker = RuntimeWorker.__new__(RuntimeWorker)
    worker.effect_reconcile_handler = reconcile_spy

    with pytest.raises(AgentRuntimeError, match="durable queue claim"):
        await worker._dispatch(
            _claim(
                command_type=PersistenceValues.EventType.EFFECT_RECONCILE_REQUESTED,
                payload={**reconcile.model_dump(mode="json"), "run_id": "run_other"},
            )
        )

    assert reconcile_spy.commands == []
