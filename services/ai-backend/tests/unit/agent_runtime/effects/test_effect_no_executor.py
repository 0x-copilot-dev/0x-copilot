from pathlib import Path

from agent_runtime.effects.staging import EffectStager
from agent_runtime.surfaces_v2.ledger_models import EffectDecisionKind
from tests.unit.architecture.effect_execution_reachability import (
    APPROVED_EXECUTION_PATHS,
    APPROVED_HANDLER_DELEGATIONS,
    EffectExecutionReachabilityGate,
)

from .fakes import (
    ExplodingEffectHandle,
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
    policy_snapshot,
    proposal,
    scope,
    user,
)

_SERVICE_ROOT = Path(__file__).resolve().parents[4]
_SOURCE_ROOT = _SERVICE_ROOT / "src"


def test_model_staging_and_worker_paths_reach_execution_only_via_allowlist() -> None:
    gate = EffectExecutionReachabilityGate(_SOURCE_ROOT)

    assert gate.allowlist_violations() == ()
    assert gate.violations() == ()
    assert gate.direct_dispatch_violations() == ()
    assert gate.handler_delegation_violations() == ()


def test_execution_allowlist_is_explicit_and_explained() -> None:
    """A broad source-prefix exemption would weaken the execution boundary."""

    assert APPROVED_EXECUTION_PATHS
    assert APPROVED_HANDLER_DELEGATIONS
    assert all(
        path.reason and len(path.nodes) >= 2 for path in APPROVED_EXECUTION_PATHS
    )
    assert all(delegation.reason for delegation in APPROVED_HANDLER_DELEGATIONS)


def test_reachability_gate_rejects_planted_indirect_import_and_call(
    tmp_path: Path,
) -> None:
    _write_fixture(
        tmp_path,
        "agent_runtime/capabilities/tools/rogue.py",
        "class RogueTool:\n"
        "    async def invoke(self):\n"
        "        from agent_runtime.effects.intermediary import dispatch\n"
        "        return await dispatch()\n",
    )
    _write_fixture(
        tmp_path,
        "agent_runtime/effects/intermediary.py",
        "from runtime_worker.mcp_effect_executor import McpEffectExecutor\n"
        "\n"
        "async def dispatch():\n"
        "    executor = McpEffectExecutor()\n"
        "    return await executor.apply(None)\n",
    )
    _write_fixture(
        tmp_path,
        "runtime_worker/mcp_effect_executor.py",
        "class McpEffectExecutor:\n"
        "    async def apply(self, prepared):\n"
        "        return prepared\n",
    )

    gate = EffectExecutionReachabilityGate(tmp_path)

    assert (
        "agent_runtime.capabilities.tools.rogue:RogueTool"
        " -> agent_runtime.effects.intermediary:dispatch"
        " -> runtime_worker.mcp_effect_executor:McpEffectExecutor"
    ) in gate.violations()
    assert gate.direct_dispatch_violations() == (
        "agent_runtime.effects.intermediary:dispatch:5",
    )


def _write_fixture(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


async def test_stager_object_graph_and_all_legal_flows_leave_exploding_handle_unused() -> (
    None
):
    ledger = FakeLedger()
    outbox = FakeOutbox()
    stager = EffectStager(
        ledger=ledger,
        outbox=outbox,
        clock=FakeClock(),
        stage_ids=FakeStageIds(),
    )
    exploding = ExplodingEffectHandle()
    assert exploding not in vars(stager).values()

    proposed = proposal()
    state = await stager.stage(
        scope=scope(),
        proposed_effect=proposed,
        policy_snapshot=policy_snapshot(),
        actor=user(),
        idempotency_key="stage",
    )
    await stager.decide(
        scope=scope(),
        stage_id=state.stage_id,
        revision=1,
        decision=EffectDecisionKind.APPROVE,
        proposal_digest=proposed.proposal_digest,
        target_digest=proposed.target_digest,
        actor=user(),
        idempotency_key="approve",
    )

    assert exploding.calls == []
    assert outbox.enqueue_calls == 1
