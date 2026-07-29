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
from agent_runtime.capabilities.mcp import DynamicMcpRegistry
from agent_runtime.capabilities.mcp.constants import Values as McpValues
from agent_runtime.capabilities.mcp.discovery_cache import McpDiscoveryCache
from agent_runtime.capabilities.mcp.gateway_context import McpOperationGatewayContext
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


class Step8Harness(BridgeWiringHarness):
    """The W2 composition root, plus the fixtures each criterion needs."""

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

    def test_the_expansion_limit_environment_knobs_do_not_reach_the_seam(
        self,
    ) -> None:
        """An honest canary over a gap this gate found and did not fix.

        ``CapabilityExpansionLimits.from_environment`` reads three documented
        operator knobs, and **no production call site invokes it**: the worker
        composition root leaves ``CapabilityBridgeComposition.expansion_limits``
        unset, ``factory.py`` forwards that ``None``, and
        ``BoundedCapabilityExpander`` falls back to the hard defaults. So the
        effective ``K`` is always 3 no matter what an operator configures.

        That is fail-safe rather than fail-open — the default is the
        conservative value, and a typo could never raise fan-out — but it means
        "at most **configured** K" is currently "at most **default** K". Pinned
        rather than left to be rediscovered: threading the knobs makes this fail,
        which is the moment to prove a configured bound end to end.
        """

        assert CapabilityBridgeComposition().expansion_limits is None
        from pathlib import Path  # noqa: PLC0415

        source_root = Path(__file__).resolve().parents[3] / "src"
        callers = [
            path
            for path in source_root.rglob("*.py")
            if "CapabilityExpansionLimits.from_environment(" in path.read_text("utf-8")
        ]
        assert callers == [], [str(path) for path in callers]


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

    async def _described(self, context: AgentRuntimeContext, registry: object):  # type: ignore[no-untyped-def]
        """Search then describe, and hand back the tools plus the live ref."""

        dependencies = await self.dependencies(context, registry=registry)
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
        try:
            return await self.call(
                tools[CapabilityBridgeToolName.INVOKE_CAPABILITY.value],
                capability_ref=ref,
                arguments={"team": "ENG"},
            )
        finally:
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

    async def test_the_composition_root_wires_no_f8_descriptor_revision_source(
        self,
    ) -> None:
        """An honest canary over the *other* half of this criterion.

        Everything above fails closed through the executor's live re-resolution.
        The F8 route — a moved descriptor revision changing the catalog
        generation so the shared Step RB revalidator refuses the reference — is
        **not wired**: ``build_capability_discovery_composer`` supplies no
        ``CatalogDescriptorRevisionSourcePort``, so the generation folds zero
        revisions and recomputes to the same value for the whole run. Every
        other keyed input is frozen per run by contract, so in production the
        revalidator cannot currently report a reference stale mid-run at all.

        That is why BUG-04 (expanded capabilities carry no descriptor revision)
        and ARQ-007's remaining "add revision invalidation with F8" are still
        open. Recorded as a canary because it is a live property of the wiring,
        not a hypothetical: threading a revision source makes the second
        assertion here fail, which is the moment to prove that route.
        """

        composer = build_capability_discovery_composer()
        assert composer is not None
        assert composer._descriptor_revision_source is None

        context = self.context()
        provider, registry = self.one_server()
        dependencies = await self.dependencies(context, registry=registry)
        catalog = dependencies.capability_catalog
        assert isinstance(catalog, CapabilityCatalog)
        generation = catalog.generation
        assert generation is not None
        assert generation.descriptor_revision_count == 0

    async def test_the_live_authority_cannot_report_a_ref_stale_mid_run(self) -> None:
        """The consequence of the canary above, measured on the real authority.

        The generation source recomputes from freshly re-read inputs, which is
        exactly right — but with no descriptor-revision source wired, and with
        the subject, connector scope, and task-policy selection all frozen for
        the run by contract, there is nothing left that can move. The authority
        therefore re-derives the *same* generation the catalog was projected
        under, before and after the descriptors underneath it change.

        So the Step RB revalidation, which lane F3.2 correctly built and lane
        F3.5 correctly wired, currently contributes nothing to this criterion in
        production. The safety property is carried by the executor's live
        re-resolution alone — which the four cases above prove — and this pins
        why that matters rather than leaving it to be inferred.
        """

        from agent_runtime.control_plane.revision_binding import (  # noqa: PLC0415
            RevisionAuthorityState,
            RevisionBoundScope,
        )

        context = self.context()
        provider, registry = self.one_server()
        dependencies = await self.dependencies(context, registry=registry)
        catalog = dependencies.capability_catalog
        assert isinstance(catalog, CapabilityCatalog)
        binding = catalog.bind_ref(catalog.entries[0].capability_ref)
        scope = RevisionBoundScope(
            subject_fingerprint=binding.issued_generation.subject_fingerprint,
            run_id=context.run_id,
            catalog_generation=binding.issued_generation.generation_ref,
        )

        composer = build_capability_discovery_composer()
        assert composer is not None
        run_token = self.bound_run(context)
        try:
            composed = composer.compose(context, mcp_server_cards=tuple(provider.cards))
            assert composed is not None
            before = await composed.generation_source.live_generation(scope=scope)
            # The descriptors move underneath the run, exactly as they do in the
            # schema-change case above.
            provider.clients["issues0"] = self.counting_client(
                "issues0", schema=_MOVED_SCHEMA
            )
            after = await composed.generation_source.live_generation(scope=scope)
        finally:
            RunControlContext.unbind(run_token)

        assert before.state is RevisionAuthorityState.ACTIVE
        assert after.state is RevisionAuthorityState.ACTIVE
        assert before.generation == after.generation


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
