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

**The bridge seam.**  A catalog holds MCP *server* cards, so a search that only
ranks the catalog can only answer at server granularity: nothing it returns is
something the model can describe into parameters or invoke.  Joining the two
halves is therefore a registration decision, and :class:`CapabilityBridgeSeam`
is that join stated once — one bounded second-tier search, and the one
run-scoped ledger it records into.  Because the ledger is also what the executor
resolves dispatch coordinates through, composing the seam is what makes search,
describe, and invoke a chain rather than three tools that individually pass.

The seam is optional in exactly the way every other input here is: a run that
supplies none registers the catalog-only search it registered before, and a run
that supplies one registers a search that reaches capabilities.  What this
module still refuses to know is *how* a capability is dispatched — it composes
the ledger and takes the executor as the
:class:`~agent_runtime.capabilities.discovery.contracts.CapabilityExecutorPort`
protocol, never the concrete gateway executor, so tool composition and dispatch
keep separate owners and this module imports no model-framework type.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, Self, runtime_checkable

from agent_runtime.capabilities.discovery.activation import (
    CapabilityActivationDecision,
    CapabilityExpansionLimits,
)
from agent_runtime.capabilities.discovery.contracts import (
    CapabilityBridgeToolName,
    CapabilityCatalog,
    CapabilityDescribeRequest,
    CapabilityExecutorPort,
    CapabilityInvokeRequest,
    CapabilityReferenceMinter,
    CapabilitySearchRequest,
)
from agent_runtime.capabilities.discovery.dispatch import (
    RunScopedCapabilityDisclosure,
)
from agent_runtime.capabilities.discovery.expansion import (
    BoundedCapabilityExpander,
    TwoTierCapabilitySearch,
)
from agent_runtime.capabilities.discovery.ranker import DeterministicLexicalRanker
from agent_runtime.capabilities.discovery.revision_authority import (
    CapabilityRefRevalidation,
)
from agent_runtime.capabilities.discovery.schema_artifacts import (
    RunScopedSchemaArtifactPublisher,
)
from agent_runtime.capabilities.discovery.telemetry import (
    CapabilityDiscoveryObserver,
    CapabilityExpansionObserver,
    ObservedCapabilityBridgeTool,
    ObservedTwoTierCapabilitySearch,
)
from agent_runtime.capabilities.discovery.tool_bridge import (
    CapabilityCatalogAccess,
    CapabilityDescribeTool,
    CapabilityInvokeTool,
    CapabilitySearchTool,
)
from agent_runtime.capabilities.mcp.loader import McpLoader
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


@dataclass(frozen=True)
class CapabilityBridgeSeam:
    """The one run-scoped pair a bridge that can actually act is built from.

    ``expansion`` is what turns a server card into records the model can name,
    and ``disclosure`` is the run-scoped ledger those records are recorded in.
    They are held together because they are useless apart: an expansion whose
    output nothing records produces refs no other tool can resolve, and a ledger
    nothing writes to holds nothing to dispatch.  Pairing them means a call site
    cannot wire one without the other, and — because the executor resolves its
    dispatch coordinates through the very same ledger — cannot accidentally give
    the executor a *different* one.

    :meth:`compose` is the intended construction path.  The minter must be keyed
    exactly as the catalog builder's was: expansion mints refs for the same
    catalog id, so a different key would produce references the run's own
    catalog identity does not explain.

    An ``observer`` is the only optional input that changes nothing about what
    the seam *does*.  Tier two is the one place the cost of opening real servers
    is visible — the bridge's own answer deliberately carries no expansion audit
    — so measuring it has to happen here or not at all.  A run that supplies
    none composes the unmeasured second tier it composed before.
    """

    disclosure: RunScopedCapabilityDisclosure
    expansion: TwoTierCapabilitySearch

    @classmethod
    def compose(
        cls,
        *,
        catalog: CapabilityCatalog,
        loader: McpLoader,
        minter: CapabilityReferenceMinter,
        limits: CapabilityExpansionLimits | None = None,
        ranker: DeterministicLexicalRanker | None = None,
        observer: CapabilityExpansionObserver | None = None,
    ) -> Self:
        """Build the bounded second tier and the ledger it discloses into."""

        shared_ranker = ranker or DeterministicLexicalRanker()
        expander = BoundedCapabilityExpander(
            loader=loader,
            minter=minter,
            limits=limits,
            ranker=shared_ranker,
        )
        expansion = (
            TwoTierCapabilitySearch(expander=expander, ranker=shared_ranker)
            if observer is None
            else ObservedTwoTierCapabilitySearch(
                expander=expander,
                ranker=shared_ranker,
                observer=observer,
            )
        )
        return cls(
            disclosure=RunScopedCapabilityDisclosure(catalog=catalog),
            expansion=expansion,
        )


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
        seam: CapabilityBridgeSeam | None = None,
        schema_artifacts: RunScopedSchemaArtifactPublisher | None = None,
        local_tool_names: frozenset[str] = frozenset(),
        observer: CapabilityDiscoveryObserver | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> tuple[CapabilityBridgeToolRegistration, ...]:
        """Return the bridge tools to register, or nothing at all.

        The result is empty for ``direct``, ``server``, and ``shadow``, and for
        any catalog that carries no generation and therefore cannot mint a
        revalidatable reference.  A ``seam`` built for a different catalog is
        refused rather than mounted, because a ledger that vouches for another
        projection's refs is not run-scoped at all.

        ``schema_artifacts`` is the F3.4 publisher describe defers an over-bound
        schema to.  Like every other optional input it only ever *adds* an
        answer: a run that supplies none reports such a schema ``unavailable``
        rather than falling back to a truncated one, and the in-bound path — the
        ordinary case — is byte-identical either way.

        An ``observer`` is applied uniformly to whatever this method decided to
        register, which is why it is threaded here rather than at each adapter's
        own construction: measuring the bridge is then a property of *being
        registered*, and a fourth bridge tool cannot be added unobserved.  It
        can only widen what is measured, never what is exposed — the activation
        and catalog narrowing above have already run.
        """

        if not activation.registers_bridge:
            return ()
        if catalog.generation is None:
            return ()
        access = CapabilityCatalogAccess(
            catalog=catalog,
            runtime_context=runtime_context,
            clock=clock,
            disclosure=None if seam is None else seam.disclosure,
        )
        registrations = [
            CapabilityBridgeToolRegistration(
                name=CapabilityBridgeToolName.SEARCH_CAPABILITIES,
                adapter=CapabilitySearchTool(
                    access=access,
                    ranker=ranker or DeterministicLexicalRanker(),
                    expansion=None if seam is None else seam.expansion,
                    local_tool_names=local_tool_names,
                ),
                args_schema=CapabilitySearchRequest,
            ),
            CapabilityBridgeToolRegistration(
                name=CapabilityBridgeToolName.DESCRIBE_CAPABILITY,
                adapter=CapabilityDescribeTool(
                    access=access,
                    schema_artifacts=schema_artifacts,
                ),
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
        if observer is None:
            return tuple(registrations)
        return tuple(
            CapabilityBridgeToolRegistration(
                name=registration.name,
                adapter=ObservedCapabilityBridgeTool(
                    inner=registration.adapter,
                    observer=observer,
                    tool=registration.name,
                ),
                args_schema=registration.args_schema,
            )
            for registration in registrations
        )


__all__ = (
    "CapabilityBridgeRegistrar",
    "CapabilityBridgeSeam",
    "CapabilityBridgeToolAdapter",
    "CapabilityBridgeToolRegistration",
)
