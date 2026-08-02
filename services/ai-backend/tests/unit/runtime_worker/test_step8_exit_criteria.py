"""F3.8 — Step 8's exit criteria, judged against the *wired* path.

Every criterion in PRD §11 Step 8 was proved by some lane, but several were
proved inside the discovery package with the seam, the executor, and the
revalidation composed **by the test**.  W2 already showed why that is not the
same claim: every unit passed while production reached the registrar with three
seams unset and the feature did nothing.  This module therefore re-asks the same
questions of the object the worker actually builds — `DefaultRuntimeDependenciesFactory`
plus `compose_capability_discovery` — and substitutes only the connector.

What is asserted here, and why it was not already:

* **cold discovery opens at most K servers** — F3.3 proved the bound at the
  expander with an injected limit.  Nothing proved it of the limit the
  composition root actually composes, over a catalog larger than ``K``.
* **warm discovery performs no duplicate list** — F3.3 proved coalescing *within*
  one expansion with a negative control.  A second search in the same run is a
  different question, and it is answered by the F8 discovery cache the worker
  wires and the wiring harness previously did not.
* **revocation/schema change between describe and invoke fails safely** — the
  package proved the schema half.  Revocation, disappearance, and the honest
  limits of the F8 route are proved here, end to end.
* **unauthorized names cannot be searched, described, guessed, or invoked** —
  the F1 case is fixture-authored.  Here an unauthorized server is really
  present in the registry and the "guess" is minted with the run's *own*
  reference key, so what is under test is authorization rather than the secrecy
  of an opaque string.
* **direct/server fallback remains available** — proved as a posture switch on a
  fully-configured deployment, including that the MCP card block the deferred
  posture suppresses comes back.

Two criteria are pinned as *canaries* rather than asserted as working, in the
house style of ``test_no_expanded_capability_can_currently_reach_the_artifact_branch``:
the expansion-limit environment knobs never reach the composed seam, and the
composition root wires no F8 descriptor-revision source.  Both fail the moment
someone threads them, which is the moment to prove the routes they enable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import os
from typing import Any

import pytest

from agent_runtime.capabilities.discovery import (
    CapabilityBridgeToolName,
    CapabilityCatalog,
    CapabilityDiscoveryErrorCode,
    HmacCapabilityReferenceMinter,
)
from agent_runtime.capabilities.discovery.activation import CapabilityExpansionLimits
from agent_runtime.capabilities.discovery.contracts import CapabilityExpansionBounds
from agent_runtime.capabilities.mcp import DynamicMcpRegistry
from agent_runtime.capabilities.mcp.annotations import (
    McpToolAnnotations,
    McpToolAnnotationsRegistry,
)
from agent_runtime.capabilities.mcp.constants import Values as McpValues
from agent_runtime.capabilities.mcp.discovery_cache import McpDiscoveryCache
from agent_runtime.capabilities.mcp.gateway_context import McpOperationGatewayContext
from agent_runtime.capabilities.mcp.revision_resolver import (
    McpDescriptorRevisionResolver,
)
from agent_runtime.capabilities.mcp.revision_wire import (
    BackendMcpRevision,
    BackendMcpRevisionNotFound,
    BackendMcpRevisionNotice,
)
from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.control_plane.context import RunControlContext
from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    CapabilityBridgeComposition,
    RuntimeDependencies,
)
from runtime_worker.capability_discovery_composition import (
    CapabilityDiscoveryEnvironment,
    build_capability_discovery_composer,
)
from runtime_worker.dependencies import (
    DefaultRuntimeDependenciesFactory,
    compose_capability_discovery,
)

from tests.unit.runtime_worker.test_capability_bridge_wiring import (
    BridgeWiringHarness,
    _SECRET,
)


_READ_TOOL = "list_issues"
_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"team": {"type": "string"}, "limit": {"type": "integer"}},
    "required": ["team"],
    "additionalProperties": False,
}
_MOVED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"team": {"type": "string"}, "cursor": {"type": "string"}},
    "required": ["team", "cursor"],
    "additionalProperties": False,
}
#: The scope the run in :meth:`BridgeWiringHarness.context` does *not* hold.
_WITHHELD_SCOPE = "hr:admin"
_UNAUTHORIZED_SERVER = "payroll"
_UNAUTHORIZED_SERVER_ID = "srv_payroll"


@dataclass
class _CountingClient:
    """A connector that records every descriptor listing it was asked for.

    ``list_tools`` is the round trip both halves of the discovery criterion are
    counted in: one per *cold* server open, and zero for a warm one when the F8
    cache is doing its job.
    """

    tools: Sequence[object]
    outputs: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    listings: int = 0
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def connect(self) -> None:
        return None

    async def list_tools(self) -> Sequence[object]:
        self.listings += 1
        return self.tools

    async def list_resources(self) -> Sequence[object]:
        return ()

    async def call_tool(self, *, tool_name: str, arguments: Mapping[str, object]):  # type: ignore[no-untyped-def]
        self.calls.append((tool_name, dict(arguments)))
        return self.outputs.get(tool_name, {"items": [{"id": "L-1"}]})


@dataclass
class _MutableProvider:
    """A provider whose card list and clients can move mid-run, as a real one can.

    Revocation and descriptor drift are events that happen *between* two model
    turns, so proving they fail closed needs a provider that can change between
    two tool calls rather than two providers compared side by side.
    """

    cards: list[Any]
    clients: dict[str, _CountingClient]

    async def list_server_cards(self) -> Sequence[Any]:
        return tuple(self.cards)

    def create_client(self, card: Any) -> _CountingClient:
        return self.clients[card.name]


class _RevisionBackend:
    """The MCP control plane's revision authority, as a substitutable backend.

    Only the HTTP boundary is faked. The resolver above it is the production
    :class:`McpDescriptorRevisionResolver` the worker builds, with its real
    single-flight, TTL, generation fencing, and notice handling — so what these
    cases exercise is the wiring into that resolver, not a re-implementation of
    it. ``revisions`` moves exactly as a backend's answer moves when a server's
    descriptors change.
    """

    def __init__(self, revisions: Mapping[str, str]) -> None:
        self.revisions = dict(revisions)
        self.exact_calls = 0

    async def get_exact(
        self, *, org_id: str, user_id: str, server_id: str
    ) -> BackendMcpRevision:
        del org_id, user_id
        self.exact_calls += 1
        revision = self.revisions.get(server_id)
        if revision is None:
            raise BackendMcpRevisionNotFound(server_id)
        return BackendMcpRevision.model_validate(
            {
                "server_id": server_id,
                "revision": revision,
                "subject_scope_hash": "scope-step8",
                "profile_id": "profile-step8",
                "config_generation": 1,
                "auth_generation": 1,
                "transport_generation": 1,
                "tool_filter_generation": 1,
                "tool_count": 1,
                "resource_count": 0,
                "descriptor_digest": f"digest-{revision}",
                "observed_at": "2026-01-01T00:00:00Z",
                "source": "backend",
            }
        )

    def notice(self, *, server_id: str, new_revision: str) -> BackendMcpRevisionNotice:
        """The feed notice the backend emits when that server's revision moves."""

        return BackendMcpRevisionNotice.model_validate(
            {
                "cursor": "cursor-step8",
                "notice_id": f"notice-{server_id}-{new_revision}",
                "sequence_no": 1,
                "server_id": server_id,
                "profile_id": "profile-step8",
                "subject_scope_hash": "scope-step8",
                "new_revision": new_revision,
                "reason": "descriptor_observed",
                "occurred_at": "2026-01-01T00:01:00Z",
            }
        )


