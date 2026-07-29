"""W2 — the composition root actually delivers the F3 bridge it was built for.

Five lanes each built one piece of the discovery bridge and each proved its own
piece against its own fixtures.  BUG-08's ``test_bridge_chain`` proves the chain
*within* the discovery package, by composing the seam, the executor, and the
revalidation by hand.  That is exactly what production could not do: the
registrar was reached with three of its seams unset, so ``invoke_capability``
never registered, an over-bound schema always reported ``unavailable``, and the
composer had no card snapshot to project a catalog from.  Every unit passed and
the feature did nothing.

So nothing here builds a bridge.  Every case starts from
:class:`DefaultRuntimeDependenciesFactory` and
:func:`compose_capability_discovery` — the two objects the worker itself uses —
and reads the result off the graph request the agent builder was handed.  The
only substitution is the MCP registry, which is the standard connector seam
every MCP test uses and is *not* part of the wiring under test: the cards, the
catalog, the reference key, the ledger, the executor, and the publisher are all
produced by production code.

The three properties this module exists to hold:

* **the wiring delivers the chain** — one reference survives search → describe →
  invoke and reaches the connector, with nothing composed by the test;
* **the two BUG-08 invariants hold across the seam** — the bridge's minter is
  keyed identically to the catalog builder's, and the ledger the registrar
  writes into is the same object the executor reads from; and
* **the dark path is untouched** — an unconfigured deployment composes the same
  tool surface, does not even list MCP cards, and never imports the discovery
  package.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from typing import Any

import pytest

from agent_runtime.capabilities.actions.policy import ConnectorWritePolicyOverrides
from agent_runtime.capabilities.discovery import (
    CapabilityBridgeToolName,
    CapabilityCatalog,
    CapabilityInvocationStatus,
    CapabilitySchemaBounds,
    ExpandedCapabilityProjector,
    HmacCapabilityReferenceMinter,
    RunScopedSchemaArtifactPublisher,
)
from agent_runtime.capabilities.mcp import DynamicMcpRegistry
from agent_runtime.capabilities.mcp.constants import Values as McpValues
from agent_runtime.capabilities.mcp.gateway_context import (
    McpOperationGatewayContext,
    McpOperationGatewayServices,
)
from agent_runtime.capabilities.operations.classifier import OperationClassifier
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    VerifiedOperationIdentity,
)
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.control_plane.context import RunControlBinding, RunControlContext
from agent_runtime.control_plane.contracts import RunControlSnapshot
from agent_runtime.control_plane.feature_modes import FeatureMode, FeatureModeSet
from agent_runtime.effects.contracts import EffectActorIdentity, EffectStageScope
from agent_runtime.effects.staging import EffectStager
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    CapabilityBridgeComposition,
    ModelConfig,
    RuntimeDependencies,
)
from agent_runtime.execution.factory import acreate_agent_runtime
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.ledger_models import EffectActor
from runtime_worker.capability_discovery_composition import (
    CapabilityDiscoveryEnvironment,
    build_capability_discovery_composer,
)
from runtime_worker.dependencies import (
    DefaultRuntimeDependenciesFactory,
    compose_capability_discovery,
)
from runtime_worker.run_control import RunControlAssignment

from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder
from tests.unit.agent_runtime.capabilities.discovery.test_bridge_chain import (
    _ArgumentStore,
    _LedgerEvents,
    _RecordingClient,
    _ResultStore,
)
from tests.unit.agent_runtime.effects.fakes import (
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
)
from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin

_SERVER = "linear"
_READ_TOOL = "list_issues"
_QUERY = "linear issues"
_SERVER_ID = "srv_linear"
#: How ``AuthorizedCatalogBuilder`` names an MCP server card before minting. It
#: is restated here rather than read off the entry so this asserts the *keyed
#: derivation*, not that two pieces of production code agree with each other.
_CATALOG_IDENTITY = f"mcp_server:{_SERVER_ID}:{_SERVER}"
# Long enough to clear ``MIN_SECRET_BYTES``; deliberately not hex, so the
# non-hex branch of the key derivation is the one under test.
_SECRET = "w2-seam-threading-deployment-secret-value"

_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"team": {"type": "string"}, "limit": {"type": "integer"}},
    "required": ["team"],
    "additionalProperties": False,
}


class _RecordingDecisionStore:
    """A ``RunControlDecisionStorePort`` that only remembers what it was told."""

    def __init__(self) -> None:
        self.writes: list[object] = []

    async def append(self, write: object) -> object:
        self.writes.append(write)
        return write

    async def list_for_run(self, **_kwargs: object) -> tuple[object, ...]:
        return tuple(self.writes)


class _RecordingWriter:
    """An ``OffloadWriter``: content in, content-addressed locator out."""

    def __init__(self) -> None:
        self.documents: list[str] = []

    def __call__(self, content: str) -> str:
        self.documents.append(content)
        return f"/large_tool_results/{len(self.documents)}"


class BridgeWiringHarness(DynamicMcpLoadingMixin):
    """Compose one run exactly as the worker does, and hand back what it built."""

    # ------------------------------------------------------------- run inputs

    @staticmethod
    def context(run_id: str = "run_w2") -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id="user_w2",
            org_id="org_w2",
            roles={"employee"},
            permission_scopes={"docs:read", "docs:write"},
            connector_scopes={"drive": frozenset({"docs:read"})},
            model_profile=ModelConfig(
                provider="openai",
                model_name="gpt-test",
                max_input_tokens=32_000,
                timeout_seconds=30,
                temperature=0,
            ),
            run_id=run_id,
            trace_id="trace_w2",
        )

    @staticmethod
    def binding(
        *,
        run_id: str = "run_w2",
        f3: FeatureMode = FeatureMode.ENFORCE,
    ) -> RunControlBinding:
        assignment = RunControlAssignment.safe_active_v1()
        snapshot = RunControlSnapshot.create(
            run_id=run_id,
            conversation_id="conv_w2",
            subject_fingerprint="b" * 64,
            deployment_profile="single_user_desktop",
            harness_variant_ref=assignment.harness_variant_ref,
            task_policy_selection_ref=assignment.task_policy_selection_ref,
            policy_revisions=assignment.policy_revisions,
            feature_modes=FeatureModeSet(f3=f3),
            budget_envelope_ref=assignment.budget_envelope_ref,
            assignment_revision=assignment.assignment_revision,
        )
        return RunControlBinding(
            snapshot=snapshot,
            effective_modes=FeatureModeSet(f3=f3),
            decisions=(),
        )

    # ----------------------------------------------------------------- the MCP

    def card(self):  # type: ignore[no-untyped-def]
        return self.make_card(
            name=_SERVER,
            short_description="Track and read Linear issues for a team.",
            required_scopes=("docs:read",),
        ).model_copy(update={"server_id": _SERVER_ID})

    def registry(
        self,
        *,
        schema: Mapping[str, Any] | None = None,
    ) -> tuple[_RecordingClient, DynamicMcpRegistry]:
        """The one seam this module substitutes: a connector that answers.

        Everything downstream — the card snapshot, the catalog, the refs, the
        dispatch — is production code reading this registry, so substituting it
        is substituting the connector, never the wiring.
        """

        client = _RecordingClient(
            tools=(
                self.make_tool(
                    name=_READ_TOOL,
                    description="List the issues assigned to one Linear team.",
                    input_schema=dict(schema or _READ_SCHEMA),
                ),
            ),
            outputs={_READ_TOOL: {"items": [{"id": "L-1"}]}},
        )
        provider = self.FakeMcpProvider(cards=(self.card(),), clients={_SERVER: client})
        return client, DynamicMcpRegistry(providers=(provider,))

    # --------------------------------------------------------- the composition

    @staticmethod
    def factory(
        *,
        decision_store: object | None = None,
        schema_artifact_writer: object | None = None,
        descriptor_revision_resolver: object | None = None,
    ) -> DefaultRuntimeDependenciesFactory:
        """The worker's own factory, wired through its own composer builder.

        ``descriptor_revision_resolver`` is the worker process's F8 revision
        authority, which ``__main__`` supplies from the one MCP control-plane
        assembly. Defaulting it to ``None`` keeps every case that does not care
        about revisions on the deployment shape it always had.
        """

        return DefaultRuntimeDependenciesFactory(
            RuntimeSettings.load(environ={}),
            capability_discovery=build_capability_discovery_composer(
                decision_store=decision_store,
                schema_artifact_writer=schema_artifact_writer,
                descriptor_revision_resolver=descriptor_revision_resolver,
            ),
        )

    async def dependencies(
        self,
        context: AgentRuntimeContext,
        *,
        registry: object,
        decision_store: object | None = None,
        schema_artifact_writer: object | None = None,
        descriptor_revision_resolver: object | None = None,
        binding: RunControlBinding | None = None,
    ) -> RuntimeDependencies:
        """Build one run's dependencies through the production composition root."""

        factory = self.factory(
            decision_store=decision_store,
            schema_artifact_writer=schema_artifact_writer,
            descriptor_revision_resolver=descriptor_revision_resolver,
        )
        token = RunControlContext.bind_for_run(
            binding or self.binding(run_id=context.run_id)
        )
        try:
            base = factory(context).model_copy(update={"mcp_registry": registry})
            return await compose_capability_discovery(factory, base, context)
        finally:
            RunControlContext.unbind(token)

    async def graph_request(
        self,
        context: AgentRuntimeContext,
        dependencies: RuntimeDependencies,
        *,
        binding: RunControlBinding | None = None,
    ) -> object:
        """Assemble the runtime and return the request the builder was handed."""

        builder = CapturingAgentBuilder()
        token = RunControlContext.bind_for_run(
            binding or self.binding(run_id=context.run_id)
        )
        try:
            await acreate_agent_runtime(
                context=context,
                dependencies=dependencies,
                agent_builder=builder,
            )
        finally:
            RunControlContext.unbind(token)
        return builder.calls[0]

    # -------------------------------------------------------------- inspection

    @staticmethod
    def tools_by_name(request: object) -> dict[str, Any]:
        return {str(getattr(tool, "name", "")): tool for tool in request.tools}  # type: ignore[attr-defined]

    @staticmethod
    async def call(tool: Any, **arguments: Any) -> Mapping[str, Any]:
        """Invoke one composed model tool through its own dispatch coroutine.

        Deliberately the ``coroutine`` rather than ``ainvoke``: the display
        wrapper injects a ``tool_call_id`` the graph supplies from a real
        ``ToolCall`` envelope, and building one here would assert LangChain's
        envelope rather than this lane's wiring. Everything the wiring owns —
        display stripping, the shadow probe, the bridge adapter — is still on
        this path.
        """

        return await tool.coroutine(**arguments)

    @staticmethod
    def bridge_names(request: object) -> set[str]:
        names = {str(getattr(tool, "name", "")) for tool in request.tools}  # type: ignore[attr-defined]
        return names & CapabilityBridgeToolName.reserved_names()

    @staticmethod
    def ref_for(answer: Mapping[str, Any], stable_name: str) -> str:
        assert "error" not in answer, answer
        matches = [
            candidate["capability_ref"]
            for candidate in answer["search"]["candidates"]
            if candidate["stable_name"] == stable_name
        ]
        assert matches, f"{stable_name} is absent from {answer['search']['candidates']}"
        return matches[0]

    def bound_run(self, context: AgentRuntimeContext, binding: object | None = None):  # type: ignore[no-untyped-def]
        """Bind the run-control snapshot for the window a tool is driven in.

        Not test scaffolding: the catalog-generation authority answers by
        *recomputing* the identity from the bound run's own trusted inputs, so a
        reference used outside the binding is correctly reported stale. The
        worker holds this binding for the whole run, and every case that invokes
        a bridge tool has to hold it too or it is measuring the wrong thing.
        """

        return RunControlContext.bind_for_run(
            binding or self.binding(run_id=context.run_id)
        )

    @staticmethod
    def bind_gateway(context: AgentRuntimeContext):  # type: ignore[no-untyped-def]
        """Bind the ordinary Operation Gateway invoke must re-enter."""

        operation_token = OperationContext.bind_for_run(
            identity=VerifiedOperationIdentity(
                org_id=context.org_id,
                user_id=context.user_id,
                conversation_id="conv_w2",
                run_id=context.run_id,
            ),
            policy_snapshot=ToolUsePolicySnapshot.from_response(
                workspace=None,
                user=None,
            ),
            ledger_emitter=_LedgerEvents(),
            artifact_service=None,
            mode=OperationGatewayMode.ENFORCE,
            canonical_arguments_durable=True,
        )
        descriptors = OperationDescriptorRegistry()
        classifier = OperationClassifier(descriptors=descriptors)
        service_token = McpOperationGatewayContext.bind_for_run(
            McpOperationGatewayServices(
                gateway=OperationGateway(
                    descriptors=descriptors,
                    classifier=classifier,
                ),
                descriptors=descriptors,
                classifier=classifier,
                stager=EffectStager(
                    ledger=FakeLedger(),
                    outbox=FakeOutbox(),
                    clock=FakeClock(),
                    stage_ids=FakeStageIds(),
                ),
                stage_scope=EffectStageScope(
                    run_id=context.run_id,
                    owner_ref=f"principal://users/{context.user_id}",
                ),
                stage_author=EffectActorIdentity(
                    actor=EffectActor.USER,
                    principal_ref=f"principal://users/{context.user_id}",
                ),
                result_store=_ResultStore(),
                argument_store=_ArgumentStore(),
                connector_overrides=ConnectorWritePolicyOverrides(),
            )
        )
        return operation_token, service_token


