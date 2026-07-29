"""BUG-08 — search, describe, and invoke are one chain, not three passing tools.

Before this lane the registrar mounted a one-tier ranker over a catalog that
holds only MCP *server* cards, so every reference the model could be shown named
a connector rather than a capability: describable, and refused at invoke because
a server card has no dispatch binding.  Each of the three tools passed its own
tests; the chain they are supposed to form did not exist.

These tests are written against that failure rather than against the units.  The
catalog is built by the real ``AuthorizedCatalogBuilder`` from real server cards
— never hand-assembled with tool entries spliced in, which is exactly the shape
that let the gap survive — and the same reference that comes back from a search
is the one described and then invoked through the real ``CallMcpTool`` into a
real Operation Gateway.  The negative control immediately below the chain test
mounts the bridge *without* the seam and asserts the old behaviour, so a
regression that silently drops expansion cannot pass as green.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.actions.policy import ConnectorWritePolicyOverrides
from agent_runtime.capabilities.discovery import (
    AuthorizedCatalogBuilder,
    BoundedCapabilityExpander,
    CapabilityActivationMode,
    CapabilityActivationResolver,
    CapabilityBridgeRecursionError,
    CapabilityBridgeRegistrar,
    CapabilityBridgeSeam,
    CapabilityBridgeToolName,
    CapabilityCatalog,
    CapabilityCatalogAccess,
    CapabilityCatalogIdentityError,
    CapabilityCatalogRevision,
    CapabilityCatalogRevisionAuthority,
    CapabilityCatalogScope,
    CapabilityDescribeTool,
    CapabilityDiscoveryErrorCode,
    CapabilityInputSchemaIdentity,
    CapabilityInvocationStatus,
    CapabilityRefRevalidation,
    CapabilityRefRevisionBinding,
    CapabilitySearchTool,
    CapabilitySource,
    ExpandedCapability,
    HmacCapabilityReferenceMinter,
    RunScopedCapabilityDisclosure,
    RunScopedCapabilityDispatchBindings,
    TwoTierCapabilitySearch,
    TwoTierCapabilitySearchResult,
)
from agent_runtime.capabilities.discovery.executor import GatewayCapabilityExecutor
from agent_runtime.capabilities.mcp import CallMcpTool, DynamicMcpRegistry, McpLoader
from agent_runtime.capabilities.mcp.execution_services import McpOperationStoredResult
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
from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.effects.contracts import EffectActorIdentity, EffectStageScope
from agent_runtime.effects.staging import EffectStager
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.surfaces_v2.ledger_models import EffectActor, LedgerEventType

from tests.unit.agent_runtime.capabilities.discovery.test_revision_authority import (
    InMemoryCatalogGenerationSource,
)
from tests.unit.agent_runtime.effects.fakes import (
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
)
from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin

_NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)
_REFERENCE_KEY = b"bug08-bridge-chain-reference-key-32-b!!!"
_SELECTION_REF = f"task-policy-selection://run_bug08/research/sha256/{'e' * 64}"
_SERVER = "linear"
_READ_TOOL = "list_issues"
_QUERY = "linear issues"

_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"team": {"type": "string"}, "limit": {"type": "integer"}},
    "required": ["team"],
    "additionalProperties": False,
}


@dataclass
class _LedgerEvents:
    """Capture the canonical operation rows the gateway itself emits."""

    rows: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def emit(
        self,
        event_type: LedgerEventType,
        payload: Mapping[str, object],
        summary: str | None = None,
    ) -> None:
        del summary
        self.rows.append((event_type.value, dict(payload)))


@dataclass
class _ResultStore:
    calls: list[str] = field(default_factory=list)

    async def store_read_result(self, *, request, output: Mapping[str, object]):  # type: ignore[no-untyped-def]
        self.calls.append(request.operation_id)
        body = {str(key): value for key, value in output.items()}
        return McpOperationStoredResult(
            result_ref=f"operation://{request.operation_id}/result",
            model_output={"items": body.get("items", [])},
        )


@dataclass
class _ArgumentStore:
    rows: dict[str, tuple[str, bytes]] = field(default_factory=dict)

    async def persist(self, *, ref: str, digest: str, canonical_bytes: bytes) -> None:
        self.rows[ref] = (digest, canonical_bytes)

    async def resolve(self, *, ref: str, digest: str) -> bytes | None:
        stored = self.rows.get(ref)
        if stored is None or stored[0] != digest:
            return None
        return stored[1]


@dataclass
class _RecordingClient:
    """Connector fake that records every dispatch that actually reached it."""

    tools: Sequence[object]
    resources: Sequence[object] = ()
    outputs: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def connect(self) -> None:
        return None

    async def list_tools(self) -> Sequence[object]:
        return self.tools

    async def list_resources(self) -> Sequence[object]:
        return self.resources

    async def call_tool(self, *, tool_name: str, arguments: Mapping[str, object]):  # type: ignore[no-untyped-def]
        self.calls.append((tool_name, dict(arguments)))
        return self.outputs.get(
            tool_name,
            {"content": [{"type": "text", "text": f"called {tool_name}"}]},
        )


class _ExplodingSearch(TwoTierCapabilitySearch):
    """A real second tier whose expansion fails the way a connector can."""

    async def search(self, **_kwargs: object) -> TwoTierCapabilitySearchResult:  # type: ignore[override]
        raise RuntimeError("postgres://secret-host/capability_catalog")


class BridgeChainHarness(DynamicMcpLoadingMixin):
    """Compose the whole real bridge: catalog → seam → registrar → gateway."""

    def context(
        self,
        *,
        run_id: str = "run_bug08",
        user_id: str = "user_bug08",
        org_id: str = "org_bug08",
    ) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id=user_id,
            org_id=org_id,
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
            trace_id="trace_bug08",
        )

    def card(self):  # type: ignore[no-untyped-def]
        return self.make_card(
            name=_SERVER,
            short_description="Track and read Linear issues for a team.",
            required_scopes=("docs:read",),
        ).model_copy(update={"server_id": "srv_linear"})

    def descriptors(self, *, read_schema: Mapping[str, Any] | None = None):  # type: ignore[no-untyped-def]
        return (
            self.make_tool(
                name=_READ_TOOL,
                description="List the issues assigned to one Linear team.",
                input_schema=dict(read_schema or _READ_SCHEMA),
            ),
        )

    def catalog(self, context: AgentRuntimeContext) -> CapabilityCatalog:
        """Build the real catalog: authorized server cards, and nothing else."""

        return AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY).build(
            context=context,
            scope=CapabilityCatalogScope.from_context(
                context,
                profile_id="research",
                policy_revision="policy_bug08",
                connector_scope_revision="scope_bug08",
            ),
            task_policy_selection_ref=_SELECTION_REF,
            mcp_server_cards=(self.card(),),
            expires_at=_NOW + timedelta(minutes=15),
        )

    def mcp(
        self,
        context: AgentRuntimeContext,
        *,
        read_schema: Mapping[str, Any] | None = None,
    ):  # type: ignore[no-untyped-def]
        client = _RecordingClient(
            tools=self.descriptors(read_schema=read_schema),
            outputs={_READ_TOOL: {"items": [{"id": "L-1"}]}},
        )
        provider = self.FakeMcpProvider(cards=(self.card(),), clients={_SERVER: client})
        registry = DynamicMcpRegistry(providers=(provider,))
        loader = McpLoader(registry)
        return (
            client,
            loader,
            CallMcpTool(registry=registry, loader=loader, runtime_context=context),
        )

    def seam(
        self, catalog: CapabilityCatalog, loader: McpLoader
    ) -> CapabilityBridgeSeam:
        return CapabilityBridgeSeam.compose(
            catalog=catalog,
            loader=loader,
            minter=HmacCapabilityReferenceMinter(reference_key=_REFERENCE_KEY),
        )

    def revalidation(
        self,
        context: AgentRuntimeContext,
        catalog: CapabilityCatalog,
    ) -> CapabilityRefRevalidation:
        from agent_runtime.control_plane.revision_binding import (  # noqa: PLC0415
            RevisionBindingRevalidator,
        )

        source = InMemoryCatalogGenerationSource()
        generation = catalog.generation
        assert generation is not None
        source.publish(
            CapabilityRefRevisionBinding.scope_for(generation, run_id=context.run_id),
            generation,
        )
        return CapabilityRefRevalidation(
            revalidator=RevisionBindingRevalidator(
                CapabilityCatalogRevisionAuthority(source)
            ),
            subject_fingerprint=AuthorizedCatalogBuilder(
                reference_key=_REFERENCE_KEY
            ).subject_fingerprint(context),
        )

    def bind_gateway(self, context: AgentRuntimeContext):  # type: ignore[no-untyped-def]
        events = _LedgerEvents()
        operation_token = OperationContext.bind_for_run(
            identity=VerifiedOperationIdentity(
                org_id=context.org_id,
                user_id=context.user_id,
                conversation_id="conv_bug08",
                run_id=context.run_id,
            ),
            policy_snapshot=ToolUsePolicySnapshot.from_response(
                workspace=None,
                user=None,
            ),
            ledger_emitter=events,
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

    def adapters(
        self,
        context: AgentRuntimeContext,
        catalog: CapabilityCatalog,
        *,
        seam: CapabilityBridgeSeam | None,
        executor: object | None = None,
        revalidation: CapabilityRefRevalidation | None = None,
    ) -> dict[str, Any]:
        """Register through the production registrar and key adapters by name."""

        registrations = CapabilityBridgeRegistrar.registrations_for(
            activation=CapabilityActivationResolver().resolve_configured(
                raw_mode=FeatureMode.ENFORCE.value,
                raw_activation=CapabilityActivationMode.DEFERRED.value,
            ),
            catalog=catalog,
            runtime_context=context,
            executor=executor,  # type: ignore[arg-type]
            revalidation=revalidation,
            seam=seam,
            clock=lambda: _NOW,
        )
        return {
            registration.name.value: registration.adapter
            for registration in registrations
        }

    async def mounted(self, context: AgentRuntimeContext):  # type: ignore[no-untyped-def]
        """Return the fully wired bridge: adapters, connector witness, seam."""

        catalog = self.catalog(context)
        client, loader, dispatcher = self.mcp(context)
        seam = self.seam(catalog, loader)
        adapters = self.adapters(
            context,
            catalog,
            seam=seam,
            executor=GatewayCapabilityExecutor(
                bindings=seam.disclosure,
                loader=loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )
        return adapters, client, seam, catalog

    @staticmethod
    def candidates(answer: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
        assert "error" not in answer, answer
        return tuple(answer["search"]["candidates"])

    @classmethod
    def ref_for(cls, answer: Mapping[str, Any], stable_name: str) -> str:
        matches = [
            candidate["capability_ref"]
            for candidate in cls.candidates(answer)
            if candidate["stable_name"] == stable_name
        ]
        assert matches, f"{stable_name} is absent from {cls.candidates(answer)}"
        return matches[0]


class TestSearchDescribeInvokeIsOneWorkingChain(BridgeChainHarness):
    """The defect closed: one reference survives all three tools."""

    async def test_search_describe_and_invoke_resolve_the_same_expanded_ref(
        self,
    ) -> None:
        """The chain BUG-08 said did not exist, asserted end to end.

        Every step reuses the reference the *previous* step returned, so a
        regression that reintroduces catalog-only search, drops the disclosure
        ledger, or loses the dispatch binding fails here rather than passing
        three isolated unit tests.
        """

        context = self.context()
        adapters, client, _seam, _catalog = await self.mounted(context)
        operation_token, service_token = self.bind_gateway(context)

        try:
            found = await adapters[
                CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
            ].ainvoke({"query": _QUERY, "limit": 10})
            capability_ref = self.ref_for(found, _READ_TOOL)

            described = await adapters[
                CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value
            ].ainvoke({"capability_ref": capability_ref})

            invoked = await adapters[
                CapabilityBridgeToolName.INVOKE_CAPABILITY.value
            ].ainvoke(
                {
                    "capability_ref": capability_ref,
                    "arguments": {"team": "ENG"},
                }
            )
        finally:
            McpOperationGatewayContext.unbind(service_token)
            OperationContext.unbind(operation_token)

        assert "error" not in described, described
        capability = described["description"]["capability"]
        assert capability["capability_ref"] == capability_ref
        assert capability["stable_name"] == _READ_TOOL
        assert [hint["name"] for hint in capability["parameters"]] == ["team", "limit"]

        assert "error" not in invoked, invoked
        receipt = invoked["invocation"]["receipt"]
        assert receipt["capability_ref"] == capability_ref
        assert receipt["status"] == CapabilityInvocationStatus.COMPLETED.value
        assert client.calls == [(_READ_TOOL, {"team": "ENG"})]

    async def test_search_answers_at_capability_granularity(self) -> None:
        """Tier two is what makes an answer actionable; tier one cannot be.

        A catalog member is a *server card*. If the mounted search returned only
        those, every reference would be undispatchable — which is precisely the
        state this lane closed.
        """

        context = self.context()
        adapters, _client, _seam, catalog = await self.mounted(context)

        found = await adapters[
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
        ].ainvoke({"query": _QUERY, "limit": 10})

        names = {candidate["stable_name"] for candidate in self.candidates(found)}
        assert _READ_TOOL in names
        assert {entry.stable_name for entry in catalog.entries} == {_SERVER}

    async def test_the_disclosure_ledger_holds_a_binding_for_what_search_returned(
        self,
    ) -> None:
        """Search and dispatch read the same run-scoped object, not two tables."""

        context = self.context()
        adapters, _client, seam, _catalog = await self.mounted(context)

        found = await adapters[
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
        ].ainvoke({"query": _QUERY, "limit": 10})
        capability_ref = self.ref_for(found, _READ_TOOL)

        binding = seam.disclosure.binding_for(capability_ref)
        assert binding is not None
        assert (binding.server_name, binding.tool_name) == (_SERVER, _READ_TOOL)
        assert binding.schema_digest == CapabilityInputSchemaIdentity.digest(
            _READ_SCHEMA
        )

    async def test_a_schema_change_between_search_and_invoke_fails_closed(self) -> None:
        """M-12's payoff: the disclosed schema identity is what invoke compares to.

        The reference is still current and the capability still exists, so only
        the recorded digest can catch this. It is the reason the field is
        required rather than optional.
        """

        context = self.context()
        catalog = self.catalog(context)
        _client, loader, dispatcher = self.mcp(context)
        seam = self.seam(catalog, loader)
        adapters = self.adapters(
            context,
            catalog,
            seam=seam,
            executor=GatewayCapabilityExecutor(
                bindings=seam.disclosure,
                loader=loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )
        found = await adapters[
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
        ].ainvoke({"query": _QUERY, "limit": 10})
        capability_ref = self.ref_for(found, _READ_TOOL)

        # The connector republishes the capability under a different schema.
        moved = _RecordingClient(
            tools=self.descriptors(
                read_schema={
                    "type": "object",
                    "properties": {"team_id": {"type": "string"}},
                    "required": ["team_id"],
                }
            ),
        )
        provider = self.FakeMcpProvider(cards=(self.card(),), clients={_SERVER: moved})
        moved_loader = McpLoader(DynamicMcpRegistry(providers=(provider,)))
        adapters = self.adapters(
            context,
            catalog,
            seam=seam,
            executor=GatewayCapabilityExecutor(
                bindings=seam.disclosure,
                loader=moved_loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )

        invoked = await adapters[
            CapabilityBridgeToolName.INVOKE_CAPABILITY.value
        ].ainvoke({"capability_ref": capability_ref, "arguments": {"team": "ENG"}})

        assert invoked["error"]["code"] == (
            CapabilityDiscoveryErrorCode.CAPABILITY_STALE.value
        )
        assert moved.calls == []


class TestWithoutTheSeamNothingIsDispatchable(BridgeChainHarness):
    """The negative control: the exact behaviour BUG-08 described.

    Without this, a regression that quietly stops mounting the second tier would
    leave every chain assertion above failing for an unexplained reason. With it,
    the two mountings are contrasted directly and the difference is the lane.
    """

    async def test_a_catalog_only_search_can_only_return_server_cards(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        adapters = self.adapters(context, catalog, seam=None)

        found = await adapters[
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
        ].ainvoke({"query": _QUERY, "limit": 10})

        assert {candidate["stable_name"] for candidate in self.candidates(found)} == {
            _SERVER
        }

    async def test_the_only_ref_it_can_return_is_refused_at_invoke(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        _client, loader, dispatcher = self.mcp(context)
        adapters = self.adapters(
            context,
            catalog,
            seam=None,
            executor=GatewayCapabilityExecutor(
                bindings=RunScopedCapabilityDisclosure(catalog=catalog),
                loader=loader,
                dispatcher=dispatcher,
            ),
            revalidation=self.revalidation(context, catalog),
        )
        found = await adapters[
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
        ].ainvoke({"query": _QUERY, "limit": 10})
        capability_ref = self.ref_for(found, _SERVER)

        invoked = await adapters[
            CapabilityBridgeToolName.INVOKE_CAPABILITY.value
        ].ainvoke({"capability_ref": capability_ref, "arguments": {}})

        assert invoked["error"]["code"] == (
            CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE.value
        )


class TestDisclosureIsRunAndSubjectScoped(BridgeChainHarness):
    """A reference is a fact about one run and one subject, and nothing wider."""

    async def test_a_ref_disclosed_to_another_run_resolves_nowhere_here(self) -> None:
        """Two identical runs, different run ids: the refs do not cross.

        Opaque refs are keyed derivations over the catalog id, itself derived
        from the whole scope identity, so a second run mints different
        references *and* holds a different ledger. Both halves are asserted:
        the reference is not even the same value, and the foreign one resolves
        to nothing.
        """

        first = self.context(run_id="run_one")
        second = self.context(run_id="run_two")
        first_adapters, _client, first_seam, _catalog = await self.mounted(first)
        second_adapters, _other, second_seam, _other_catalog = await self.mounted(
            second
        )

        first_found = await first_adapters[
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
        ].ainvoke({"query": _QUERY, "limit": 10})
        second_found = await second_adapters[
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
        ].ainvoke({"query": _QUERY, "limit": 10})
        first_ref = self.ref_for(first_found, _READ_TOOL)
        second_ref = self.ref_for(second_found, _READ_TOOL)

        assert first_ref != second_ref
        assert first_seam.disclosure.binding_for(first_ref) is not None
        assert second_seam.disclosure.binding_for(first_ref) is None
        assert first_seam.disclosure.binding_for(second_ref) is None

        described = await second_adapters[
            CapabilityBridgeToolName.DESCRIBE_CAPABILITY.value
        ].ainvoke({"capability_ref": first_ref})
        invoked = await second_adapters[
            CapabilityBridgeToolName.INVOKE_CAPABILITY.value
        ].ainvoke({"capability_ref": first_ref, "arguments": {"team": "ENG"}})

        assert described["error"]["code"] == (
            CapabilityDiscoveryErrorCode.CAPABILITY_NOT_FOUND.value
        )
        assert invoked["error"]["code"] == (
            CapabilityDiscoveryErrorCode.CAPABILITY_NOT_FOUND.value
        )

    async def test_a_disclosed_ref_does_not_resolve_for_another_subject(self) -> None:
        """Same run id, different user: the catalog is not this subject's."""

        owner = self.context(run_id="run_shared")
        intruder = self.context(run_id="run_shared", user_id="user_intruder")
        adapters, _client, seam, catalog = await self.mounted(owner)

        found = await adapters[
            CapabilityBridgeToolName.SEARCH_CAPABILITIES.value
        ].ainvoke({"query": _QUERY, "limit": 10})
        capability_ref = self.ref_for(found, _READ_TOOL)

        stolen = CapabilityDescribeTool(
            access=CapabilityCatalogAccess(
                catalog=catalog,
                runtime_context=intruder,
                clock=lambda: _NOW,
                disclosure=seam.disclosure,
            )
        )
        described = await stolen.ainvoke({"capability_ref": capability_ref})

        assert described["error"]["code"] == (
            CapabilityDiscoveryErrorCode.CATALOG_INACTIVE.value
        )

    async def test_a_capability_owned_by_a_foreign_catalog_cannot_be_recorded(
        self,
    ) -> None:
        """The ledger's own guard, independent of ref opacity.

        Ref derivation already separates two runs, but a ledger that accepted
        any well-formed record would make that the *only* thing standing between
        them. Ownership is checked here so the property does not rest on the
        keyed derivation alone.
        """

        owner = self.context(run_id="run_owner")
        stranger = self.context(run_id="run_stranger")
        ledger = RunScopedCapabilityDisclosure(catalog=self.catalog(owner))
        foreign_catalog = self.catalog(stranger)

        foreign = ExpandedCapability(
            owner_capability_ref=foreign_catalog.entries[0].capability_ref,
            server_name=_SERVER,
            tool_name=_READ_TOOL,
            schema_digest=CapabilityInputSchemaIdentity.digest(_READ_SCHEMA),
            entry=foreign_catalog.entries[0].model_copy(
                update={"stable_name": _READ_TOOL, "display_name": _READ_TOOL}
            ),
        )

        with pytest.raises(CapabilityCatalogIdentityError) as exc_info:
            ledger.record((foreign,))

        assert str(exc_info.value) == (
            RunScopedCapabilityDisclosure.Messages.UNOWNED_CAPABILITY
        )

    async def test_the_executor_refuses_a_dispatch_for_another_subject(self) -> None:
        """The dispatch seam checks the subject itself, not only the catalog."""

        from agent_runtime.capabilities.discovery.contracts import (  # noqa: PLC0415
            CapabilityInvocationTarget,
        )
        from agent_runtime.capabilities.discovery.tool_bridge import (  # noqa: PLC0415
            CapabilityExecutionRefused,
        )

        context = self.context()
        catalog = self.catalog(context)
        _client, loader, dispatcher = self.mcp(context)
        seam = self.seam(catalog, loader)
        executor = GatewayCapabilityExecutor(
            bindings=seam.disclosure,
            loader=loader,
            dispatcher=dispatcher,
        )

        with pytest.raises(CapabilityExecutionRefused) as exc_info:
            await executor.execute(
                target=CapabilityInvocationTarget.from_catalog_entry(
                    catalog.entries[0]
                ),
                arguments={},
                idempotency_key=None,
                runtime_context=self.context(user_id="user_other"),
            )

        assert exc_info.value.code is (
            CapabilityDiscoveryErrorCode.CAPABILITY_UNAVAILABLE
        )