class Step8Harness(BridgeWiringHarness):
    """The W2 composition root, plus the fixtures each criterion needs."""

    @staticmethod
    def revision_authority(
        revisions: Mapping[str, str],
    ) -> tuple[_RevisionBackend, McpDescriptorRevisionResolver]:
        """The worker's own F8 resolver over a substitutable backend."""

        backend = _RevisionBackend(revisions)
        return backend, McpDescriptorRevisionResolver(backend)  # type: ignore[arg-type]

    async def move_descriptors(
        self,
        *,
        backend: _RevisionBackend,
        resolver: McpDescriptorRevisionResolver,
        context: AgentRuntimeContext,
        server_id: str,
        revision: str,
    ) -> None:
        """Move one server's descriptors the way the F8 feed reports it.

        The backend's answer changes and the notice is applied through the
        resolver's own ``apply_notice`` — the exact call
        ``McpRevisionFeedCoordinator`` makes for every notice it reads. Nothing
        here reaches past the control plane's public surface to force the
        outcome.
        """

        backend.revisions[server_id] = revision
        await resolver.apply_notice(
            org_id=context.org_id,
            user_id=context.user_id,
            notice=backend.notice(server_id=server_id, new_revision=revision),
        )

    def counting_card(self, name: str, *, server_id: str, scope: str = "docs:read"):  # type: ignore[no-untyped-def]
        return self.make_card(
            name=name,
            short_description=f"Track and read {name} issues for a team.",
            required_scopes=(scope,),
        ).model_copy(update={"server_id": server_id})

    def counting_client(self, name: str, *, schema: Mapping[str, Any] | None = None):  # type: ignore[no-untyped-def]
        return _CountingClient(
            tools=(
                self.make_tool(
                    name=_READ_TOOL,
                    description=f"List the issues assigned to one {name} team.",
                    input_schema=dict(schema or _READ_SCHEMA),
                ),
            ),
        )

    def many_servers(self, count: int) -> tuple[_MutableProvider, DynamicMcpRegistry]:
        """A catalog deliberately larger than ``K``, all matching one query."""

        names = [f"issues{index}" for index in range(count)]
        provider = _MutableProvider(
            cards=[self.counting_card(name, server_id=f"srv_{name}") for name in names],
            clients={name: self.counting_client(name) for name in names},
        )
        return provider, DynamicMcpRegistry(providers=(provider,))

    def one_server(
        self,
        *,
        schema: Mapping[str, Any] | None = None,
    ) -> tuple[_MutableProvider, DynamicMcpRegistry]:
        provider = _MutableProvider(
            cards=[self.counting_card("issues0", server_id="srv_issues0")],
            clients={"issues0": self.counting_client("issues0", schema=schema)},
        )
        return provider, DynamicMcpRegistry(providers=(provider,))

    @staticmethod
    def cached_factory(cache: object) -> DefaultRuntimeDependenciesFactory:
        """The worker's factory with the F8 discovery cache production wires.

        ``runtime_worker/__main__.py`` builds one cache per worker process and
        passes it exactly here, so a warm-path claim measured without it would
        be measuring a deployment nobody runs.
        """

        from agent_runtime.settings import RuntimeSettings  # noqa: PLC0415

        return DefaultRuntimeDependenciesFactory(
            RuntimeSettings.load(environ={}),
            mcp_discovery_cache=cache,
            capability_discovery=build_capability_discovery_composer(),
        )

    async def cached_dependencies(
        self,
        context: AgentRuntimeContext,
        *,
        registry: object,
        cache: object,
    ) -> RuntimeDependencies:
        factory = self.cached_factory(cache)
        token = RunControlContext.bind_for_run(self.binding(run_id=context.run_id))
        try:
            base = factory(context).model_copy(update={"mcp_registry": registry})
            return await compose_capability_discovery(factory, base, context)
        finally:
            RunControlContext.unbind(token)

    def run_key(self, context: AgentRuntimeContext) -> bytes:
        """This run's real F3 reference key, recomputed by production code."""

        key = CapabilityDiscoveryEnvironment.reference_key(
            run_id=context.run_id,
            environ=dict(os.environ),
        )
        assert key is not None
        return key

    @staticmethod
    def error_code(answer: Mapping[str, Any]) -> str:
        assert "error" in answer, answer
        error = answer["error"]
        return str(error.get("code", error))


