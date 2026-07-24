from __future__ import annotations

import ast
from pathlib import Path

from agent_runtime.effects.staging import EffectStager
from agent_runtime.surfaces_v2.ledger_models import EffectDecisionKind

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

_EFFECTS_ROOT = (
    Path(__file__).resolve().parents[4] / "src" / "agent_runtime" / "effects"
)
_FORBIDDEN_MODULE_PREFIXES = (
    "agent_runtime.capabilities.mcp",
    "agent_runtime.capabilities.desktop",
    "agent_runtime.capabilities.browser",
    "agent_runtime.capabilities.sandbox",
    "agent_runtime.surfaces_v2.commit_engine",
)


def _forbidden_imports(source: str) -> set[str]:
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return {
        module for module in imports if module.startswith(_FORBIDDEN_MODULE_PREFIXES)
    }


def test_effect_domain_has_no_import_path_to_effect_handles() -> None:
    for source_path in _EFFECTS_ROOT.glob("*.py"):
        assert _forbidden_imports(source_path.read_text()) == set(), source_path


def test_import_scanner_canary_detects_a_planted_violation() -> None:
    planted = "from agent_runtime.capabilities.mcp.client import BackendMcpHttpClient\n"

    assert _forbidden_imports(planted) == {"agent_runtime.capabilities.mcp.client"}


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