class TestDisclosureLedgerContract(BridgeChainHarness):
    """The ledger accumulates without ever widening what may be dispatched."""

    def _disclosed(
        self,
        catalog: CapabilityCatalog,
        *,
        schema_digest: str,
        tool_name: str = _READ_TOOL,
    ) -> ExpandedCapability:
        owner = catalog.entries[0]
        return ExpandedCapability(
            owner_capability_ref=owner.capability_ref,
            server_name=owner.stable_name,
            tool_name=tool_name,
            schema_digest=schema_digest,
            entry=owner.model_copy(
                update={
                    "capability_ref": "cap_" + "9" * 32,
                    "stable_name": tool_name,
                    "display_name": tool_name,
                }
            ),
        )

    def test_the_immutable_table_builds_from_the_expansion_result_alone(self) -> None:
        """M-12 stated as an API: no parallel descriptor stream to keep aligned.

        The pre-existing ``from_disclosed`` still takes ``(ref, server, tool)``
        triples for callers that hold the untrusted descriptors themselves; this
        is the shape the expansion lane actually produces.
        """

        catalog = self.catalog(self.context())
        capability = self._disclosed(catalog, schema_digest="a" * 64)

        table = RunScopedCapabilityDispatchBindings.from_expansion((capability,))

        binding = table.binding_for("cap_" + "9" * 32)
        assert binding is not None
        assert (binding.server_name, binding.tool_name) == (_SERVER, _READ_TOOL)
        assert binding.schema_digest == "a" * 64

    def test_the_immutable_table_still_refuses_a_duplicate_ref(self) -> None:
        catalog = self.catalog(self.context())
        capability = self._disclosed(catalog, schema_digest="a" * 64)

        with pytest.raises(ValueError) as exc_info:
            RunScopedCapabilityDispatchBindings.from_expansion((capability, capability))

        assert str(exc_info.value) == (
            RunScopedCapabilityDispatchBindings.Messages.DUPLICATE_REF
        )

    def test_an_unknown_ref_resolves_to_nothing(self) -> None:
        ledger = RunScopedCapabilityDisclosure(catalog=self.catalog(self.context()))

        assert ledger.binding_for("cap_" + "0" * 32) is None
        assert ledger.entry_for("cap_" + "0" * 32) is None

    def test_re_disclosure_refreshes_the_schema_identity_it_was_shown_with(
        self,
    ) -> None:
        """A second search is the answer the model is acting on, so it wins.

        The dispatch coordinates cannot move, so this can only ever update the
        schema identity — which is the one thing a connector may legitimately
        change between two searches in the same run.
        """

        catalog = self.catalog(self.context())
        ledger = RunScopedCapabilityDisclosure(catalog=catalog)

        ledger.record((self._disclosed(catalog, schema_digest="a" * 64),))
        ledger.record((self._disclosed(catalog, schema_digest="b" * 64),))

        binding = ledger.binding_for("cap_" + "9" * 32)
        assert binding is not None
        assert binding.schema_digest == "b" * 64

    def test_changed_dispatch_coordinates_are_refused(self) -> None:
        catalog = self.catalog(self.context())
        ledger = RunScopedCapabilityDisclosure(catalog=catalog)
        ledger.record((self._disclosed(catalog, schema_digest="a" * 64),))

        with pytest.raises(CapabilityCatalogIdentityError) as exc_info:
            ledger.record(
                (
                    self._disclosed(
                        catalog,
                        schema_digest="a" * 64,
                        tool_name="delete_issues",
                    ),
                )
            )

        assert str(exc_info.value) == (
            RunScopedCapabilityDisclosure.Messages.AMBIGUOUS_COORDINATES
        )

    def test_an_undisclosed_ref_cannot_be_bound(self) -> None:
        catalog = self.catalog(self.context())
        ledger = RunScopedCapabilityDisclosure(catalog=catalog)

        with pytest.raises(CapabilityCatalogIdentityError) as exc_info:
            ledger.bind_ref("cap_" + "9" * 32)

        assert str(exc_info.value) == (
            RunScopedCapabilityDisclosure.Messages.NOT_DISCLOSED
        )

    def test_an_ungenerated_catalog_cannot_bind_a_disclosed_ref(self) -> None:
        """The same fail-closed rule the catalog itself applies."""

        catalog = self.catalog(self.context())
        ungenerated = CapabilityCatalog(
            scope=catalog.scope,
            revision=CapabilityCatalogRevision(
                **catalog.revision.model_dump(exclude={"generation"})
            ),
            entries=catalog.entries,
        )
        ledger = RunScopedCapabilityDisclosure(catalog=ungenerated)
        ledger.record((self._disclosed(ungenerated, schema_digest="a" * 64),))

        with pytest.raises(CapabilityCatalogIdentityError) as exc_info:
            ledger.bind_ref("cap_" + "9" * 32)

        assert str(exc_info.value) == CapabilityCatalog.Messages.UNGENERATED

    def test_a_disclosed_record_can_never_name_a_bridge_tool(self) -> None:
        """The recursion guard covers the new surface too, structurally.

        A disclosed record is an ordinary :class:`CapabilityIndexEntry`, so the
        chokepoint that refuses a reserved name for a catalog member refuses it
        here as well — the ledger needs no guard of its own, and a fourth bridge
        tool is covered without editing anything.
        """

        catalog = self.catalog(self.context())

        with pytest.raises(ValidationError) as exc_info:
            self._disclosed(
                catalog,
                schema_digest="a" * 64,
                tool_name=CapabilityBridgeToolName.INVOKE_CAPABILITY.value,
            )

        assert CapabilityBridgeRecursionError.Messages.RESERVED_CATALOG_NAME in str(
            exc_info.value
        )


