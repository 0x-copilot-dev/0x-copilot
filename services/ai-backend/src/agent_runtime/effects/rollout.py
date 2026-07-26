"""Closed E2 capability dependencies for canonical effect executors.

The request API and worker both act on the same immutable effect-stage
executor.  Keeping this mapping in the effects domain makes an E2 admission
decision identical before a decision/enqueue and before a later commit; no
adapter may substitute a smaller capability set for one of those paths.
"""

from __future__ import annotations

from agent_runtime.rollout import RolloutCapability
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind


def effect_execution_capabilities(
    executor: EffectExecutorKind,
) -> tuple[RolloutCapability, ...]:
    """Return the full closed E2 dependency set for ``executor``.

    Every write-capable executor requires the universal operation/stage/commit
    lanes.  Its adapter-specific lane is additive; it never replaces those
    common controls.
    """

    common = (
        RolloutCapability.OPERATION_GATEWAY,
        RolloutCapability.EFFECT_STAGER,
        RolloutCapability.EFFECT_COMMIT,
    )
    if executor in {EffectExecutorKind.MCP, EffectExecutorKind.BUILTIN}:
        return (*common, RolloutCapability.MCP_GATEWAY)
    if executor is EffectExecutorKind.WORKSPACE:
        return (
            *common,
            RolloutCapability.WORKSPACE_OVERLAY,
            RolloutCapability.WORKSPACE_COMMIT,
        )
    if executor is EffectExecutorKind.BROWSER:
        return (*common, RolloutCapability.BROWSER_ADAPTER)
    if executor is EffectExecutorKind.SANDBOX:
        return (*common, RolloutCapability.SANDBOX_ADAPTER)
    raise ValueError("effect executor has no E2 capability mapping")


__all__ = ("effect_execution_capabilities",)
