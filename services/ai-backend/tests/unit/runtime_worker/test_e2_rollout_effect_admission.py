"""Adversarial E2 admission tests at the sole effect-commit worker boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
import json

import pytest

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.rollout import RolloutCapability
from agent_runtime.rollout_admission import E2RolloutAdmission
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord, RuntimeEffectCommitCommand
from runtime_worker.e2_rollout_admission import E2RolloutEffectCommitHandler
from runtime_worker.loop import RuntimeWorker

pytestmark = pytest.mark.anyio

_ORG = "org_e2_runtime"
_USER = "user_e2_canary"
_RUN = "run_e2_runtime"
_CONVERSATION = "conv_e2_runtime"
_STAGE = "stg_123e4567-e89b-42d3-a456-426614174000"
_CAPABILITIES = (
    RolloutCapability.OPERATION_GATEWAY,
    RolloutCapability.MCP_GATEWAY,
    RolloutCapability.EFFECT_STAGER,
    RolloutCapability.EFFECT_COMMIT,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _settings(*, kill: bool = False) -> RuntimeSettings:
    environment = {
        "SURFACES_V2": "true",
        "ARTIFACT_EFFECTS_V2": "true",
        "ARTIFACT_DRAFTS_V2": "true",
        "OPERATION_GATEWAY_MODE": "enforce",
        "EFFECT_STAGER_MODE": "enforce",
        "EFFECT_COMMIT_MODE": "enforce",
        "MCP_GATEWAY_MODE": "enforce",
        "E2_ROLLOUT_COHORTS_JSON": json.dumps(
            [
                {
                    "capability": capability.value,
                    "org_id": _ORG,
                    "user_id": _USER,
                }
                for capability in _CAPABILITIES
            ]
        ),
    }
    if kill:
        environment["E2_ROLLOUT_KILL_SWITCHES_JSON"] = json.dumps(
            [RolloutCapability.EFFECT_COMMIT.value]
        )
    return RuntimeSettings.load(environ=environment)


def _run(*, user_id: str = _USER) -> RunRecord:
    return RunRecord(
        run_id=_RUN,
        conversation_id=_CONVERSATION,
        org_id=_ORG,
        user_id=user_id,
        user_message_id="msg_e2_runtime",
        trace_id="trace_e2_runtime",
        status=AgentRunStatus.RUNNING,
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=AgentRuntimeContext(
            user_id=user_id,
            org_id=_ORG,
            roles=["employee"],
            run_id=_RUN,
            trace_id="trace_e2_runtime",
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


def _command(*, user_id: str = _USER) -> RuntimeEffectCommitCommand:
    return RuntimeEffectCommitCommand(
        command_id="effect-e2-runtime-1",
        org_id=_ORG,
        user_id=user_id,
        conversation_id=_CONVERSATION,
        run_id=_RUN,
        stage_id=_STAGE,
        revision=2,
        decision_ledger_id="re2runtime·002",
        proposal_digest="a" * 64,
        target_digest="b" * 64,
        idempotency_key="effect-e2-runtime-1",
    )


@dataclass
class _Runs:
    run: RunRecord

    async def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None:
        assert (org_id, run_id) == (_ORG, _RUN)
        return self.run


@dataclass
class _Resolver:
    calls: int = 0

    async def resolve_executor(
        self, *, run: RunRecord, stage_id: str
    ) -> EffectExecutorKind:
        assert run.run_id == _RUN
        assert stage_id == _STAGE
        self.calls += 1
        return EffectExecutorKind.MCP


@dataclass
class _ExternalEffectSpy:
    commands: list[RuntimeEffectCommitCommand] = field(default_factory=list)

    async def handle(self, command: RuntimeEffectCommitCommand) -> None:
        self.commands.append(command)


def _handler(*, settings: RuntimeSettings, run: RunRecord, spy: _ExternalEffectSpy):
    return E2RolloutEffectCommitHandler(
        delegate=spy,
        persistence=_Runs(run=run),
        executor_resolver=_Resolver(),
        admission=E2RolloutAdmission(
            resolution=settings.execution.rollout,
            cohorts=settings.execution.rollout_cohorts,
            kill_switches=settings.execution.rollout_kill_switches,
        ),
    )


async def test_nonmatching_cohort_cannot_reach_the_effect_executor() -> None:
    spy = _ExternalEffectSpy()
    handler = _handler(
        settings=_settings(), run=_run(user_id="user_not_enrolled"), spy=spy
    )

    with pytest.raises(AgentRuntimeError) as error:
        await handler.handle(_command(user_id="user_not_enrolled"))

    assert error.value.code is RuntimeErrorCode.PERMISSION_DENIED
    assert spy.commands == []


async def test_operational_rollback_blocks_an_already_enqueued_effect_before_apply() -> (
    None
):
    spy = _ExternalEffectSpy()
    handler = _handler(settings=_settings(kill=True), run=_run(), spy=spy)

    with pytest.raises(AgentRuntimeError) as error:
        await handler.handle(_command())

    assert error.value.code is RuntimeErrorCode.PERMISSION_DENIED
    # The worker gate runs before the downstream claim/prepare/apply handler,
    # so a queue item from before rollback cannot produce an external effect.
    assert spy.commands == []


async def test_admitted_cohort_reaches_the_wrapped_effect_handler_once() -> None:
    spy = _ExternalEffectSpy()
    handler = _handler(settings=_settings(), run=_run(), spy=spy)
    command = _command()

    await handler.handle(command)

    assert spy.commands == [command]


def test_worker_wraps_even_an_injected_effect_handler_at_the_only_dispatch_slot() -> (
    None
):
    store = InMemoryRuntimeApiStore()
    downstream = _ExternalEffectSpy()
    worker = RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=RuntimeSettings.load(environ={}),
        effect_commit_handler=downstream,
    )

    assert isinstance(worker.effect_commit_handler, E2RolloutEffectCommitHandler)
    assert worker.effect_commit_handler.delegate is downstream