class TestAnUnrecordableBridgeIsRefusedRatherThanMounted(BridgeChainHarness):
    """Composition errors surface as errors, never as a silently narrower run."""

    def test_expansion_without_a_ledger_is_refused(self) -> None:
        context = self.context()
        catalog = self.catalog(context)
        _client, loader, _dispatcher = self.mcp(context)
        seam = self.seam(catalog, loader)

        with pytest.raises(ValueError) as exc_info:
            CapabilitySearchTool(
                access=CapabilityCatalogAccess(
                    catalog=catalog,
                    runtime_context=context,
                    clock=lambda: _NOW,
                ),
                expansion=seam.expansion,
            )

        assert str(exc_info.value) == (
            CapabilitySearchTool.Messages.UNRECORDABLE_EXPANSION
        )

    def test_a_synchronous_call_to_an_expanding_search_is_refused(self) -> None:
        """Answering from tier one alone would look complete and be narrower."""

        context = self.context()
        catalog = self.catalog(context)
        _client, loader, _dispatcher = self.mcp(context)
        seam = self.seam(catalog, loader)
        tool = CapabilitySearchTool(
            access=CapabilityCatalogAccess(
                catalog=catalog,
                runtime_context=context,
                clock=lambda: _NOW,
                disclosure=seam.disclosure,
            ),
            expansion=seam.expansion,
        )

        with pytest.raises(TypeError) as exc_info:
            tool.invoke(_QUERY)

        assert str(exc_info.value) == (
            CapabilitySearchTool.Messages.SYNCHRONOUS_EXPANSION
        )

    def test_a_ledger_built_for_another_catalog_cannot_be_attached(self) -> None:
        context = self.context()
        catalog = self.catalog(context)

        with pytest.raises(ValueError) as exc_info:
            CapabilityCatalogAccess(
                catalog=catalog,
                runtime_context=context,
                clock=lambda: _NOW,
                disclosure=RunScopedCapabilityDisclosure(
                    catalog=self.catalog(self.context(run_id="run_elsewhere"))
                ),
            )

        assert str(exc_info.value) == (
            CapabilityCatalogAccess.Messages.FOREIGN_DISCLOSURE
        )

    async def test_a_failing_expansion_refuses_rather_than_answering_narrowly(
        self,
    ) -> None:
        """A broken second tier must not look like a complete catalog answer.

        Falling back to tier one would return only server cards — every one of
        them undispatchable — in the exact shape of a successful answer. The
        model cannot tell those apart, so the honest outcome is a refusal, and
        the connector's own failure text never reaches it.
        """

        context = self.context()
        catalog = self.catalog(context)
        _client, loader, _dispatcher = self.mcp(context)
        seam = self.seam(catalog, loader)
        tool = CapabilitySearchTool(
            access=CapabilityCatalogAccess(
                catalog=catalog,
                runtime_context=context,
                clock=lambda: _NOW,
                disclosure=seam.disclosure,
            ),
            expansion=_ExplodingSearch(
                expander=BoundedCapabilityExpander(
                    loader=loader,
                    minter=HmacCapabilityReferenceMinter(reference_key=_REFERENCE_KEY),
                )
            ),
        )

        answer = await tool.ainvoke({"query": _QUERY, "limit": 10})

        assert answer["error"]["code"] == (
            CapabilityDiscoveryErrorCode.EXECUTION_FAILED.value
        )
        assert "postgres" not in answer["error"]["safe_message"]
        assert seam.disclosure.binding_for("cap_" + "9" * 32) is None


