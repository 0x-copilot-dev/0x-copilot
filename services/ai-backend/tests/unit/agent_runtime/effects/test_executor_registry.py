from __future__ import annotations

import pytest

from agent_runtime.effects.executor import EffectExecutionScope, RecordingEffectExecutor
from agent_runtime.effects.executor_registry import (
    EffectExecutorRegistry,
    EffectExecutorRegistryError,
)
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind


def _scope() -> EffectExecutionScope:
    return EffectExecutionScope(
        org_id="org_test",
        user_id="user_test",
        run_id="run_test",
        owner_ref="run://org_test/run_test",
    )


def test_registry_resolves_only_the_registered_executor_kind() -> None:
    registry = EffectExecutorRegistry(
        {EffectExecutorKind.BUILTIN: lambda _scope: RecordingEffectExecutor()}
    )

    assert (
        registry.resolve(kind=EffectExecutorKind.BUILTIN, scope=_scope()).kind
        is EffectExecutorKind.BUILTIN
    )
    with pytest.raises(EffectExecutorRegistryError, match="no executor"):
        registry.resolve(kind=EffectExecutorKind.MCP, scope=_scope())


def test_registry_fails_startup_when_an_enforced_kind_is_missing() -> None:
    registry = EffectExecutorRegistry(
        {EffectExecutorKind.BUILTIN: lambda _scope: RecordingEffectExecutor()}
    )

    with pytest.raises(EffectExecutorRegistryError, match="mcp"):
        registry.require({EffectExecutorKind.BUILTIN, EffectExecutorKind.MCP})