@pytest.fixture
def deferred_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CapabilityDiscoveryEnvironment.ACTIVATION, "deferred")
    monkeypatch.setenv(CapabilityDiscoveryEnvironment.REFERENCE_SECRET, _SECRET)
    monkeypatch.delenv(
        CapabilityDiscoveryEnvironment.CATALOG_TTL_SECONDS, raising=False
    )


@pytest.mark.usefixtures("deferred_environment")
class TestColdDiscoveryOpensAtMostK(Step8Harness):
    """ "cold discovery opens at most K servers" — through the composed seam."""

    async def test_a_catalog_far_larger_than_k_opens_at_most_k_servers(self) -> None:
        """Nine authorized servers, one query that matches them all, K opens.

        F3.3 proved the bound against an injected ``CapabilityExpansionLimits``.
        The value production actually runs under is whatever
        ``factory.py`` hands ``CapabilityBridgeSeam.compose``, and that is what
        this counts: every server whose descriptors were listed during one
        ``search_capabilities`` call.
        """

        context = self.context()
        provider, registry = self.many_servers(9)
        dependencies = await self.dependencies(context, registry=registry)
        tools = self.tools_by_name(await self.graph_request(context, dependencies))
        limit = CapabilityExpansionLimits().max_servers

        run_token = self.bound_run(context)
        try:
            found = await self.call(
                tools[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value],
                query="issues",
                limit=10,
            )
        finally:
            RunControlContext.unbind(run_token)

        assert "error" not in found, found
        opened = [name for name, client in provider.clients.items() if client.listings]
        # Exactly ``K``, not merely at most: all nine cards match the query, so a
        # saturated bound is the only correct answer. A bound nothing reaches is
        # not a bound, and this fails both if the fan-out widens (nine opens) and
        # if the second tier quietly stops expanding (zero).
        assert len(opened) == limit, opened

    async def test_the_catalog_the_bound_applies_to_is_genuinely_larger(self) -> None:
        """The negative control: nine cards were authorized and indexed."""

        context = self.context()
        _provider, registry = self.many_servers(9)
        dependencies = await self.dependencies(context, registry=registry)
        catalog = dependencies.capability_catalog

        assert isinstance(catalog, CapabilityCatalog)
        assert len(catalog.entries) == 9
        assert len(catalog.entries) > CapabilityExpansionLimits().max_servers

    async def test_a_configured_expansion_limit_changes_how_many_servers_open(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """BUG-13, inverted: the operator's ``K`` is the ``K`` production runs.

        This case previously pinned the opposite. ``CapabilityExpansionLimits.from_environment``
        reads three documented knobs and had **no production call site**: the
        worker composition root left ``CapabilityBridgeComposition.expansion_limits``
        unset, ``factory.py`` forwarded that ``None``, and
        ``BoundedCapabilityExpander`` fell back to the hard defaults, so the
        effective ``K`` was always 3 whatever an operator configured.

        Now the composition root resolves the limits and threads them, and the
        proof is the same one the default bound gets: nine authorized servers,
        one query that matches them all, and a count of the servers whose
        descriptors were actually listed. Five is neither the default (3) nor
        the structural ceiling (8), so only a genuinely plumbed value can
        produce it.
        """

        monkeypatch.setenv(CapabilityExpansionLimits.Env.MAX_SERVERS, "5")
        context = self.context()
        provider, registry = self.many_servers(9)
        dependencies = await self.dependencies(context, registry=registry)
        tools = self.tools_by_name(await self.graph_request(context, dependencies))

        run_token = self.bound_run(context)
        try:
            found = await self.call(
                tools[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value],
                query="issues",
                limit=10,
            )
        finally:
            RunControlContext.unbind(run_token)

        assert "error" not in found, found
        opened = [name for name, client in provider.clients.items() if client.listings]
        assert len(opened) == 5, opened
        assert len(opened) != CapabilityExpansionLimits().max_servers

    async def test_an_out_of_range_expansion_limit_opens_the_default_not_the_ceiling(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The direction of the failure is the property, not merely that it fails.

        A typo must never be able to *raise* fan-out. ``999`` is out of range,
        and the contract's rule is that anything unreadable resolves to the
        conservative default rather than to the structural ceiling — so what
        reaches the seam here is 3, not 8. Measured through the wired seam
        rather than on the contract, because the contract's own rule was already
        proved and it was the wiring that did not exist.
        """

        monkeypatch.setenv(CapabilityExpansionLimits.Env.MAX_SERVERS, "999")
        context = self.context()
        provider, registry = self.many_servers(9)
        dependencies = await self.dependencies(context, registry=registry)
        tools = self.tools_by_name(await self.graph_request(context, dependencies))

        run_token = self.bound_run(context)
        try:
            found = await self.call(
                tools[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value],
                query="issues",
                limit=10,
            )
        finally:
            RunControlContext.unbind(run_token)

        assert "error" not in found, found
        opened = [name for name, client in provider.clients.items() if client.listings]
        assert len(opened) == CapabilityExpansionLimits().max_servers, opened
        assert len(opened) < CapabilityExpansionBounds.MAX_SERVERS

    def test_exactly_one_production_call_site_resolves_the_expansion_limits(
        self,
    ) -> None:
        """One reader, so there is one effective ``K`` rather than several.

        The canary that used to assert *no* caller is kept pointing the other
        way. A second call site would be a second answer to "what is K", and the
        run would be bounded by whichever one happened to reach the seam.
        ``CapabilityBridgeComposition`` still defaults the field to ``None`` —
        the wiring supplies it, the contract does not presume it.
        """

        assert CapabilityBridgeComposition().expansion_limits is None
        from pathlib import Path  # noqa: PLC0415

        source_root = Path(__file__).resolve().parents[3] / "src"
        callers = sorted(
            path.relative_to(source_root).as_posix()
            for path in source_root.rglob("*.py")
            if "CapabilityExpansionLimits.from_environment(" in path.read_text("utf-8")
        )
        assert callers == ["runtime_worker/capability_discovery_composition.py"]


@pytest.mark.usefixtures("deferred_environment")
class TestWarmDiscoveryPerformsNoDuplicateList(Step8Harness):
    """ "warm discovery performs no duplicate list" — across two searches."""

    async def test_a_second_search_in_the_same_run_lists_nothing_again(self) -> None:
        """The cache the worker wires is what makes the second tier cheap twice.

        F3.3's coalescing proof covers one expansion. This is the question the
        criterion actually asks: having opened a server once, does searching
        again pay for it again? It can only be answered with the F8 discovery
        cache in place, which is why this composes the factory the way
        ``runtime_worker/__main__.py`` does rather than the way the W2 harness
        did.
        """

        context = self.context()
        provider, registry = self.many_servers(2)
        dependencies = await self.cached_dependencies(
            context,
            registry=registry,
            cache=McpDiscoveryCache(),
        )
        tools = self.tools_by_name(await self.graph_request(context, dependencies))
        search = tools[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value]

        run_token = self.bound_run(context)
        try:
            first = await self.call(search, query="issues", limit=10)
            cold = {name: client.listings for name, client in provider.clients.items()}
            second = await self.call(search, query="issues", limit=10)
            warm = {name: client.listings for name, client in provider.clients.items()}
        finally:
            RunControlContext.unbind(run_token)

        assert "error" not in first, first
        assert "error" not in second, second
        assert sum(cold.values()) > 0, "the cold search opened nothing"
        assert warm == cold, (cold, warm)

    async def test_without_the_cache_the_second_search_does_list_again(self) -> None:
        """The negative control: the warm claim is a property of the cache.

        Without it the same two searches list every admitted server twice, so
        the assertion above is measuring the F8 cache rather than a fixture that
        happens to answer once.
        """

        context = self.context()
        provider, registry = self.many_servers(2)
        dependencies = await self.dependencies(context, registry=registry)
        tools = self.tools_by_name(await self.graph_request(context, dependencies))
        search = tools[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value]

        run_token = self.bound_run(context)
        try:
            await self.call(search, query="issues", limit=10)
            cold = sum(client.listings for client in provider.clients.values())
            await self.call(search, query="issues", limit=10)
            warm = sum(client.listings for client in provider.clients.values())
        finally:
            RunControlContext.unbind(run_token)

        assert cold > 0
        assert warm == 2 * cold, (cold, warm)


@pytest.mark.usefixtures("deferred_environment")
class TestRevocationBetweenDescribeAndInvokeFailsSafely(Step8Harness):
    """ "revocation/schema change between describe and invoke fails safely"."""

    async def _described(  # type: ignore[no-untyped-def]
        self,
        context: AgentRuntimeContext,
        registry: object,
        *,
        descriptor_revision_resolver: object | None = None,
    ):
        """Search then describe, and hand back the tools plus the live ref."""

        dependencies = await self.dependencies(
            context,
            registry=registry,
            descriptor_revision_resolver=descriptor_revision_resolver,
        )
        tools = self.tools_by_name(await self.graph_request(context, dependencies))
        run_token = self.bound_run(context)
        try:
            found = await self.call(
                tools[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value],
                query="issues",
                limit=10,
            )
            ref = self.ref_for(found, _READ_TOOL)
            described = await self.call(
                tools[CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value],
                capability_ref=ref,
            )
        finally:
            RunControlContext.unbind(run_token)
        assert "error" not in described, described
        return tools, ref

    async def _invoke(
        self,
        context: AgentRuntimeContext,
        tools: Mapping[str, Any],
        ref: str,
    ) -> Mapping[str, Any]:
        run_token = self.bound_run(context)
        operation_token, service_token = self.bind_gateway(context)
        # ``list_issues`` is a read that is absent from the curated catalog; in
        # production the MCP loader registers its ``readOnlyHint`` at discovery.
        # Register it here so the P1b PDP derives READ (and ALLOWs the invoke)
        # rather than the fail-closed WRITE it must assume for an un-annotated,
        # un-catalogued op.
        annotations_token = McpToolAnnotationsRegistry.bind_for_run({})
        for server_id in range(10):
            McpToolAnnotationsRegistry.register(
                f"issues{server_id}",
                _READ_TOOL,
                McpToolAnnotations(read_only_hint=True),
            )
        try:
            return await self.call(
                tools[CapabilityBridgeToolName.INVOKE_CAPABILITY.value],
                capability_ref=ref,
                arguments={"team": "ENG"},
            )
        finally:
            McpToolAnnotationsRegistry.unbind(annotations_token)
            McpOperationGatewayContext.unbind(service_token)
            OperationContext.unbind(operation_token)
            RunControlContext.unbind(run_token)

    async def test_a_schema_change_after_describe_refuses_the_invoke(self) -> None:
        """The package proved this; here the whole chain is production's.

        Refusing is deterministic and does not depend on whether the arguments
        the model authored against the old schema happen to still validate —
        ``{"team": "ENG"}`` is valid under both schemas and is still refused.
        """

        context = self.context()
        provider, registry = self.one_server()
        tools, ref = await self._described(context, registry)

        provider.clients["issues0"] = self.counting_client(
            "issues0", schema=_MOVED_SCHEMA
        )
        invoked = await self._invoke(context, tools, ref)

        assert (
            self.error_code(invoked)
            == CapabilityDiscoveryErrorCode.CAPABILITY_STALE.value
        )

    async def test_a_capability_withdrawn_after_describe_refuses_the_invoke(
        self,
    ) -> None:
        """The authority stops publishing the capability between the two calls."""

        context = self.context()
        provider, registry = self.one_server()
        tools, ref = await self._described(context, registry)

        provider.clients["issues0"] = _CountingClient(tools=())
        invoked = await self._invoke(context, tools, ref)

        assert (
            self.error_code(invoked)
            == CapabilityDiscoveryErrorCode.CAPABILITY_STALE.value
        )

    async def test_authorization_revoked_after_describe_refuses_the_invoke(
        self,
    ) -> None:
        """Revocation proper: the card now demands a scope the run never held.

        The executor re-resolves through the run's own ``McpLoader``, whose
        permission check always runs uncached on the live card, so a scope
        withdrawn mid-run is refused at invoke even though the reference, the
        ledger binding, and the schema digest are all still exactly as
        disclosed.
        """

        context = self.context()
        provider, registry = self.one_server()
        tools, ref = await self._described(context, registry)

        provider.cards = [
            self.counting_card(
                "issues0", server_id="srv_issues0", scope=_WITHHELD_SCOPE
            )
        ]
        invoked = await self._invoke(context, tools, ref)

        assert (
            self.error_code(invoked)
            == CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE.value
        )

    async def test_a_server_removed_after_describe_refuses_the_invoke(self) -> None:
        """The connector is disconnected between the two model turns."""

        context = self.context()
        provider, registry = self.one_server()
        tools, ref = await self._described(context, registry)

        provider.cards = []
        invoked = await self._invoke(context, tools, ref)

        assert (
            self.error_code(invoked)
            == CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE.value
        )

    async def test_an_unchanged_capability_still_invokes(self) -> None:
        """The negative control: the four refusals above are not refusing always."""

        context = self.context()
        _provider, registry = self.one_server()
        tools, ref = await self._described(context, registry)

        invoked = await self._invoke(context, tools, ref)

        assert "error" not in invoked, invoked

    async def test_a_revision_move_alone_refuses_the_invoke(self) -> None:
        """The F8 route, end to end, with everything else deliberately still.

        The connector is untouched: the same client, the same tools, the same
        schema, the same card, the same scopes. The *only* thing that changes
        between describe and invoke is the backend's descriptor revision for the
        server, delivered the way the F8 feed delivers it. So the executor's
        live re-resolution — which carries all four refusals above — has nothing
        to object to, and the refusal can only be the Step RB revalidation
        reporting the reference bound to a generation the authority no longer
        derives.

        This is the criterion's other half, and until the composition root
        folded the descriptor revisions there was no configuration of production
        in which it could happen.
        """

        context = self.context()
        provider, registry = self.one_server()
        backend, resolver = self.revision_authority({"srv_issues0": "rev-1"})
        tools, ref = await self._described(
            context, registry, descriptor_revision_resolver=resolver
        )

        await self.move_descriptors(
            backend=backend,
            resolver=resolver,
            context=context,
            server_id="srv_issues0",
            revision="rev-2",
        )
        invoked = await self._invoke(context, tools, ref)

        assert (
            self.error_code(invoked)
            == CapabilityDiscoveryErrorCode.CAPABILITY_STALE.value
        )
        # The connector really was left alone: nothing about the descriptors the
        # model was shown changed, so this refusal is the revision binding's.
        assert provider.clients["issues0"].tools[0].input_schema == _READ_SCHEMA

    async def test_the_same_run_still_invokes_while_the_revision_holds(self) -> None:
        """The negative control for the revision route specifically.

        A wired revision source that reported a different value on every read
        would refuse every invoke and would be indistinguishable, from the
        outside, from a working staleness check.
        """

        context = self.context()
        _provider, registry = self.one_server()
        _backend, resolver = self.revision_authority({"srv_issues0": "rev-1"})
        tools, ref = await self._described(
            context, registry, descriptor_revision_resolver=resolver
        )

        invoked = await self._invoke(context, tools, ref)

        assert "error" not in invoked, invoked

    async def test_the_composition_root_folds_the_f8_descriptor_revisions(
        self,
    ) -> None:
        """BUG-12, inverted: the generation is keyed to a revision that can move.

        This case previously pinned the opposite — ``build_capability_discovery_composer``
        supplied no revision source, so the generation folded zero revisions and
        recomputed to the same value for the whole run. Every other keyed input
        is frozen per run by contract, which made the Step RB revalidation
        unable to report anything stale in production.

        The negative control is the deployment with no F8 authority wired: it
        still folds zero, which is what keeps this lane byte-identical wherever
        the control plane is off.
        """

        context = self.context()
        provider, registry = self.one_server()
        _backend, resolver = self.revision_authority({"srv_issues0": "rev-1"})

        dependencies = await self.dependencies(
            context,
            registry=registry,
            descriptor_revision_resolver=resolver,
        )
        catalog = dependencies.capability_catalog
        assert isinstance(catalog, CapabilityCatalog)
        generation = catalog.generation
        assert generation is not None
        assert generation.descriptor_revision_count == 1

        unwired = await self.dependencies(context, registry=registry)
        unwired_catalog = unwired.capability_catalog
        assert isinstance(unwired_catalog, CapabilityCatalog)
        assert unwired_catalog.generation is not None
        assert unwired_catalog.generation.descriptor_revision_count == 0

    async def test_the_live_authority_reports_a_ref_stale_when_descriptors_move(
        self,
    ) -> None:
        """The criterion's F8 half, measured on the real authority end to end.

        The generation source recomputes from freshly re-read inputs, and now
        one of those inputs can actually change: the F8 descriptor revisions,
        read through the same resolver the MCP loader consults. So a reference
        minted before a server's descriptors moved is bound to a generation the
        authority no longer derives, and the shared Step RB revalidator refuses
        it.

        Nothing about the *schema* changes here — only the backend's revision
        for the server. That isolates this route from the executor's live
        re-resolution, which the four cases above already prove: a refusal seen
        here can only have come from the revision binding.
        """

        from agent_runtime.capabilities.discovery import (  # noqa: PLC0415
            CapabilityCatalogRevisionAuthority,
            CapabilityRefRevalidation,
        )
        from agent_runtime.control_plane.revision_binding import (  # noqa: PLC0415
            RevalidationOutcome,
            RevisionAuthorityState,
            RevisionBindingRevalidator,
            RevisionBoundScope,
        )

        context = self.context()
        provider, registry = self.one_server()
        backend, resolver = self.revision_authority({"srv_issues0": "rev-1"})

        dependencies = await self.dependencies(
            context,
            registry=registry,
            descriptor_revision_resolver=resolver,
        )
        catalog = dependencies.capability_catalog
        assert isinstance(catalog, CapabilityCatalog)
        binding = catalog.bind_ref(catalog.entries[0].capability_ref)
        scope = RevisionBoundScope(
            subject_fingerprint=binding.issued_generation.subject_fingerprint,
            run_id=context.run_id,
            catalog_generation=binding.issued_generation.generation_ref,
        )

        composer = build_capability_discovery_composer(
            descriptor_revision_resolver=resolver,
        )
        assert composer is not None
        run_token = self.bound_run(context)
        try:
            composed = await composer.acompose(
                context, mcp_server_cards=tuple(provider.cards)
            )
            assert composed is not None
            before = await composed.generation_source.live_generation(scope=scope)
            # The descriptors move underneath the run: the backend now reports a
            # different revision and the feed says so.
            await self.move_descriptors(
                backend=backend,
                resolver=resolver,
                context=context,
                server_id="srv_issues0",
                revision="rev-2",
            )
            after = await composed.generation_source.live_generation(scope=scope)

            revalidation = CapabilityRefRevalidation(
                revalidator=RevisionBindingRevalidator(
                    CapabilityCatalogRevisionAuthority(composed.generation_source)
                ),
                subject_fingerprint=composed.subject_fingerprint,
            )
            assert after.generation is not None
            decision = await revalidation.decide(
                binding=binding,
                run_id=context.run_id,
                live_generation=after.generation,
            )
        finally:
            RunControlContext.unbind(run_token)

        assert before.state is RevisionAuthorityState.ACTIVE
        assert after.state is RevisionAuthorityState.ACTIVE
        # The authority genuinely re-derived a different identity, and the
        # shared revalidator turned that into a refusal.
        assert before.generation != after.generation
        assert decision.outcome is not RevalidationOutcome.CURRENT

    async def test_an_unmoved_descriptor_keeps_the_same_live_generation(self) -> None:
        """The negative control: the revision route is not refusing always.

        A source that reported a fresh identity on every read would fail every
        reference on its second use and would look exactly like a working
        staleness check. Two reads with nothing moved must agree.
        """

        from agent_runtime.control_plane.revision_binding import (  # noqa: PLC0415
            RevisionAuthorityState,
            RevisionBoundScope,
        )

        context = self.context()
        provider, registry = self.one_server()
        _backend, resolver = self.revision_authority({"srv_issues0": "rev-1"})

        dependencies = await self.dependencies(
            context,
            registry=registry,
            descriptor_revision_resolver=resolver,
        )
        catalog = dependencies.capability_catalog
        assert isinstance(catalog, CapabilityCatalog)
        binding = catalog.bind_ref(catalog.entries[0].capability_ref)
        scope = RevisionBoundScope(
            subject_fingerprint=binding.issued_generation.subject_fingerprint,
            run_id=context.run_id,
            catalog_generation=binding.issued_generation.generation_ref,
        )

        composer = build_capability_discovery_composer(
            descriptor_revision_resolver=resolver,
        )
        assert composer is not None
        run_token = self.bound_run(context)
        try:
            composed = await composer.acompose(
                context, mcp_server_cards=tuple(provider.cards)
            )
            assert composed is not None
            first = await composed.generation_source.live_generation(scope=scope)
            second = await composed.generation_source.live_generation(scope=scope)
        finally:
            RunControlContext.unbind(run_token)

        assert first.state is RevisionAuthorityState.ACTIVE
        assert first.generation == second.generation
        # And the generation the run's own catalog was stamped with is the one
        # the authority re-derives, so a ref is usable before anything moves.
        assert first.generation == binding.issued_generation

    async def test_the_revision_read_costs_no_extra_round_trip_per_question(
        self,
    ) -> None:
        """Cost, stated as a test rather than as a claim.

        The live authority is asked once per ``invoke_capability``. Each question
        re-reads the revisions, but through the resolver's own TTL cache, so the
        backend is reached when the run's catalog is composed and then not again
        until something invalidates the entry — which is precisely when a
        revision moved. Ten questions must not be ten fetches.
        """

        from agent_runtime.control_plane.revision_binding import (  # noqa: PLC0415
            RevisionBoundScope,
        )

        context = self.context()
        provider, registry = self.one_server()
        backend, resolver = self.revision_authority({"srv_issues0": "rev-1"})

        composer = build_capability_discovery_composer(
            descriptor_revision_resolver=resolver,
        )
        assert composer is not None
        run_token = self.bound_run(context)
        try:
            composed = await composer.acompose(
                context, mcp_server_cards=tuple(provider.cards)
            )
            assert composed is not None
            assert composed.catalog.generation is not None
            scope = RevisionBoundScope(
                subject_fingerprint=composed.catalog.generation.subject_fingerprint,
                run_id=context.run_id,
                catalog_generation=composed.catalog.generation.generation_ref,
            )
            after_compose = backend.exact_calls
            for _ in range(10):
                await composed.generation_source.live_generation(scope=scope)
        finally:
            RunControlContext.unbind(run_token)

        assert after_compose == 1
        assert backend.exact_calls == after_compose


@pytest.mark.usefixtures("deferred_environment")
class TestUnauthorizedNamesAreUnreachable(Step8Harness):
    """ "unauthorized capability names cannot be searched, described, guessed,
    or invoked" — against a registry that really holds one."""

    def mixed_registry(self) -> tuple[_MutableProvider, DynamicMcpRegistry]:
        """One authorized server and one the run holds no scope for."""

        provider = _MutableProvider(
            cards=[
                self.counting_card("issues0", server_id="srv_issues0"),
                self.counting_card(
                    _UNAUTHORIZED_SERVER,
                    server_id=_UNAUTHORIZED_SERVER_ID,
                    scope=_WITHHELD_SCOPE,
                ),
            ],
            clients={
                "issues0": self.counting_client("issues0"),
                _UNAUTHORIZED_SERVER: self.counting_client(_UNAUTHORIZED_SERVER),
            },
        )
        return provider, DynamicMcpRegistry(providers=(provider,))

    async def test_the_unauthorized_server_is_never_indexed(self) -> None:
        context = self.context()
        _provider, registry = self.mixed_registry()
        dependencies = await self.dependencies(context, registry=registry)
        catalog = dependencies.capability_catalog

        assert isinstance(catalog, CapabilityCatalog)
        assert [entry.stable_name for entry in catalog.entries] == ["issues0"]

    async def test_it_cannot_be_searched_by_its_own_name(self) -> None:
        """Searching the withheld name returns nothing — and search still works.

        The second half is the control: a search that answered nothing for
        *every* query would satisfy the first assertion while telling us
        nothing, so the same composed tool is asked for the authorized server
        and must find it.
        """

        context = self.context()
        _provider, registry = self.mixed_registry()
        dependencies = await self.dependencies(context, registry=registry)
        tools = self.tools_by_name(await self.graph_request(context, dependencies))
        search = tools[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value]

        run_token = self.bound_run(context)
        try:
            found = await self.call(search, query=_UNAUTHORIZED_SERVER, limit=10)
            authorized = await self.call(search, query="issues0 team", limit=10)
        finally:
            RunControlContext.unbind(run_token)

        assert "error" not in found, found
        names = {
            candidate["stable_name"] for candidate in found["search"]["candidates"]
        }
        assert _UNAUTHORIZED_SERVER not in names, names
        assert "issues0" in {
            candidate["stable_name"] for candidate in authorized["search"]["candidates"]
        }

    async def test_a_correctly_minted_guess_is_refused_by_both_tools(self) -> None:
        """The discriminating case: the reference is *right*, the answer is no.

        The guess is minted with this run's own reference key, for the exact
        identity ``AuthorizedCatalogBuilder`` would have used had the server been
        authorized, against the run's real catalog id. So nothing here is
        protected by the opacity of the string — an attacker who knew the key
        would produce exactly this. Both tools must still refuse, and with the
        *same* code an unknown reference gets, so the error cannot be read as
        confirmation that the capability exists.
        """

        context = self.context()
        _provider, registry = self.mixed_registry()
        dependencies = await self.dependencies(context, registry=registry)
        catalog = dependencies.capability_catalog
        assert isinstance(catalog, CapabilityCatalog)
        tools = self.tools_by_name(await self.graph_request(context, dependencies))
        minter = HmacCapabilityReferenceMinter(reference_key=self.run_key(context))
        guessed = minter.mint(
            catalog_id=catalog.revision.catalog_id,
            identity=(f"mcp_server:{_UNAUTHORIZED_SERVER_ID}:{_UNAUTHORIZED_SERVER}"),
        )
        # The guess is a real reference, not a malformed string: it is exactly
        # what the builder mints for an authorized member.
        assert guessed != catalog.entries[0].capability_ref
        assert len(guessed) == len(catalog.entries[0].capability_ref)

        run_token = self.bound_run(context)
        operation_token, service_token = self.bind_gateway(context)
        try:
            described = await self.call(
                tools[CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value],
                capability_ref=guessed,
            )
            invoked = await self.call(
                tools[CapabilityBridgeToolName.INVOKE_CAPABILITY.value],
                capability_ref=guessed,
                arguments={"team": "ENG"},
            )
            unknown = await self.call(
                tools[CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value],
                capability_ref=minter.mint(
                    catalog_id=catalog.revision.catalog_id,
                    identity="mcp_server:srv_nothing:nothing",
                ),
            )
        finally:
            McpOperationGatewayContext.unbind(service_token)
            OperationContext.unbind(operation_token)
            RunControlContext.unbind(run_token)

        not_found = CapabilityDiscoveryErrorCode.CAPABILITY_NOT_FOUND.value
        assert self.error_code(described) == not_found
        assert self.error_code(invoked) == not_found
        # Not an existence oracle: a capability that never existed anywhere gets
        # the identical answer.
        assert self.error_code(unknown) == not_found

    async def test_the_unauthorized_server_is_never_opened(self) -> None:
        """Refusal is not merely in the answer — no descriptor is ever read.

        The query is one both cards' text matches ("issues ... for a team"
        appears in each short description), so a second tier that ranked over
        everything the *registry* holds would open both. The authorized server
        is opened and the withheld one is not, which is only true because the
        withheld card never entered the catalog tier two ranks over.

        The first assertion is the criterion; the second is what stops it from
        passing on a search that expanded nothing at all — an earlier draft of
        this case queried the withheld name, matched no catalog member, and
        proved nothing.
        """

        context = self.context()
        provider, registry = self.mixed_registry()
        dependencies = await self.dependencies(context, registry=registry)
        tools = self.tools_by_name(await self.graph_request(context, dependencies))

        run_token = self.bound_run(context)
        try:
            await self.call(
                tools[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value],
                query="issues for a team",
                limit=10,
            )
        finally:
            RunControlContext.unbind(run_token)

        assert provider.clients[_UNAUTHORIZED_SERVER].listings == 0
        assert provider.clients["issues0"].listings > 0, "tier two never expanded"


class TestDirectAndServerFallbackRemainAvailable(Step8Harness):
    """ "direct/server fallback remains available" — as a posture switch.

    Not the unconfigured deployment (W2 covers that) but a deployment that has
    configured F3, holds a reference secret, carries an ``enforce`` F3 mode, and
    dials the posture back — which is exactly what §19's ``F3: deferred →
    server/direct`` kill switch produces, and what the ``f3_direct_fallback``
    promotion cohort declares.
    """

    @pytest.fixture(autouse=True)
    def _secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CapabilityDiscoveryEnvironment.REFERENCE_SECRET, _SECRET)

    @pytest.mark.parametrize("posture", ["direct", "server"])
    async def test_a_dialled_back_posture_restores_the_pre_f3_surface(
        self,
        monkeypatch: pytest.MonkeyPatch,
        posture: str,
    ) -> None:
        monkeypatch.setenv(CapabilityDiscoveryEnvironment.ACTIVATION, posture)
        context = self.context()
        _provider, registry = self.one_server()
        dependencies = await self.dependencies(context, registry=registry)
        request = await self.graph_request(context, dependencies)
        names = self.tools_by_name(request)

        assert dependencies.capability_catalog is None
        assert dependencies.capability_bridge is None
        assert self.bridge_names(request) == set()
        for tool_name in (
            McpValues.ToolName.LOAD_MCP_SERVER,
            McpValues.ToolName.CALL_MCP_TOOL,
        ):
            assert tool_name in names, (posture, sorted(names))

    @pytest.mark.parametrize("posture", ["direct", "server"])
    async def test_the_dialled_back_posture_keeps_the_mcp_card_block(
        self,
        monkeypatch: pytest.MonkeyPatch,
        posture: str,
    ) -> None:
        """Fallback is not only the tools — the model keeps its enumeration.

        F3.9 suppresses the per-server card block whenever a bridge tool
        registers. A fallback posture that kept the suppression would leave the
        model with the direct tools and no idea which servers exist, which is a
        worse state than either posture on its own.
        """

        monkeypatch.setenv(CapabilityDiscoveryEnvironment.ACTIVATION, posture)
        context = self.context()
        _provider, registry = self.one_server()
        dependencies = await self.dependencies(context, registry=registry)
        request = await self.graph_request(context, dependencies)

        assert "issues0" in str(request.system_prompt)  # type: ignore[attr-defined]

    async def test_the_deferred_posture_is_the_one_that_suppresses(self) -> None:
        """The negative control for the two cases above."""

        context = self.context()
        _provider, registry = self.one_server()
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv(CapabilityDiscoveryEnvironment.ACTIVATION, "deferred")
            dependencies = await self.dependencies(context, registry=registry)
            request = await self.graph_request(context, dependencies)

        assert self.bridge_names(request) == CapabilityBridgeToolName.reserved_names()
        assert "issues0" not in str(request.system_prompt)  # type: ignore[attr-defined]

    async def test_a_shadow_run_keeps_the_pre_f3_surface_too(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The rung below ``deferred`` on §19's ladder, asserted at the surface."""

        monkeypatch.setenv(CapabilityDiscoveryEnvironment.ACTIVATION, "deferred")
        context = self.context()
        _provider, registry = self.one_server()
        binding = self.binding(run_id=context.run_id, f3=FeatureMode.SHADOW)
        dependencies = await self.dependencies(
            context, registry=registry, binding=binding
        )
        request = await self.graph_request(context, dependencies, binding=binding)
        names = self.tools_by_name(request)

        assert self.bridge_names(request) == set()
        assert McpValues.ToolName.CALL_MCP_TOOL in names
        assert "issues0" in str(request.system_prompt)  # type: ignore[attr-defined]
