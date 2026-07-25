"""Contract tests for the body-free A5 reconciliation worker adapter."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError

from agent_runtime.effects.claims import EffectClaim
from agent_runtime.effects.coordinator import EffectReconcileCommand
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.surfaces_v2.ledger_models import EffectActor, EffectExecutorKind
from runtime_api.schemas import (
    AgentRunStatus,
    RunRecord,
    RuntimeEffectReconcileCommand,
)
from runtime_worker.handlers.effect_reconcile import RuntimeEffectReconcileHandler

pytestmark = pytest.mark.anyio

ORG = "org_effect_handlers"
USER = "user_effect_handlers"
RUN = "run_effect_handlers"
CONVERSATION = "conv_effect_handlers"
TRACE = "trace_effect_handlers"
STAGE = "stg_123e4567-e89b-42d3-a456-426614174000"
CLAIM_ID = "clm_effect_handler"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _run() -> RunRecord:
    return RunRecord(
        run_id=RUN,
        conversation_id=CONVERSATION,
        org_id=ORG,
        user_id=USER,
        user_message_id="msg_effect_handlers",
        trace_id=TRACE,
        status=AgentRunStatus.RUNNING,
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=AgentRuntimeContext(
            user_id=USER,
            org_id=ORG,
            roles=["employee"],
            run_id=RUN,
            trace_id=TRACE,
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        ),
    )


def _claim(**changes: object) -> EffectClaim:
    values: dict[str, object] = {
        "org_id": ORG,
        "run_id": RUN,
        "stage_id": STAGE,
        "revision": 2,
        "claim_id": CLAIM_ID,
        "idempotency_key": "effect-handler-reconcile-1",
        "executor": EffectExecutorKind.BUILTIN,
        "proposal_digest": "a" * 64,
        "target_digest": "b" * 64,
        "target_ref": "artifact://org_effect_handlers/target",
        "proposal_ref": "artifact://org_effect_handlers/proposal",
        "actor": EffectActor.USER,
        "decision_ledger_id": "rhandler·002",
    }
    values.update(changes)
    return EffectClaim.model_validate(values)


def _command(**changes: object) -> RuntimeEffectReconcileCommand:
    values: dict[str, object] = {
        "command_id": "effect-reconcile-handler-1",
        "org_id": ORG,
        "run_id": RUN,
        "claim_id": CLAIM_ID,
    }
    values.update(changes)
    return RuntimeEffectReconcileCommand.model_validate(values)


@dataclass
class _Runs:
    run: RunRecord | None
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None:
        self.calls.append((org_id, run_id))
        return self.run


@dataclass
class _Claims:
    claim: EffectClaim | None
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def get_by_claim_id(
        self, *, org_id: str, claim_id: str
    ) -> EffectClaim | None:
        self.calls.append((org_id, claim_id))
        return self.claim


@dataclass
class _Coordinator:
    commands: list[EffectReconcileCommand] = field(default_factory=list)

    async def handle(self, command: object) -> object:
        del command
        raise AssertionError("reconcile adapter must not invoke commit handling")

    async def reconcile(self, command: EffectReconcileCommand) -> object:
        self.commands.append(command)
        return object()


@dataclass
class _Factory:
    coordinator: _Coordinator
    runs: list[RunRecord] = field(default_factory=list)

    def for_run(self, *, run: RunRecord) -> _Coordinator:
        self.runs.append(run)
        return self.coordinator


async def test_reconcile_handler_validates_claim_and_maps_body_free_command() -> None:
    run = _run()
    coordinator = _Coordinator()
    factory = _Factory(coordinator=coordinator)
    handler = RuntimeEffectReconcileHandler(
        persistence=_Runs(run=run),
        claims=_Claims(claim=_claim()),
        coordinator_factory=factory,
    )

    await handler.handle(_command())

    assert factory.runs == [run]
    assert coordinator.commands == [
        EffectReconcileCommand(org_id=ORG, claim_id=CLAIM_ID)
    ]
    assert coordinator.commands[0].model_dump(mode="json") == {
        "org_id": ORG,
        "claim_id": CLAIM_ID,
    }


async def test_reconcile_handler_rejects_claim_run_mismatch_before_loading_run() -> (
    None
):
    runs = _Runs(run=_run())
    coordinator = _Coordinator()
    factory = _Factory(coordinator=coordinator)
    handler = RuntimeEffectReconcileHandler(
        persistence=runs,
        claims=_Claims(claim=_claim(run_id="run_foreign")),
        coordinator_factory=factory,
    )

    with pytest.raises(AgentRuntimeError) as error:
        await handler.handle(_command())

    assert error.value.code is RuntimeErrorCode.PERMISSION_DENIED
    assert runs.calls == []
    assert factory.runs == []
    assert coordinator.commands == []


async def test_reconcile_handler_rejects_missing_claim_before_constructing_coordinator() -> (
    None
):
    coordinator = _Coordinator()
    factory = _Factory(coordinator=coordinator)
    handler = RuntimeEffectReconcileHandler(
        persistence=_Runs(run=_run()),
        claims=_Claims(claim=None),
        coordinator_factory=factory,
    )

    with pytest.raises(AgentRuntimeError) as error:
        await handler.handle(_command())

    assert error.value.code is RuntimeErrorCode.VALIDATION_ERROR
    assert factory.runs == []
    assert coordinator.commands == []


async def test_reconcile_handler_rejects_missing_run_and_transport_bodies() -> None:
    coordinator = _Coordinator()
    factory = _Factory(coordinator=coordinator)
    handler = RuntimeEffectReconcileHandler(
        persistence=_Runs(run=None),
        claims=_Claims(claim=_claim()),
        coordinator_factory=factory,
    )

    with pytest.raises(AgentRuntimeError) as error:
        await handler.handle(_command())
    assert error.value.code is RuntimeErrorCode.VALIDATION_ERROR
    assert factory.runs == []
    assert coordinator.commands == []
    with pytest.raises(ValidationError):
        RuntimeEffectReconcileCommand.model_validate(
            {**_command().model_dump(mode="json"), "target_bytes": "secret"}
        )
