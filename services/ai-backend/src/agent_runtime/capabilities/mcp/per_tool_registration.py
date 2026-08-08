"""Register one model tool per real MCP tool. The only MCP dispatch surface.

This is **the flip**. Everything it composes shipped in earlier increments and
was inert: the per-tool :class:`~agent_runtime.capabilities.mcp.tool_source.McpToolSource`
(P2-3), the five middleware stages and the composer (P2-4 / P2-5), the
``tool_name → server_slug`` :class:`~agent_runtime.capabilities.mcp.connector_resolver.ToolConnectorResolver`
(P2-1) and its stream seam (P2-6), and the credential seam (P2-7a). This module
is the one place they meet, and :meth:`McpPerToolRegistrar.build` is the single
entry point ``execution.factory`` calls.

**There is no longer a gateway behind it.** The umbrella ``call_mcp_tool`` --
and the ``MCP_PER_TOOL_ENABLED`` flag that chose between them -- are deleted.
What made the flag necessary was direct-connect: ai-backend would have needed
the vendor's bearer in-process and had no way to get one, so "on" risked
registering a connector surface that could not answer. Routing the library
through ``backend``'s proxy removed that risk rather than accepting it.

**A ``None`` return no longer means "fall back".** It means the run registers no
MCP dispatch tool at all -- the honest surface for a connector plane that did
not resolve, since the alternative is advertising a route that cannot dispatch.

**Three things the flip publishes besides the tools:**

* ``cards_by_urn`` → the POLICY stage, so a GATE can build its interrupt payload
  for the fixed tool it wraps without re-resolving the server.
* the :class:`ToolConnectorResolver` → the worker's stream seam, so a per-tool
  call still carries ``connector_slug`` / ``provenance`` / ``access_mode``.
* a **descriptor-driven** ``interrupt_on`` map — one entry per GATE-eligible
  tool, replacing the single ``call_mcp_tool`` key the name-keyed enforcer used.
  See :class:`McpPerToolInterrupts` for why it is emitted only when the run has
  no in-tool approval channel.

**Reserved names.** The native tool names for the run are passed into the source
as ``reserved_names``, so a connector advertising ``write_todos`` or
``read_file`` is dropped with a typed failure instead of shadowing the builtin —
which would also hijack that builtin's stream provenance, because the shadowed
name would then resolve to a connector in the published map.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import logging
from types import MappingProxyType
from typing import Final

from langchain_core.tools import BaseTool

from agent_runtime.capabilities.mcp.cards import McpLoadError, McpServerCard
from agent_runtime.capabilities.mcp.connection import McpConnectionDirectory
from agent_runtime.capabilities.mcp.connector_resolver import ToolConnectorResolver
from agent_runtime.capabilities.mcp.middleware.citations_tool import (
    McpCitationsMiddleware,
)
from agent_runtime.capabilities.mcp.middleware.compose import ToolMiddlewareComposer
from agent_runtime.capabilities.mcp.middleware.error_map_tool import (
    McpErrorMapMiddleware,
    McpErrorResultTool,
)
from agent_runtime.capabilities.mcp.middleware.exec_policy_tool import (
    McpExecPolicyMiddleware,
)
from agent_runtime.capabilities.mcp.middleware.observe_tool import McpObserveMiddleware
from agent_runtime.capabilities.mcp.middleware.present_tool import (
    ConnectorWriteOpCatalogue,
    McpPresentMiddleware,
)
from agent_runtime.capabilities.mcp.middleware.policy_tool import PolicyToolMiddleware
from agent_runtime.capabilities.mcp.tool_source import (
    AuthorizedCardLister,
    McpToolCatalog,
    McpToolLoaderClientFactory,
    McpToolSource,
    ToolDescriptorPair,
)
from agent_runtime.capabilities.policy.contracts import (
    Action,
    CapabilityDescriptor,
    CredentialProvider,
    ToolMiddleware,
    Trust,
)
from agent_runtime.capabilities.tools.tool_use_enforcement import APPROVAL_DECISIONS
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.surfaces_v2.gate import ToolAccessGate

_LOGGER = logging.getLogger(__name__)


class HarnessBuiltinToolNames:
    """Model tool names the harness itself registers, outside the factory's list.

    ``_local_tool_names`` can only see the tools the factory composes; the Deep
    Agents / LangChain middleware add theirs when the graph is built, so they are
    absent from that set and a connector could take one of their names. The list
    is the framework-owned half of the operations conformance table
    (``capabilities/operations/conformance.py``) — the same names, in one place,
    so "which names are already taken" has a single answer.
    """

    #: Framework-registered names, none of which a connector tool may claim.
    NAMES: Final[frozenset[str]] = frozenset(
        {
            # langchain.agents.middleware.TodoListMiddleware
            "write_todos",
            # delegation.subagents.atlas_task_tool
            "task",
            # deepagents.middleware.filesystem.FilesystemMiddleware
            "execute",
            "ls",
            "read_file",
            "write_file",
            "edit_file",
            "glob",
            "grep",
        }
    )


@dataclass(frozen=True)
class McpRegistryCardSource:
    """Adapt the run's MCP registry to the source's unfiltered card seam.

    :class:`~agent_runtime.capabilities.mcp.tool_source.AuthorizedCardLister`
    wants ``list_server_cards()`` with no arguments and applies the permission
    gate itself; ``DynamicMcpRegistry.list_server_cards`` takes a context and
    has already applied it. Reading the providers directly keeps the
    authorization decision in exactly one place — the lister — instead of
    filtering twice with two subtly different rules.
    """

    registry: object

    async def list_server_cards(self) -> tuple[McpServerCard, ...]:
        """Return every registered card, unfiltered, across all providers."""

        cards: list[McpServerCard] = []
        for provider in getattr(self.registry, "providers", ()) or ():
            lister = getattr(provider, "list_server_cards", None)
            if not callable(lister):
                continue
            listed = await lister()
            cards.extend(card for card in listed if isinstance(card, McpServerCard))
        return tuple(cards)


class McpPerToolInterrupts:
    """The descriptor-driven ``interrupt_on`` map that replaces the single key.

    Under the legacy gateway there was exactly one entry to make —
    ``call_mcp_tool`` — and the tool-use policy decided whether to make it. Under
    per-tool registration that name does not exist, so the declaration has to be
    derived per tool from what the tool *is*: :meth:`eligible` reads
    ``action × trust`` off the descriptor, which is the same pair the PDP's
    approval stage reads.

    Eligible = anything that is not a **trusted read**. That is the one cell of
    the PDP matrix (``service.py`` §3.3/§3.5) that resolves to ALLOW under the
    deployment default, so it is the one tool class that must never cost the user
    an approval.

    **Why this is emitted only when there is no in-tool gate.** The POLICY stage
    is the primary gate: it parks on
    :meth:`~agent_runtime.surfaces_v2.gate.ToolAccessGate.park_for_approval`
    *after* the PDP has decided, so it gates exactly the calls the PDP said to
    gate. Deep Agents' ``interrupt_on`` middleware interrupts *before* the tool
    runs, for every call of a listed name, and knows nothing of the decision — so
    listing a tool that the POLICY stage will also park raises **two** approvals
    for one write. Where the two cannot both fire is precisely where this map
    earns its keep: ``ToolAccessGate`` is built only when a provider offers OAuth
    (``factory._tool_access_gate``), so a non-OAuth connector has ``gate=None``
    and its writes are refused outright today ("approval is not available for
    this run"). With the map, they park and can be approved.
    """

    @classmethod
    def eligible(cls, descriptor: CapabilityDescriptor) -> bool:
        """Whether this capability could ever require approval (action × trust)."""

        return (
            descriptor.action is not Action.READ or descriptor.trust is Trust.UNTRUSTED
        )

    @classmethod
    def build(
        cls,
        pairs: Sequence[ToolDescriptorPair],
        *,
        gate: ToolAccessGate | None,
    ) -> Mapping[str, object]:
        """Return ``{tool_name: interrupt config}`` for the GATE-eligible tools.

        Empty when an in-tool approval channel exists — the POLICY stage owns the
        park in that case, and a second name-keyed gate would double-prompt.
        """

        if gate is not None:
            return MappingProxyType({})
        return MappingProxyType(
            {
                tool.name: {"allowed_decisions": list(APPROVAL_DECISIONS)}
                for tool, descriptor in pairs
                if cls.eligible(descriptor)
            }
        )


@dataclass(frozen=True)
class McpPerToolCollaborators:
    """The credential-plane objects the per-tool source cannot invent itself.

    Injected rather than constructed here for two reasons. One is testability:
    the whole flip is exercisable with fakes, no network and no keychain. The
    other is that the desktop provider is **blocked** on a token writer (P2-7a),
    so "there is no credential plane" has to be an ordinary, representable state
    rather than an import-time assumption — see the module docstring.
    """

    directory: McpConnectionDirectory
    credentials: CredentialProvider
    #: Overridden only by tests; production uses the source's own default
    #: (``DirectMcpClientFactory`` → one ``MultiServerMCPClient``).
    client_factory: McpToolLoaderClientFactory | None = None


@dataclass(frozen=True)
class McpPerToolRegistration:
    """What one successful flip produced, ready for the factory to register.

    Read-only by construction: the maps are snapshots, not live views, so a
    holder cannot edit the registration after the fact.
    """

    #: Fully-wrapped model tools, one per registered MCP tool.
    tools: tuple[BaseTool, ...]
    #: Descriptor-driven Deep Agents approval config (see
    #: :class:`McpPerToolInterrupts` — usually empty).
    interrupt_on: Mapping[str, object]
    #: ``tool_name → server_slug`` for the worker's stream seams.
    resolver: ToolConnectorResolver
    #: ``CapabilityDescriptor.urn → McpServerCard``, as the POLICY stage saw it.
    cards_by_urn: Mapping[str, McpServerCard]
    #: Per-connector load failures, safe for model and API surfaces.
    failures: tuple[McpLoadError, ...]

    #: The registered tool names — what the factory must not double-register.
    @property
    def tool_names(self) -> frozenset[str]:
        """The names this registration occupies on the model tool surface."""

        return frozenset(tool.name for tool in self.tools)


class McpPerToolRegistrar:
    """Build the per-tool MCP model tool surface, or decline to.

    The single composition entry point for the flip. Declining is always
    expressed as ``None``, never as an empty registration, so the factory has one
    unambiguous signal for "keep the legacy gateway".
    """

    @classmethod
    async def build(
        cls,
        *,
        runtime_context: AgentRuntimeContext,
        mcp_registry: object,
        collaborators: McpPerToolCollaborators | None,
        gate: ToolAccessGate | None,
        reserved_names: frozenset[str],
    ) -> McpPerToolRegistration | None:
        """Return the per-tool registration, or ``None`` to keep the legacy path.

        ``None`` for any of: no credential plane resolved; the registry exposes
        no MCP seam; the load raised; the load registered nothing. There is no
        gateway behind this ``None`` -- the run simply gets no MCP dispatch tool.
        """

        if collaborators is None:
            return None
        if not callable(getattr(mcp_registry, "resolve_server", None)):
            return None

        source = McpToolSource(
            context=runtime_context,
            cards=AuthorizedCardLister(cards=McpRegistryCardSource(mcp_registry)),
            directory=collaborators.directory,
            credentials=collaborators.credentials,
            client_factory=collaborators.client_factory,
            reserved_names=reserved_names | HarnessBuiltinToolNames.NAMES,
        )
        pairs = await cls._load(source, runtime_context=runtime_context)
        if not pairs:
            return None

        stack = cls._stack(
            runtime_context=runtime_context,
            catalog=source.catalog,
            gate=gate,
        )
        return McpPerToolRegistration(
            tools=tuple(cls._compose(pair, stack) for pair in pairs),
            interrupt_on=McpPerToolInterrupts.build(pairs, gate=gate),
            resolver=source.catalog.connector_resolver(),
            cards_by_urn=source.catalog.cards_by_urn,
            failures=source.catalog.failures,
        )

    @classmethod
    async def _load(
        cls,
        source: McpToolSource,
        *,
        runtime_context: AgentRuntimeContext,
    ) -> list[ToolDescriptorPair]:
        """Load the pairs, treating a total load failure as "no per-tool surface".

        The source already converts every *per-server* failure into a typed
        record and keeps going, so reaching this handler means the load itself
        broke — a defect in the credential plane or the client. The run keeps the
        legacy gateway rather than losing its connectors; the detail is logged
        for operators and never described to the model.
        """

        try:
            return await source.load()
        except Exception:  # noqa: BLE001 — a broken load must not fail the run
            _LOGGER.warning(
                "mcp_per_tool_load_failed",
                extra={"metadata": {"trace_id": runtime_context.trace_id}},
                exc_info=True,
            )
            return []

    @classmethod
    def _stack(
        cls,
        *,
        runtime_context: AgentRuntimeContext,
        catalog: McpToolCatalog,
        gate: ToolAccessGate | None,
    ) -> tuple[ToolMiddleware, ...]:
        """The fixed pipeline, outermost first — asserted by the composer.

        Written in ``MIDDLEWARE_ORDER`` order on purpose: the composer refuses
        anything else, so this tuple is a declaration rather than a hint.
        """

        return (
            PolicyToolMiddleware(
                runtime_context=runtime_context,
                cards_by_urn=catalog.cards_by_urn,
                gate=gate,
            ),
            McpExecPolicyMiddleware(),
            McpObserveMiddleware(),
            McpErrorMapMiddleware(),
            McpCitationsMiddleware(),
            # The PRESENT stage is handed the run's WRITE descriptors because
            # only this method has seen every tool the run loaded. A read
            # presented without them mints a surface no Save can ever compose
            # against — the descriptors exist nowhere else once the session ends.
            McpPresentMiddleware(
                write_ops=ConnectorWriteOpCatalogue.from_pairs(catalog.pairs)
            ),
        )

    @classmethod
    def _compose(
        cls,
        pair: ToolDescriptorPair,
        stack: tuple[ToolMiddleware, ...],
    ) -> BaseTool:
        """Wrap one pair in the pipeline, then in the failure renderer.

        The renderer is outside the pipeline, not a sixth stage: ERROR_MAP raises
        its normalized :class:`McpToolCallError` so EXEC_POLICY can read it and
        decide about a retry, and only once that decision is final may the
        failure become a value the model sees.
        """

        return McpErrorResultTool.wrapping(ToolMiddlewareComposer.compose(pair, stack))


__all__ = [
    "HarnessBuiltinToolNames",
    "McpPerToolCollaborators",
    "McpPerToolInterrupts",
    "McpPerToolRegistrar",
    "McpPerToolRegistration",
    "McpRegistryCardSource",
]