class TestTheModelFacingSurfaceIsUnchanged(BridgeChainHarness):
    """Mounting the seam changes the bridge's reach, never its payload.

    The three bridge tool schemas were measured at 830 tokens in the provider
    payload (EXECUTION-BACKLOG §5). This lane must not move that number, so the
    registered names, order, and argument schemas are pinned to be identical
    with and without the seam.
    """

    def test_the_registered_schemas_are_identical_with_and_without_the_seam(
        self,
    ) -> None:
        context = self.context()
        catalog = self.catalog(context)
        _client, loader, dispatcher = self.mcp(context)
        seam = self.seam(catalog, loader)
        executor = GatewayCapabilityExecutor(
            bindings=seam.disclosure,
            loader=loader,
            dispatcher=dispatcher,
        )
        revalidation = self.revalidation(context, catalog)

        def signature(*, mounted: CapabilityBridgeSeam | None) -> tuple[object, ...]:
            return tuple(
                (
                    registration.name.value,
                    registration.args_schema.model_json_schema(),
                    registration.adapter.name,
                    registration.adapter.description,
                )
                for registration in CapabilityBridgeRegistrar.registrations_for(
                    activation=CapabilityActivationResolver().resolve_configured(
                        raw_mode=FeatureMode.ENFORCE.value,
                        raw_activation=CapabilityActivationMode.DEFERRED.value,
                    ),
                    catalog=catalog,
                    runtime_context=context,
                    executor=executor,
                    revalidation=revalidation,
                    seam=mounted,
                    clock=lambda: _NOW,
                )
            )

        assert signature(mounted=seam) == signature(mounted=None)

    def test_the_catalog_still_holds_only_dispatchable_server_cards(self) -> None:
        """Disclosure is not catalog membership, and must never become it."""

        catalog = self.catalog(self.context())

        assert all(
            entry.source is CapabilitySource.MCP_SERVER for entry in catalog.entries
        )
        assert catalog.revision.descriptor_count == len(catalog.entries)