@pytest.fixture
def deferred_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exactly what an operator must set for a working ``deferred`` deployment."""

    monkeypatch.setenv(CapabilityDiscoveryEnvironment.ACTIVATION, "deferred")
    monkeypatch.setenv(CapabilityDiscoveryEnvironment.REFERENCE_SECRET, _SECRET)
    monkeypatch.delenv(
        CapabilityDiscoveryEnvironment.CATALOG_TTL_SECONDS, raising=False
    )


@pytest.fixture
def dark_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment that configured nothing, pinned against the dev's own shell."""

    for key in (
        CapabilityDiscoveryEnvironment.ACTIVATION,
        CapabilityDiscoveryEnvironment.CATALOG_TTL_SECONDS,
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.usefixtures("deferred_environment")
class TestTheWiringDeliversTheChain(BridgeWiringHarness):
    """The lane's keystone: composed by production, driven end to end."""

    async def test_search_describe_and_invoke_work_through_the_composition_root(
        self,
    ) -> None:
        """One reference survives all three tools, with nothing composed here.

        This is the proof BUG-08's own chain test structurally could not give.
        There the seam, the executor, and the revalidation were built by the
        test; here they exist only if ``factory.py`` and the worker composition
        root actually produced them. Every step reuses the reference the
        previous step returned, so losing expansion, losing the shared ledger,
        or losing the executor each fails here rather than passing three
        isolated unit tests.
        """

        context = self.context()
        client, registry = self.registry()
        dependencies = await self.dependencies(context, registry=registry)
        request = await self.graph_request(context, dependencies)
        tools = self.tools_by_name(request)

        assert self.bridge_names(request) == CapabilityBridgeToolName.reserved_names()

        run_token = self.bound_run(context)
        operation_token, service_token = self.bind_gateway(context)
        try:
            found = await self.call(
                tools[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value],
                query=_QUERY,
                limit=10,
            )
            capability_ref = self.ref_for(found, _READ_TOOL)
            described = await self.call(
                tools[CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value],
                capability_ref=capability_ref,
            )
            invoked = await self.call(
                tools[CapabilityBridgeToolName.INVOKE_CAPABILITY.value],
                capability_ref=capability_ref,
                arguments={"team": "ENG"},
            )
        finally:
            McpOperationGatewayContext.unbind(service_token)
            OperationContext.unbind(operation_token)
            RunControlContext.unbind(run_token)

        assert "error" not in described, described
        capability = described["description"]["capability"]
        assert capability["capability_ref"] == capability_ref
        assert capability["stable_name"] == _READ_TOOL

        assert "error" not in invoked, invoked
        receipt = invoked["invocation"]["receipt"]
        assert receipt["capability_ref"] == capability_ref
        assert receipt["status"] == CapabilityInvocationStatus.COMPLETED.value
        # The dispatch reached the connector through the run's own CallMcpTool.
        assert client.calls == [(_READ_TOOL, {"team": "ENG"})]

    async def test_the_catalog_is_projected_from_the_runs_authorized_cards(
        self,
    ) -> None:
        """The sync-factory / async-registry mismatch, resolved without a fake.

        Nothing hands the composer a card snapshot: it is awaited off the run's
        own registry by ``compose_capability_discovery``. If that await were
        dropped the composer would see no cards, refuse to build a catalog, and
        the bridge would be absent — which is precisely the state this lane
        found production in.
        """

        context = self.context()
        _client, registry = self.registry()
        dependencies = await self.dependencies(context, registry=registry)
        catalog = dependencies.capability_catalog

        assert isinstance(catalog, CapabilityCatalog)
        assert [entry.stable_name for entry in catalog.entries] == [_SERVER]
        assert catalog.generation is not None

    async def test_a_run_with_no_authorized_cards_stays_on_the_pre_f3_path(
        self,
    ) -> None:
        """An empty snapshot is not a bridge over nothing; it is no bridge.

        Registering a bridge would suppress the MCP card block (F3.9) and leave
        the model no route to MCP at all, so an empty projection has to narrow
        exactly like an unbuildable catalog.
        """

        context = self.context()
        provider = self.FakeMcpProvider(cards=(), clients={})
        dependencies = await self.dependencies(
            context,
            registry=DynamicMcpRegistry(providers=(provider,)),
        )
        request = await self.graph_request(context, dependencies)

        assert dependencies.capability_catalog is None
        assert dependencies.capability_bridge is None
        assert self.bridge_names(request) == set()
        assert McpValues.ToolName.CALL_MCP_TOOL in self.tools_by_name(request)


@pytest.mark.usefixtures("deferred_environment")
class TestTheTwoBug08Invariants(BridgeWiringHarness):
    """Same key, same ledger — asserted across the seam, not inside it."""

    async def test_the_bridge_minter_is_keyed_as_the_catalog_builder_was(
        self,
    ) -> None:
        """A different key would mint refs the run's own catalog cannot explain.

        Asserted by *re-deriving* a catalog member's reference with the bridge's
        own minter. Only a minter holding the identical key reproduces the
        identical opaque token, so this fails for any second key — including one
        derived from the same secret under a different purpose string.
        """

        context = self.context()
        _client, registry = self.registry()
        dependencies = await self.dependencies(context, registry=registry)
        catalog = dependencies.capability_catalog
        bridge = dependencies.capability_bridge

        assert isinstance(catalog, CapabilityCatalog)
        assert isinstance(bridge, CapabilityBridgeComposition)
        assert isinstance(bridge.minter, HmacCapabilityReferenceMinter)
        entry = catalog.entries[0]
        assert (
            bridge.minter.mint(
                catalog_id=catalog.revision.catalog_id,
                identity=_CATALOG_IDENTITY,
            )
            == entry.capability_ref
        )

    async def test_a_foreign_key_cannot_produce_the_catalogs_references(
        self,
    ) -> None:
        """The negative control: the assertion above is not vacuously true."""

        context = self.context()
        _client, registry = self.registry()
        dependencies = await self.dependencies(context, registry=registry)
        catalog = dependencies.capability_catalog
        assert isinstance(catalog, CapabilityCatalog)
        entry = catalog.entries[0]
        foreign = HmacCapabilityReferenceMinter(
            reference_key=b"w2-a-different-reference-key-32b!"
        )

        assert (
            foreign.mint(
                catalog_id=catalog.revision.catalog_id,
                identity=_CATALOG_IDENTITY,
            )
            != entry.capability_ref
        )

    async def test_the_executor_reads_the_ledger_the_search_tool_writes(
        self,
    ) -> None:
        """One disclosure ledger, or invoke can never resolve what search found.

        Invoking a reference *before* any search is the discriminating case: the
        ledger is empty, so there is no dispatch binding and the executor must
        refuse. Invoking the same reference after a search must then succeed —
        which it can only do if both tools were handed the same ledger object.
        """

        context = self.context()
        _client, registry = self.registry()
        dependencies = await self.dependencies(context, registry=registry)
        tools = self.tools_by_name(await self.graph_request(context, dependencies))
        search = tools[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value]
        invoke = tools[CapabilityBridgeToolName.INVOKE_CAPABILITY.value]

        run_token = self.bound_run(context)
        operation_token, service_token = self.bind_gateway(context)
        try:
            found = await self.call(search, query=_QUERY, limit=10)
            capability_ref = self.ref_for(found, _READ_TOOL)
            # A ledger that the search tool did not write into cannot bind this.
            unknown = await self.call(
                invoke,
                capability_ref=f"{capability_ref[:-1]}"
                + ("0" if capability_ref[-1] != "0" else "1"),
                arguments={"team": "ENG"},
            )
            known = await self.call(
                invoke, capability_ref=capability_ref, arguments={"team": "ENG"}
            )
        finally:
            McpOperationGatewayContext.unbind(service_token)
            OperationContext.unbind(operation_token)
            RunControlContext.unbind(run_token)

        assert "error" in unknown, unknown
        assert "error" not in known, known


@pytest.mark.usefixtures("deferred_environment")
class TestTheOptionalSeamsAreThreaded(BridgeWiringHarness):
    """Telemetry and protected schemas: added answers, never changed ones."""

    async def test_the_composition_root_threads_a_keyed_schema_publisher(
        self,
    ) -> None:
        """F3.4's publisher reaches describe, keyed as the catalog builder was.

        The reference an artifact is published under folds in the *binding*
        digest, so it is a function of the run, subject, and catalog generation
        rather than of the capability. Re-deriving it here from the run's own
        reference key — recomputed through the same production helper the
        composition used — is what proves the publisher shares the catalog's key
        rather than merely holding some key of its own.
        """

        context = self.context()
        writer = _RecordingWriter()
        _client, registry = self.registry()
        dependencies = await self.dependencies(
            context,
            registry=registry,
            schema_artifact_writer=writer,
        )
        catalog = dependencies.capability_catalog
        bridge = dependencies.capability_bridge
        assert isinstance(catalog, CapabilityCatalog)
        assert isinstance(bridge, CapabilityBridgeComposition)
        publisher = bridge.schema_artifacts
        assert isinstance(publisher, RunScopedSchemaArtifactPublisher)

        binding = catalog.bind_ref(catalog.entries[0].capability_ref)
        digest = "d" * 64
        run_key = CapabilityDiscoveryEnvironment.reference_key(
            run_id=context.run_id,
            environ=dict(os.environ),
        )
        assert run_key is not None
        assert publisher.expected_ref(
            binding=binding,
            content_digest=digest,
        ) == HmacCapabilityReferenceMinter(reference_key=run_key).mint_schema_artifact(
            binding_digest=binding.binding_digest,
            content_digest=digest,
        )

    async def test_without_a_writer_no_publisher_is_threaded_at_all(self) -> None:
        """The negative control: an unavailable schema, never a truncated one."""

        context = self.context()
        _client, registry = self.registry()
        dependencies = await self.dependencies(context, registry=registry)
        bridge = dependencies.capability_bridge

        assert isinstance(bridge, CapabilityBridgeComposition)
        assert bridge.schema_artifacts is None

    def test_no_expanded_capability_can_currently_reach_the_artifact_branch(
        self,
    ) -> None:
        """An honest canary over a gap this lane threaded but cannot close.

        ``ExpandedCapabilityProjector`` clamps a projected capability to exactly
        the two bounds ``schema_fits_inline`` tests — at most 32 parameters, each
        name and type truncated to 96 characters — so every tier-two record fits
        inline by construction. A catalog member is an MCP *server* card with no
        parameters at all, so it fits too. The publisher is therefore wired and
        correct, and nothing a ``deferred`` run can describe today will exercise
        it.

        That is a defensible state (an inlined schema is the better answer when
        it is the *whole* schema) but it is not an obvious one, so it is pinned
        rather than left to be rediscovered. Raising either projector bound above
        its ``CapabilitySchemaBounds`` twin makes the artifact branch reachable
        and fails here, which is the moment to prove the branch end to end.
        """

        assert (
            ExpandedCapabilityProjector._MAX_PARAMETERS
            <= CapabilitySchemaBounds.MAX_PARAMETERS
        )
        assert (
            ExpandedCapabilityProjector._PARAMETER_MAX_CHARS
            <= CapabilitySchemaBounds.MAX_PARAMETER_CHARS
        )

    async def test_discovery_decisions_reach_the_run_journal(self) -> None:
        """The recorder's binding is knowable only at the composition root.

        Nothing inside the discovery package knows the run's org, trace,
        control snapshot, or capability policy revision, so an unthreaded
        observer is an unmeasured feature. Every registered bridge tool is
        observed, which is why the observer is applied at registration rather
        than at each adapter's construction.
        """

        context = self.context()
        store = _RecordingDecisionStore()
        _client, registry = self.registry()
        dependencies = await self.dependencies(
            context,
            registry=registry,
            decision_store=store,
        )
        tools = self.tools_by_name(await self.graph_request(context, dependencies))

        await self.call(
            tools[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value],
            query=_QUERY,
            limit=10,
        )

        assert store.writes, "a registered bridge must record its decisions"
        write = store.writes[0]
        assert write.org_id == context.org_id  # type: ignore[attr-defined]
        assert write.trace_id == context.trace_id  # type: ignore[attr-defined]
        # The journal is partitioned by the *control* subject fingerprint, not
        # by F3's catalog-keyed one: a row written under the other derivation
        # could never be listed back for this run.
        assert write.subject_fingerprint == "b" * 64  # type: ignore[attr-defined]
        assert write.decision.run_id == context.run_id  # type: ignore[attr-defined]

    async def test_a_failing_decision_store_never_fails_the_run(self) -> None:
        """Telemetry is the one input that must not be able to cost anything."""

        class _Exploding:
            async def append(self, _write: object) -> object:
                raise RuntimeError("postgres://secret-host/run_control_decisions")

            async def list_for_run(self, **_kwargs: object) -> tuple[object, ...]:
                return ()

        context = self.context()
        _client, registry = self.registry()
        dependencies = await self.dependencies(
            context,
            registry=registry,
            decision_store=_Exploding(),
        )
        tools = self.tools_by_name(await self.graph_request(context, dependencies))

        found = await self.call(
            tools[CapabilityBridgeToolName.SEARCH_CAPABILITIES.value],
            query=_QUERY,
            limit=10,
        )

        assert "error" not in found, found
        assert self.ref_for(found, _READ_TOOL)


@pytest.mark.usefixtures("deferred_environment")
class TestTheSeamsNarrowRatherThanWiden(BridgeWiringHarness):
    """Every partially-resolved input lands further back, never further on."""

    async def test_a_shadow_ceiling_registers_no_bridge_at_all(self) -> None:
        """Configuration cannot widen past the run's signed feature mode."""

        context = self.context()
        _client, registry = self.registry()
        dependencies = await self.dependencies(
            context,
            registry=registry,
            binding=self.binding(run_id=context.run_id, f3=FeatureMode.SHADOW),
        )
        request = await self.graph_request(
            context,
            dependencies,
            binding=self.binding(run_id=context.run_id, f3=FeatureMode.SHADOW),
        )

        assert dependencies.capability_bridge is None
        assert self.bridge_names(request) == set()

    async def test_a_wrongly_typed_bridge_keeps_the_catalog_only_pair(
        self,
    ) -> None:
        """Half a seam is the one state worth refusing outright.

        Reading attributes off whatever arrived could mount an expansion with no
        executor — a search that discloses references nothing can dispatch.
        """

        context = self.context()
        _client, registry = self.registry()
        dependencies = await self.dependencies(context, registry=registry)
        request = await self.graph_request(
            context,
            dependencies.model_copy(update={"capability_bridge": {"minter": "nope"}}),
        )

        assert self.bridge_names(request) == {
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value,
            CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value,
        }

    async def test_no_revalidation_means_no_invoke_capability(self) -> None:
        """An unrevalidatable reference must never be offered, only refused."""

        context = self.context()
        _client, registry = self.registry()
        dependencies = await self.dependencies(context, registry=registry)
        bridge = dependencies.capability_bridge
        assert isinstance(bridge, CapabilityBridgeComposition)

        request = await self.graph_request(
            context,
            dependencies.model_copy(
                update={
                    "capability_bridge": CapabilityBridgeComposition(
                        minter=bridge.minter,
                        revalidation=None,
                        observer=bridge.observer,
                        expansion_observer=bridge.expansion_observer,
                        schema_artifacts=bridge.schema_artifacts,
                    )
                }
            ),
        )

        assert self.bridge_names(request) == {
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value,
            CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value,
        }

    async def test_a_missing_reference_secret_stays_dark_in_production(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No key, no revalidatable reference, no bridge — never a fallback key."""

        monkeypatch.delenv(CapabilityDiscoveryEnvironment.REFERENCE_SECRET)
        monkeypatch.setenv(CapabilityDiscoveryEnvironment.ENVIRONMENT, "production")
        context = self.context()
        _client, registry = self.registry()
        dependencies = await self.dependencies(context, registry=registry)

        assert dependencies.capability_catalog is None
        assert dependencies.capability_bridge is None


@pytest.mark.usefixtures("dark_environment")
class TestFeatureOffParity(BridgeWiringHarness):
    """With F3 dark this lane changed nothing — surface, work, or imports."""

    async def test_the_dark_surface_is_the_pre_f3_surface(self) -> None:
        context = self.context()
        _client, registry = self.registry()
        dependencies = await self.dependencies(context, registry=registry)
        request = await self.graph_request(context, dependencies)
        names = self.tools_by_name(request)

        assert dependencies.capability_activation is None
        assert dependencies.capability_catalog is None
        assert dependencies.capability_bridge is None
        assert self.bridge_names(request) == set()
        # ``auth_mcp`` is absent only because this fake provider advertises no
        # OAuth seam; the direct MCP tools that the registry *does* support are
        # all present, which is the property under test.
        for tool_name in (
            McpValues.ToolName.LOAD_MCP_SERVER,
            McpValues.ToolName.CALL_MCP_TOOL,
        ):
            assert tool_name in names

    async def test_the_dark_path_never_lists_mcp_cards_for_f3(self) -> None:
        """Parity in *work done*, not only in behaviour.

        The activated posture pays one extra card listing to cross the
        sync-factory / async-registry boundary. An unconfigured deployment holds
        no composer, returns on the first line, and pays nothing — so the cost
        of this lane is a cost of the feature, never of the default.
        """

        class _CountingRegistry:
            def __init__(self, inner: object) -> None:
                self._inner = inner
                self.listings = 0

            @property
            def providers(self) -> Sequence[object]:
                return self._inner.providers  # type: ignore[attr-defined]

            async def resolve_server(self, name: str) -> object:
                return await self._inner.resolve_server(name)  # type: ignore[attr-defined]

            async def list_available_servers(self, context: object) -> Sequence[object]:
                self.listings += 1
                return await self._inner.list_available_servers(context)  # type: ignore[attr-defined]

        context = self.context()
        _client, registry = self.registry()
        counting = _CountingRegistry(registry)
        factory = self.factory()
        token = RunControlContext.bind_for_run(self.binding(run_id=context.run_id))
        try:
            base = factory(context).model_copy(update={"mcp_registry": counting})
            await compose_capability_discovery(factory, base, context)
        finally:
            RunControlContext.unbind(token)

        assert factory.capability_discovery is None
        assert counting.listings == 0

    def test_the_dark_dependency_build_never_imports_the_discovery_package(
        self,
    ) -> None:
        """W1's import-graph proof still holds now that a composer exists.

        The presence gate moved up into ``build_capability_discovery_composer``
        precisely so this stays true: a handler that always held a composer
        would run the real composition on every run of every deployment. The
        subprocess variant of this proof lives in
        ``test_capability_discovery_composition``; this in-process case pins the
        gate itself, which is what would silently regress.
        """

        assert (
            build_capability_discovery_composer(
                decision_store=_RecordingDecisionStore(),
                schema_artifact_writer=_RecordingWriter(),
            )
            is None
        )


class TestTheApprovalResumeWiresTheSameBridge(BridgeWiringHarness):
    """Bug R1's lesson: two paths that must wire a seam identically."""

    def test_both_worker_composition_roots_call_one_function(self) -> None:
        """A source-level canary, because the drift is invisible at runtime.

        A resumed run whose bridge was composed differently from its own first
        turn would mint references its earlier turns cannot explain — and would
        do so silently, only for runs that happened to pause for approval.
        """

        from pathlib import Path  # noqa: PLC0415

        worker = Path(__file__).resolve().parents[3] / "src" / "runtime_worker"
        for handler in ("run.py", "approval.py"):
            source = (worker / "handlers" / handler).read_text(encoding="utf-8")
            assert "await compose_capability_discovery(" in source, handler
            assert "build_capability_discovery_composer(" in source, handler

    def test_the_worker_root_supplies_the_f8_revision_authority(self) -> None:
        """BUG-12's failure class, pinned where it actually happened.

        Every unit of the revision-bound-reference chain passed while the
        composition root supplied no revision source, so the safety property it
        exists for was inert in production and nothing failed. The chain is only
        as live as its least-wired link, and the least-wired link is the one
        no unit test can reach: the process root that owns the MCP control
        plane.

        Asserted at source level because that is the only level at which it is
        assertable — constructing the real entrypoint would require a database,
        a queue, and a backend. A runtime proof of what this enables lives in
        ``test_step8_exit_criteria``; this pins that production reaches it.
        """

        from pathlib import Path  # noqa: PLC0415

        worker = Path(__file__).resolve().parents[3] / "src" / "runtime_worker"
        root = (worker / "__main__.py").read_text(encoding="utf-8")
        assert "mcp_revision_resolver=mcp_control_plane.resolver" in root

        loop = (worker / "loop.py").read_text(encoding="utf-8")
        # Both handlers, or the approval resume composes a different generation
        # from the turn it is resuming — bug R1's lesson, one seam later.
        assert loop.count("mcp_revision_resolver=mcp_revision_resolver") == 2
        for handler in ("run.py", "approval.py"):
            source = (worker / "handlers" / handler).read_text(encoding="utf-8")
            assert "descriptor_revision_resolver=mcp_revision_resolver" in source
