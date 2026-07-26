"""Request-boundary E2 tests for generic canonical effect decisions."""

from __future__ import annotations

import json

import pytest

from agent_runtime.api.effect_commit_queue import RuntimeEffectCommitOutbox
from agent_runtime.api.effect_ledger import RuntimeEffectLedger
from agent_runtime.api.effect_stage_decision_service import EffectStageDecisionService
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.effects.contracts import EffectActorIdentity, EffectStageScope
from agent_runtime.effects.errors import EffectStageForbidden
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.effects.staging import EffectStager
from agent_runtime.rollout import RolloutCapability
from agent_runtime.rollout_admission import E2RolloutAdmission
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectDecisionKind,
    EffectExecutorKind,
    EffectPolicy,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord
from tests.unit.agent_runtime.effects.fakes import policy_snapshot, proposal

pytestmark = pytest.mark.anyio

_ORG = "org_generic_effect"
_USER = "user_generic_effect"
_RUN = "run_generic_effect"
_CONVERSATION = "conv_generic_effect"
_CAPABILITIES = (
    RolloutCapability.OPERATION_GATEWAY,
    RolloutCapability.EFFECT_STAGER,
    RolloutCapability.EFFECT_COMMIT,
    RolloutCapability.MCP_GATEWAY,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _settings(*, admitted_user: str) -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
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


def _run() -> RunRecord:
    from agent_runtime.execution.contracts import AgentRuntimeContext

    return RunRecord(
        run_id=_RUN,
        conversation_id=_CONVERSATION,
        org_id=_ORG,
        user_id=_USER,
        user_message_id="msg_generic_effect",
        trace_id="trace_generic_effect",
        status=AgentRunStatus.RUNNING,
        model_provider="openai",
        model_name="gpt-5.4-mini",
        runtime_context=AgentRuntimeContext(
            user_id=_USER,
            org_id=_ORG,
            roles=["employee"],
            run_id=_RUN,
            trace_id="trace_generic_effect",
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


async def _stage(store: InMemoryRuntimeApiStore, run: RunRecord):
    owner_ref = f"principal://users/{run.user_id}"
    scope = EffectStageScope(run_id=run.run_id, owner_ref=owner_ref)
    stager = EffectStager(
        ledger=RuntimeEffectLedger(
            event_producer=RuntimeEventProducer(persistence=store, event_store=store),
            run=run,
            owner_ref=owner_ref,
        ),
        outbox=RuntimeEffectCommitOutbox(
            queue=store,
            scope=EffectExecutionScope(
                org_id=run.org_id,
                user_id=run.user_id,
                conversation_id=run.conversation_id,
                run_id=run.run_id,
                owner_ref=owner_ref,
            ),
        ),
    )
    return await stager.stage(
        scope=scope,
        proposed_effect=proposal(executor=EffectExecutorKind.MCP),
        policy_snapshot=policy_snapshot(user_policy=EffectPolicy.ASK),
        actor=EffectActorIdentity(actor=EffectActor.USER, principal_ref=owner_ref),
        idempotency_key="stage-generic-effect",
    )


def _service(
    *, store: InMemoryRuntimeApiStore, settings: RuntimeSettings
) -> EffectStageDecisionService:
    return EffectStageDecisionService(
        persistence=store,
        event_producer=RuntimeEventProducer(persistence=store, event_store=store),
        queue=store,
        rollout_admission=E2RolloutAdmission(
            resolution=settings.execution.rollout,
            cohorts=settings.execution.rollout_cohorts,
            kill_switches=settings.execution.rollout_kill_switches,
        ),
    )


async def test_generic_noncohort_decision_writes_no_ledger_or_outbox_row() -> None:
    store = InMemoryRuntimeApiStore()
    run = _run()
    store.runs[_RUN] = run
    store.events_by_run[_RUN] = []
    staged = await _stage(store, run)
    before = tuple(store.events_by_run[_RUN])
    service = _service(store=store, settings=_settings(admitted_user="other_user"))

    with pytest.raises(EffectStageForbidden):
        await service.record_decision(
            org_id=_ORG,
            user_id=_USER,
            run_id=_RUN,
            stage_id=staged.stage_id,
            revision=staged.current_revision.revision,
            decision=EffectDecisionKind.APPROVE,
            proposal_digest=staged.current_revision.proposal_digest,
            target_digest=staged.target_digest,
            allowed_executors=frozenset({EffectExecutorKind.MCP}),
        )

    assert tuple(store.events_by_run[_RUN]) == before
    assert store.effect_commit_commands == []


async def test_generic_admitted_decision_persists_governed_command_mark() -> None:
    store = InMemoryRuntimeApiStore()
    run = _run()
    store.runs[_RUN] = run
    store.events_by_run[_RUN] = []
    staged = await _stage(store, run)
    service = _service(store=store, settings=_settings(admitted_user=_USER))

    await service.record_decision(
        org_id=_ORG,
        user_id=_USER,
        run_id=_RUN,
        stage_id=staged.stage_id,
        revision=staged.current_revision.revision,
        decision=EffectDecisionKind.APPROVE,
        proposal_digest=staged.current_revision.proposal_digest,
        target_digest=staged.target_digest,
        allowed_executors=frozenset({EffectExecutorKind.MCP}),
    )

    assert len(store.effect_commit_commands) == 1
    assert store.effect_commit_commands[0].governed_capabilities == _CAPABILITIES
