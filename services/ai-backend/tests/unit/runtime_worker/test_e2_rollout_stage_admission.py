"""Adversarial E2 coverage for the legacy D1/D3 staged-MCP lane.

These tests prove admission is not a UI concern: denied staging writes neither
the ledger nor the outbox, and a rollback after approval reaches no downstream
claim/connector handler.  The runtime wrapper is deliberately exercised with a
spy rather than a mock of its own checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from uuid import uuid4

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from agent_runtime.rollout import RolloutCapability
from agent_runtime.rollout_admission import E2RolloutAdmission
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.stage_rollout import StagedWriteRolloutGate
from agent_runtime.surfaces_v2.staging import StageRolloutDenied, WriteStager
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    RunRecord,
    RuntimeStageCommitCommand,
)
from runtime_worker.e2_rollout_admission import E2RolloutStageCommitHandler
from runtime_worker.loop import RuntimeWorker

pytestmark = pytest.mark.anyio

_ORG = "org_e2_stage"
_USER = "user_e2_stage"
_RUN = "run_e2_stage"
_CONVERSATION = "conv_e2_stage"
_CAPABILITIES = (
    RolloutCapability.OPERATION_GATEWAY,
    RolloutCapability.EFFECT_STAGER,
    RolloutCapability.EFFECT_COMMIT,
    RolloutCapability.MCP_GATEWAY,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _settings(
    *,
    admitted_user: str = _USER,
    enabled: bool = True,
    kill: bool = False,
) -> RuntimeSettings:
    environment: dict[str, str] = {}
    if enabled:
        environment.update(
            {
                "OPERATION_GATEWAY_MODE": "enforce",
                "EFFECT_STAGER_MODE": "enforce",
                "EFFECT_COMMIT_MODE": "enforce",
                "MCP_GATEWAY_MODE": "enforce",
                "E2_ROLLOUT_COHORTS_JSON": json.dumps(
                    [
                        {
                            "capability": capability.value,
                            "org_id": _ORG,
                            "user_id": admitted_user,
                        }
                        for capability in _CAPABILITIES
                    ]
                ),
            }
        )
    if kill:
        environment["E2_ROLLOUT_KILL_SWITCHES_JSON"] = json.dumps(
            [RolloutCapability.EFFECT_COMMIT.value]
        )
    return RuntimeSettings.load(environ=environment)


def _admission(settings: RuntimeSettings) -> E2RolloutAdmission:
    return E2RolloutAdmission(
        resolution=settings.execution.rollout,
        cohorts=settings.execution.rollout_cohorts,
        kill_switches=settings.execution.rollout_kill_switches,
    )


def _run(*, user_id: str = _USER) -> RunRecord:
    return RunRecord(
        run_id=_RUN,
        conversation_id=_CONVERSATION,
        org_id=_ORG,
        user_id=user_id,
        user_message_id="msg_e2_stage",
        trace_id="trace_e2_stage",
        status=AgentRunStatus.RUNNING,
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=AgentRuntimeContext(
            user_id=user_id,
            org_id=_ORG,
            roles=["employee"],
            run_id=_RUN,
            trace_id="trace_e2_stage",
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


def _draft(*, user_id: str = _USER) -> DraftRecord:
    return DraftRecord(
        draft_id=uuid4().hex,
        version=1,
        org_id=_ORG,
        conversation_id=_CONVERSATION,
        run_id=_RUN,
        user_id=user_id,
        title="Cohort email",
        content_text="Only an admitted cohort may send this.",
        target_connector="gmail",
        status=DraftStatus.SEND_PENDING_APPROVAL,
    )


@dataclass
class _QueueSpy:
    commands: list[RuntimeStageCommitCommand] = field(default_factory=list)

    async def enqueue_stage_commit(self, **kwargs: object) -> None:  # noqa: ANN003
        self.commands.append(RuntimeStageCommitCommand.model_validate(kwargs))


@dataclass
class _DispatchSpy:
    commands: list[RuntimeStageCommitCommand] = field(default_factory=list)

    async def handle(self, command: RuntimeStageCommitCommand) -> None:
        self.commands.append(command)


def _stager(
    *,
    store: InMemoryRuntimeApiStore,
    settings: RuntimeSettings,
    queue: _QueueSpy,
) -> WriteStager:
    return WriteStager(
        draft_store=InMemoryDraftStore(),
        ledger=RuntimeStageLedger(
            event_producer=RuntimeEventProducer(persistence=store, event_store=store)
        ),
        rollout_gate=StagedWriteRolloutGate(admission=_admission(settings)),
        commit_queue=queue,
    )


@pytest.mark.parametrize(
    ("settings", "user_id"),
    (
        (_settings(admitted_user="someone_else"), _USER),
        (_settings(kill=True), _USER),
    ),
)
async def test_denied_new_stage_has_zero_ledger_and_outbox_effects(
    settings: RuntimeSettings,
    user_id: str,
) -> None:
    store = InMemoryRuntimeApiStore()
    run = _run(user_id=user_id)
    store.runs[_RUN] = run
    store.events_by_run[_RUN] = []
    queue = _QueueSpy()
    stager = _stager(store=store, settings=settings, queue=queue)
    draft = _draft(user_id=user_id)

    with pytest.raises(StageRolloutDenied):
        await stager.stage(
            run=run,
            org_id=_ORG,
            run_id=_RUN,
            draft=draft,
            target_connector="gmail",
            target_op="send",
        )

    assert store.events_by_run[_RUN] == []
    assert queue.commands == []


@pytest.mark.parametrize(
    "continuation_settings",
    (
        _settings(enabled=False),
        _settings(admitted_user="another_cohort"),
        _settings(kill=True),
    ),
)
async def test_governed_queued_stage_never_falls_back_after_restart_or_rollback(
    continuation_settings: RuntimeSettings,
) -> None:
    store = InMemoryRuntimeApiStore()
    run = _run()
    store.runs[_RUN] = run
    store.events_by_run[_RUN] = []
    queue = _QueueSpy()
    staging_settings = _settings()
    stager = _stager(store=store, settings=staging_settings, queue=queue)
    draft = _draft()

    staged = await stager.stage(
        run=run,
        org_id=_ORG,
        run_id=_RUN,
        draft=draft,
        target_connector="gmail",
        target_op="send",
    )
    assert staged.governed_lane is not None
    approved = await stager.record_decision(
        run=run,
        org_id=_ORG,
        run_id=_RUN,
        stage_id=staged.stage_id,
        decision="approve",
        rev=1,
    )
    assert approved.governed_lane == staged.governed_lane
    assert len(queue.commands) == 1
    command = queue.commands[0]
    assert command.governed_capabilities == staged.governed_lane.capabilities
    before = tuple(store.events_by_run[_RUN])

    dispatch = _DispatchSpy()
    worker_gate = E2RolloutStageCommitHandler(
        delegate=dispatch,
        persistence=store,
        event_store=store,
        admission=_admission(continuation_settings),
    )
    with pytest.raises(AgentRuntimeError) as error:
        await worker_gate.handle(command)

    assert error.value.code is RuntimeErrorCode.PERMISSION_DENIED
    assert dispatch.commands == []
    # Worker denial happens before the downstream claim/prepare/apply protocol;
    # it adds neither a terminal ledger event nor another command.
    assert tuple(store.events_by_run[_RUN]) == before
    assert queue.commands == [command]


async def test_governed_stage_admission_is_deterministic_across_config_reload() -> None:
    first = _settings()
    # Rebuild from the same trusted environment rather than reusing a mutable
    # policy object; that is the worker restart/config-reload boundary.
    restarted = _settings()
    store = InMemoryRuntimeApiStore()
    run = _run()
    store.runs[_RUN] = run
    store.events_by_run[_RUN] = []
    queue = _QueueSpy()
    stager = _stager(store=store, settings=first, queue=queue)
    staged = await stager.stage(
        run=run,
        org_id=_ORG,
        run_id=_RUN,
        draft=_draft(),
        target_connector="gmail",
        target_op="send",
    )
    await stager.record_decision(
        run=run,
        org_id=_ORG,
        run_id=_RUN,
        stage_id=staged.stage_id,
        decision="approve",
        rev=1,
    )
    dispatch = _DispatchSpy()
    worker_gate = E2RolloutStageCommitHandler(
        delegate=dispatch,
        persistence=store,
        event_store=store,
        admission=_admission(restarted),
    )

    await worker_gate.handle(queue.commands[0])

    assert dispatch.commands == queue.commands


async def test_tampered_stage_command_cannot_strip_the_authoritative_mark() -> None:
    store = InMemoryRuntimeApiStore()
    run = _run()
    store.runs[_RUN] = run
    store.events_by_run[_RUN] = []
    queue = _QueueSpy()
    settings = _settings()
    stager = _stager(store=store, settings=settings, queue=queue)
    staged = await stager.stage(
        run=run,
        org_id=_ORG,
        run_id=_RUN,
        draft=_draft(),
        target_connector="gmail",
        target_op="send",
    )
    await stager.record_decision(
        run=run,
        org_id=_ORG,
        run_id=_RUN,
        stage_id=staged.stage_id,
        decision="approve",
        rev=1,
    )
    stripped = queue.commands[0].model_copy(update={"governed_capabilities": None})
    dispatch = _DispatchSpy()
    worker_gate = E2RolloutStageCommitHandler(
        delegate=dispatch,
        persistence=store,
        event_store=store,
        admission=_admission(settings),
    )

    with pytest.raises(AgentRuntimeError) as error:
        await worker_gate.handle(stripped)

    assert error.value.code is RuntimeErrorCode.PERMISSION_DENIED
    assert dispatch.commands == []


def test_worker_wraps_an_injected_stage_handler_at_its_only_dispatch_slot() -> None:
    store = InMemoryRuntimeApiStore()
    downstream = _DispatchSpy()

    worker = RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=RuntimeSettings.load(environ={}),
        stage_commit_handler=downstream,  # type: ignore[arg-type]
    )

    assert isinstance(worker.stage_commit_handler, E2RolloutStageCommitHandler)
    assert worker.stage_commit_handler.delegate is downstream
