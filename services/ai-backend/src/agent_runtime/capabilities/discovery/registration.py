"""The one factory-facing entry point for F3 bridge tool registration.

The runtime factory owns model-tool composition; this module owns the single
question "which discovery bridge tools, if any, may this run expose".  Keeping
that decision here means the factory never grows a second activation
vocabulary, never reimplements the F3 gate, and never has to know how a bridge
tool is built.

Registration is a *narrowing* decision in every direction:

* the posture is read from the F3.1 activation decision, whose own invariants
  already make a widening posture unrepresentable — only ``deferred`` registers
  anything, so ``direct``, ``server``, and ``shadow`` return no tools at all;
* a catalog that cannot mint bound references registers nothing, because a
  reference that cannot be revalidated at use time must never be offered; and
* ``invoke_capability`` is registered only when both its revalidation and its
  non-model executor are wired, so a run never sees a tool that could only
  fail.

Registering fewer tools always falls back to the pre-F3 direct/server
disclosure path, which is unaffected by anything in this module.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from agent_runtime.capabilities.discovery.activation import (
    CapabilityActivationDecision,
)
from agent_runtime.capabilities.discovery.contracts import (
    CapabilityBridgeToolName,
    CapabilityCatalog,
    CapabilityDescribeRequest,
    CapabilityExecutorPort,
    CapabilityInvokeRequest,
    CapabilitySearchRequest,
)
from agent_runtime.capabilities.discovery.ranker import DeterministicLexicalRanker
from agent_runtime.capabilities.discovery.revision_authority import (
    CapabilityRefRevalidation,
)
from agent_runtime.capabilities.discovery.tool_bridge import (
    CapabilityCatalogAccess,
    CapabilityDescribeTool,
    CapabilityInvokeTool,
    CapabilitySearchTool,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract


def _utc_now() -> datetime:
    return datetime.now(UTC)


@runtime_checkable
class CapabilityBridgeToolAdapter(Protocol):
    """The pure-domain surface the factory needs to wrap one bridge tool.

    Deliberately framework-free: this package builds no ``StructuredTool`` and
    imports no model-framework type, so the factory keeps sole ownership of how
    a model tool is composed.
    """

    name: str
    description: str

    async def ainvoke(self, raw_input: Mapping[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CapabilityBridgeToolRegistration:
    """One bridge tool the runtime factory should register, plus its schema.

    The adapter is a pure domain object and the schema is an ordinary runtime
    contract, so the factory wraps these exactly like every other model tool and
    they receive the same display, tool-policy, approval, and budget middleware.
    Nothing here is privileged.
    """

    name: CapabilityBridgeToolName
    adapter: CapabilityBridgeToolAdapter
    args_schema: type[RuntimeContract]


class CapabilityBridgeRegistrar:
    """Decide which bounded F3 bridge tools a run may expose to the model."""

    @staticmethod
    def registrations_for(
        *,
        activation: CapabilityActivationDecision,
        catalog: CapabilityCatalog,
        runtime_context: AgentRuntimeContext,
        executor: CapabilityExecutorPort | None = None,
        revalidation: CapabilityRefRevalidation | None = None,
        ranker: DeterministicLexicalRanker | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> tuple[CapabilityBridgeToolRegistration, ...]:
        """Return the bridge tools to register, or nothing at all.

        The result is empty for ``direct``, ``server``, and ``shadow``, and for
        any catalog that carries no generation and therefore cannot mint a
        revalidatable reference.
        """

        if not activation.registers_bridge:
            return ()
        if catalog.generation is None:
            return ()
        access = CapabilityCatalogAccess(
            catalog=catalog,
            runtime_context=runtime_context,
            clock=clock,
        )
        registrations = [
            CapabilityBridgeToolRegistration(
                name=CapabilityBridgeToolName.SEARCH_CAPABILITIES,
                adapter=CapabilitySearchTool(
                    access=access,
                    ranker=ranker or DeterministicLexicalRanker(),
                ),
                args_schema=CapabilitySearchRequest,
            ),
            CapabilityBridgeToolRegistration(
                name=CapabilityBridgeToolName.DESCRIBE_CAPABILITY,
                adapter=CapabilityDescribeTool(access=access),
                args_schema=CapabilityDescribeRequest,
            ),
        ]
        if executor is not None and revalidation is not None:
            registrations.append(
                CapabilityBridgeToolRegistration(
                    name=CapabilityBridgeToolName.INVOKE_CAPABILITY,
                    adapter=CapabilityInvokeTool(
                        access=access,
                        executor=executor,
                        revalidation=revalidation,
                    ),
                    args_schema=CapabilityInvokeRequest,
                )
            )
        return tuple(registrations)


__all__ = (
    "CapabilityBridgeRegistrar",
    "CapabilityBridgeToolAdapter",
    "CapabilityBridgeToolRegistration",
)
