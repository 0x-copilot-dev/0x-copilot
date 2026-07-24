"""Contract tests for the narrow A5 effect-commit worker adapter."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError

from agent_runtime.effects.contracts import EffectCommitCommand
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from runtime_api.schemas import (
    AgentRunStatus,
    RunRecord,
    RuntimeEffectCommitCommand,
)
from runtime_worker.handlers.effect_commit import RuntimeEffectCommitHandler

pytestmark = pytest.mark.anyio

ORG = "org_effect_handlers"
USER = "user_effect_handlers"
RUN = "run_effect_handlers"
CONVERSATION = "conv_effect_handlers"
TRACE = "trace_effect_handlers"
STAGE = "stg_123e4567-e89b-42d3-a456-426614174000"


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


def _command(**changes: object) -> RuntimeEffectCommitCommand:
    values: dict[str, object] = {
        "command_id": "effect-commit-handler-1",
        "org_id": ORG,
        "user_id": USER,
        "conversation_id": CONVERSATION,
        "run_id": RUN,
        "stage_id": STAGE,
        "revision": 2,
        "decision_ledger_id": "rhandler·002",
        "proposal_digest": "a" * 64,
        "target_digest": "b" * 64,
        "idempotency_key": "effect-handler-commit-1",
        "trace_propagation": {"traceparent": "00-ignored-by-adapter"},
    }
    values.update(changes)
    return RuntimeEffectCommitCommand.model_validate(values)


@dataclass
class _Runs:
    run: RunRecord | None
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None:
        self.calls.append((org_id, run_id))
        return self.run


@dataclass
class _Coordinator:
    commands: list[EffectCommitCommand] = field(default_factory=list)

    async def handle(self, command: EffectCommitCommand) -> object:
        self.commands.append(command)
        return object()

    async def reconcile(self, command: object) -> object:
        del command
        raise AssertionError("commit adapter must not invoke reconciliation")


@dataclass
class _Factory:
    coordinator: _Coordinator
    runs: list[RunRecord] = field(default_factory=list)

    def for_run(self, *, run: RunRecord) -> _Coordinator:
        self.runs.append(run)
        return self.coordinator


async def test_commit_handler_revalidates_scope_and_maps_exact_pure_command() -> None:
    run = _run()
    coordinator = _Coordinator()
    factory = _Factory(coordinator=coordinator)
    handler = RuntimeEffectCommitHandler(
        persistence=_Runs(run=run),
        coordinator_factory=factory,
    )
    command = _command()

    await handler.handle(command)

    assert factory.runs == [run]
    assert coordinator.commands == [
        EffectCommitCommand(
            run_id=RUN,
            stage_id=STAGE,
            revision=2,
            decision_ledger_id="rhandler·002",
            proposal_digest="a" * 64,
            target_digest="b" * 64,
            idempotency_key="effect-handler-commit-1",
        )
    ]


async def test_commit_handler_drops_transport_metadata_and_has_no_body_channel() -> (
    None
):
    coordinator = _Coordinator()
    handler = RuntimeEffectCommitHandler(
        persistence=_Runs(run=_run()),
        coordinator_factory=_Factory(coordinator=coordinator),
    )

    await handler.handle(_command())

    assert coordinator.commands[0].model_dump(mode="json") == {
        "run_id": RUN,
        "stage_id": STAGE,
        "revision": 2,
        "decision_ledger_id": "rhandler·002",
        "proposal_digest": "a" * 64,
        "target_digest": "b" * 64,
        "idempotency_key": "effect-handler-commit-1",
    }
    with pytest.raises(ValidationError):
        RuntimeEffectCommitCommand.model_validate(
            {**_command().model_dump(mode="json"), "proposal_bytes": "secret"}
        )


async def test_commit_handler_rejects_foreign_user_before_constructing_coordinator() -> (
    None
):
    coordinator = _Coordinator()
    factory = _Factory(coordinator=coordinator)
    handler = RuntimeEffectCommitHandler(
        persistence=_Runs(run=_run()),
        coordinator_factory=factory,
    )

    with pytest.raises(AgentRuntimeError) as error:
        await handler.handle(_command(user_id="user_foreign"))

    assert error.value.code is RuntimeErrorCode.PERMISSION_DENIED
    assert factory.runs == []
    assert coordinator.commands == []


async def test_commit_handler_rejects_missing_run_before_constructing_coordinator() -> (
    None
):
    coordinator = _Coordinator()
    factory = _Factory(coordinator=coordinator)
    handler = RuntimeEffectCommitHandler(
        persistence=_Runs(run=None),
        coordinator_factory=factory,
    )

    with pytest.raises(AgentRuntimeError) as error:
        await handler.handle(_command())

    assert error.value.code is RuntimeErrorCode.VALIDATION_ERROR
    assert factory.runs == []
    assert coordinator.commands == []
