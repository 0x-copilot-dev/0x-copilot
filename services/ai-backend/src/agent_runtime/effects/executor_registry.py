"""Closed, worker-only resolution of effect executors."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from agent_runtime.effects.executor import EffectExecutionScope, EffectExecutor
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind


class EffectExecutorRegistryError(RuntimeError):
    """Raised when server composition cannot resolve a required executor."""


EffectExecutorFactory = Callable[[EffectExecutionScope], EffectExecutor]


class EffectExecutorRegistry:
    """A closed registry constructed only by the worker composition root.

    The registry accepts no model input and resolves an executor from a verified
    runtime scope, making it impossible for a tool prompt to choose an arbitrary
    transport implementation.
    """

    def __init__(
        self, factories: Mapping[EffectExecutorKind, EffectExecutorFactory]
    ) -> None:
        if not factories:
            raise EffectExecutorRegistryError(
                "effect executor registry cannot be empty"
            )
        self._factories = dict(factories)
        for kind, factory in self._factories.items():
            if not isinstance(kind, EffectExecutorKind) or not callable(factory):
                raise EffectExecutorRegistryError(
                    "invalid effect executor registration"
                )

    def resolve(
        self, *, kind: EffectExecutorKind, scope: EffectExecutionScope
    ) -> EffectExecutor:
        factory = self._factories.get(kind)
        if factory is None:
            raise EffectExecutorRegistryError(
                f"no executor is configured for {kind.value}"
            )
        executor = factory(scope)
        if executor.kind is not kind:
            raise EffectExecutorRegistryError(
                "executor factory returned a mismatched kind"
            )
        return executor

    def require(self, kinds: set[EffectExecutorKind]) -> None:
        missing = kinds - self._factories.keys()
        if missing:
            values = ", ".join(sorted(kind.value for kind in missing))
            raise EffectExecutorRegistryError(
                f"required effect executors missing: {values}"
            )


__all__ = [
    "EffectExecutorFactory",
    "EffectExecutorRegistry",
    "EffectExecutorRegistryError",
]
