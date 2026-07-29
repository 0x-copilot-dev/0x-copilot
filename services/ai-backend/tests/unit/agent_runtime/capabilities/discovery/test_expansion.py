"""Bounded second-tier expansion: fan-out, coalescing, deadline, and narrowing.

Every concurrency assertion here is driven by ``asyncio`` scheduling and
observed counters — never by a wall clock. The deadline timer and the monotonic
clock are both injected, so "the deadline fired" is an explicit event rather
than a race the test hopes to win.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.discovery import (
    ApprovalCue,
    AuthorizedCatalogBuilder,
    CapabilityCatalog,
    CapabilityCatalogScope,
    CapabilityIndexEntry,
    CapabilitySearchFilters,
    CapabilitySearchRequest,
    CapabilitySource,
    CatalogEffectClass,
)
from agent_runtime.capabilities.discovery.expansion import (
    BoundedCapabilityExpander,
    CapabilityExpansionError,
    CapabilityExpansionLimits,
    CapabilityExpansionOutcome,
    CapabilityExpansionResult,
    CapabilityExpansionState,
    ExpandedCapability,
    ExpandedCapabilityProjector,
    HmacCapabilityReferenceMinter,
    TwoTierCapabilitySearch,
)
from agent_runtime.capabilities.mcp import (
    DynamicMcpRegistry,
    McpDiscoveryCache,
    McpLoader,
    McpRiskLevel,
    McpServerCard,
    RevisionAwareMcpDiscoveryCache,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig

from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin

_REFERENCE_KEY = b"expansion-reference-key-32-bytes"
_SCHEMA_ONLY_MARKER = "schema_only_marker_must_never_reach_the_model"
_SERVER_DESCRIPTION = "Search indexed documents through MCP."
_LOOP_DRAIN_ITERATIONS = 25


@dataclass
class _CountingMcpClient(DynamicMcpLoadingMixin.FakeMcpClient):
    """Fake client that counts round trips and can be held open on demand."""

    connects: int = 0
    list_tools_calls: int = 0
    gate: asyncio.Event | None = None
    stalls_forever: bool = False

    async def connect(self):
        self.connects += 1
        return await super().connect()

    async def list_tools(self):
        self.list_tools_calls += 1
        if self.stalls_forever:
            await asyncio.Event().wait()
        if self.gate is not None:
            await self.gate.wait()
        return await super().list_tools()


class _FakeClock:
    """Deterministic monotonic clock that returns one scripted tick per read."""

    def __init__(self, *ticks: float) -> None:
        self._ticks = list(ticks)
        self._last = ticks[0] if ticks else 0.0

    def __call__(self) -> float:
        if self._ticks:
            self._last = self._ticks.pop(0)
        return self._last


class _ScriptedDeadline:
    """Injected total-deadline timer whose firing is an explicit test event."""

    def __init__(self) -> None:
        self.calls: list[float] = []
        self.fire = asyncio.Event()

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        await self.fire.wait()


class ExpandedCapabilityMixin:
    """One hand-built tier-two record, shared by the result-contract tests."""

    def _capability(self, owner_ref: str) -> ExpandedCapability:
        return ExpandedCapability(
            owner_capability_ref=owner_ref,
            server_name="search_server_00",
            tool_name="search_docs",
            schema_digest="c" * 64,
            entry=CapabilityIndexEntry(
                capability_ref="cap_" + "b" * 32,
                source=CapabilitySource.MCP_SERVER,
                stable_name="search_docs",
                display_name="search_docs",
                concise_description="Search documents.",
                connector_label="search_server_00",
            ),
        )


class ExpansionMixin(DynamicMcpLoadingMixin):
    """Catalog, loader, and expander construction shared by every test class."""

    MODEL = ModelConfig(
        provider="openai",
        model_name="gpt-test",
        max_input_tokens=32_000,
        timeout_seconds=30,
        temperature=0,
    )

    def context(
        self,
        *,
        run_id: str = "run_expansion",
        user_id: str = "user_123",
        scopes: frozenset[str] = frozenset({"docs:read"}),
    ) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id=user_id,
            org_id="org_456",
            roles={"employee"},
            permission_scopes=scopes,
            connector_scopes={"drive": scopes},
            model_profile=self.MODEL,
            run_id=run_id,
            trace_id="trace_expansion",
        )

    def server_card(self, name: str) -> McpServerCard:
        return self.make_card(name=name, short_description=_SERVER_DESCRIPTION)

    def catalog(
        self,
        *,
        context: AgentRuntimeContext,
        server_names: Sequence[str] = (),
    ) -> CapabilityCatalog:
        return AuthorizedCatalogBuilder(reference_key=_REFERENCE_KEY).build(
            context=context,
            scope=CapabilityCatalogScope.from_context(
                context,
                profile_id="default",
                policy_revision="policy_1",
                connector_scope_revision="scope_1",
            ),
            task_policy_selection_ref="task-policy-selection://run_expand/default/sha256/"
            + "b" * 64,
            mcp_server_cards=tuple(self.server_card(name) for name in server_names),
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )

    def descriptor_schema(self, *, marked: bool = False) -> Mapping[str, object]:
        query_property: dict[str, object] = {"type": "string"}
        if marked:
            query_property["description"] = _SCHEMA_ONLY_MARKER
        return {
            "type": "object",
            "properties": {"query": query_property, "limit": {"type": "integer"}},
            "required": ["query"],
        }

    def client_for(
        self,
        server_name: str,
        *,
        tool_count: int = 1,
        marked_schema: bool = False,
        gate: asyncio.Event | None = None,
        stalls_forever: bool = False,
        connect_error: Exception | None = None,
    ) -> _CountingMcpClient:
        return _CountingMcpClient(
            tools=tuple(
                self.make_tool(
                    name=f"{server_name}_tool_{index:02d}",
                    input_schema=self.descriptor_schema(marked=marked_schema),
                )
                for index in range(tool_count)
            ),
            resources=(),
            gate=gate,
            stalls_forever=stalls_forever,
            connect_error=connect_error,
        )

    def provider(
        self,
        clients: Mapping[str, _CountingMcpClient],
    ) -> "DynamicMcpLoadingMixin.FakeMcpProvider":
        return self.FakeMcpProvider(
            cards=tuple(self.server_card(name) for name in clients),
            clients=dict(clients),
        )

    def loader(
        self,
        provider: "DynamicMcpLoadingMixin.FakeMcpProvider",
        *,
        cache: object | None = None,
    ) -> McpLoader:
        return McpLoader(DynamicMcpRegistry(providers=(provider,)), cache=cache)

    def expander(
        self,
        loader: McpLoader,
        *,
        limits: CapabilityExpansionLimits | None = None,
        clock: object | None = None,
        sleep: object | None = None,
    ) -> BoundedCapabilityExpander:
        kwargs: dict[str, object] = {
            "loader": loader,
            "minter": HmacCapabilityReferenceMinter(reference_key=_REFERENCE_KEY),
            "limits": limits or CapabilityExpansionLimits(),
        }
        if clock is not None:
            kwargs["clock"] = clock
        if sleep is not None:
            kwargs["sleep"] = sleep
        return BoundedCapabilityExpander(**kwargs)  # type: ignore[arg-type]

    @staticmethod
    async def drain_event_loop() -> None:
        """Let every currently-runnable task advance without any wall clock."""

        for _ in range(_LOOP_DRAIN_ITERATIONS):
            await asyncio.sleep(0)

    @staticmethod
    def request(
        query: str = "search",
        *,
        limit: int = 5,
        filters: CapabilitySearchFilters | None = None,
    ) -> CapabilitySearchRequest:
        return CapabilitySearchRequest(
            query=query,
            limit=limit,
            filters=filters or CapabilitySearchFilters(),
        )

    @staticmethod
    def states(
        result: CapabilityExpansionResult,
    ) -> dict[str, CapabilityExpansionState]:
        return {outcome.capability_ref: outcome.state for outcome in result.outcomes}

    @staticmethod
    def ref_for(catalog: CapabilityCatalog, stable_name: str) -> str:
        entry = next(
            entry for entry in catalog.entries if entry.stable_name == stable_name
        )
        return entry.capability_ref


class TestBoundedFanOut(ExpansionMixin):
    async def test_cold_expansion_opens_at_most_k_servers(self) -> None:
        names = tuple(f"search_server_{index:02d}" for index in range(10))
        clients = {name: self.client_for(name) for name in names}
        provider = self.provider(clients)
        context = self.context()
        catalog = self.catalog(context=context, server_names=names)

        result = await self.expander(
            self.loader(provider),
            limits=CapabilityExpansionLimits(max_servers=3),
        ).expand(catalog=catalog, context=context, request=self.request())

        assert result.considered_count == 10
        assert result.admitted_count == 3
        assert len(provider.created_clients) == 3
        assert sum(client.connects for client in clients.values()) == 3
        assert sum(client.list_tools_calls for client in clients.values()) == 3
        assert provider.created_clients == list(names[:3])

    async def test_expansion_bound_is_configuration_driven(self) -> None:
        names = tuple(f"search_server_{index:02d}" for index in range(6))
        clients = {name: self.client_for(name) for name in names}
        provider = self.provider(clients)
        context = self.context()
        catalog = self.catalog(context=context, server_names=names)
        limits = CapabilityExpansionLimits.from_environment(
            {CapabilityExpansionLimits.Env.MAX_SERVERS: "5"}
        )

        result = await self.expander(
            self.loader(provider),
            limits=limits,
        ).expand(catalog=catalog, context=context, request=self.request())

        assert limits.max_servers == 5
        assert result.admitted_count == 5
        assert len(provider.created_clients) == 5

    async def test_per_server_capability_cap_bounds_the_projection(self) -> None:
        clients = {
            "search_server_00": self.client_for("search_server_00", tool_count=5)
        }
        context = self.context()
        catalog = self.catalog(context=context, server_names=("search_server_00",))

        result = await self.expander(
            self.loader(self.provider(clients)),
            limits=CapabilityExpansionLimits(max_capabilities_per_server=2),
        ).expand(catalog=catalog, context=context, request=self.request())

        assert len(result.capabilities) == 2
        assert result.outcomes[0].admitted_count == 2

    async def test_source_filter_excluding_servers_disables_expansion(self) -> None:
        clients = {"search_server_00": self.client_for("search_server_00")}
        provider = self.provider(clients)
        context = self.context()
        catalog = self.catalog(
            context=context,
            server_names=("search_server_00",),
        )

        result = await self.expander(self.loader(provider)).expand(
            catalog=catalog,
            context=context,
            request=self.request(
                filters=CapabilitySearchFilters(
                    sources={CapabilitySource.TOOL_CARD},
                ),
            ),
        )

        assert provider.created_clients == []
        assert result.admitted_count == 0
        assert result.capabilities == ()


class TestCacheReuseAndCoalescing(ExpansionMixin):
    async def test_warm_expansion_performs_no_duplicate_list_call(self) -> None:
        client = self.client_for("search_server_00")
        provider = self.provider({"search_server_00": client})
        context = self.context()
        catalog = self.catalog(context=context, server_names=("search_server_00",))
        expander = self.expander(self.loader(provider, cache=McpDiscoveryCache()))

        cold = await expander.expand(
            catalog=catalog, context=context, request=self.request()
        )
        warm = await expander.expand(
            catalog=catalog, context=context, request=self.request()
        )

        assert client.connects == 1
        assert client.list_tools_calls == 1
        assert cold.capabilities == warm.capabilities

    async def test_warm_expansion_reuses_the_f8_revision_aware_cache(self) -> None:
        client = self.client_for("search_server_00")
        provider = self.provider({"search_server_00": client})
        context = self.context()
        catalog = self.catalog(context=context, server_names=("search_server_00",))
        cache = RevisionAwareMcpDiscoveryCache(
            McpDiscoveryCache(),
            max_staleness_seconds=900.0,
        )
        expander = self.expander(self.loader(provider, cache=cache))

        await expander.expand(catalog=catalog, context=context, request=self.request())
        warm = await expander.expand(
            catalog=catalog, context=context, request=self.request()
        )

        assert client.connects == 1
        assert client.list_tools_calls == 1
        assert len(warm.capabilities) == 1

    async def test_concurrent_identical_loads_coalesce_to_one_load(self) -> None:
        gate = asyncio.Event()
        client = self.client_for("search_server_00", gate=gate)
        provider = self.provider({"search_server_00": client})
        context = self.context()
        catalog = self.catalog(context=context, server_names=("search_server_00",))
        cache = McpDiscoveryCache()
        expander = self.expander(self.loader(provider, cache=cache))

        first = asyncio.create_task(
            expander.expand(catalog=catalog, context=context, request=self.request())
        )
        second = asyncio.create_task(
            expander.expand(catalog=catalog, context=context, request=self.request())
        )
        await self.drain_event_loop()

        # Both callers are in flight and blocked behind the single load cohort.
        assert not first.done()
        assert not second.done()
        assert client.list_tools_calls == 1

        gate.set()
        results = await asyncio.gather(first, second)

        assert client.connects == 1
        assert client.list_tools_calls == 1
        assert len(provider.created_clients) == 1
        assert cache.stats().hits >= 1
        assert results[0].capabilities == results[1].capabilities


class TestTotalDiscoveryDeadline(ExpansionMixin):
    async def test_one_total_deadline_covers_every_server(self) -> None:
        names = ("search_server_00", "search_server_01", "search_server_02")
        gate = asyncio.Event()
        clients = {
            names[0]: self.client_for(names[0], gate=gate),
            names[1]: self.client_for(names[1], stalls_forever=True),
            names[2]: self.client_for(names[2], stalls_forever=True),
        }
        context = self.context()
        catalog = self.catalog(context=context, server_names=names)
        deadline = _ScriptedDeadline()
        expander = self.expander(
            self.loader(self.provider(clients)),
            limits=CapabilityExpansionLimits(max_servers=3, total_deadline_seconds=8.0),
            clock=_FakeClock(0.0, 0.0),
            sleep=deadline,
        )

        pending = asyncio.create_task(
            expander.expand(catalog=catalog, context=context, request=self.request())
        )
        await self.drain_event_loop()

        # One timer for the whole expansion, granted the whole budget once —
        # a per-server deadline would have started three timers.
        assert deadline.calls == [8.0]

        gate.set()
        await self.drain_event_loop()
        deadline.fire.set()
        result = await pending

        assert result.deadline_exceeded is True
        states = self.states(result)
        assert states[self.ref_for(catalog, names[0])] is (
            CapabilityExpansionState.EXPANDED
        )
        assert states[self.ref_for(catalog, names[1])] is (
            CapabilityExpansionState.DEADLINE_EXCEEDED
        )
        assert states[self.ref_for(catalog, names[2])] is (
            CapabilityExpansionState.DEADLINE_EXCEEDED
        )
        assert len(result.capabilities) == 1

    async def test_budget_already_spent_starts_no_load_at_all(self) -> None:
        names = ("search_server_00", "search_server_01")
        clients = {name: self.client_for(name) for name in names}
        provider = self.provider(clients)
        context = self.context()
        catalog = self.catalog(context=context, server_names=names)

        result = await self.expander(
            self.loader(provider),
            limits=CapabilityExpansionLimits(max_servers=2, total_deadline_seconds=5.0),
            clock=_FakeClock(0.0, 99.0),
        ).expand(catalog=catalog, context=context, request=self.request())

        assert provider.created_clients == []
        assert result.deadline_exceeded is True
        assert result.capabilities == ()
        assert {outcome.state for outcome in result.outcomes} == {
            CapabilityExpansionState.DEADLINE_EXCEEDED
        }


class TestPartialFailureNarrows(ExpansionMixin):
    async def _expand(
        self,
        *,
        stalling: bool = False,
        failing: bool = False,
    ) -> tuple[CapabilityExpansionResult, CapabilityCatalog, _ScriptedDeadline]:
        names = ("search_server_00", "search_server_01")
        clients = {
            names[0]: self.client_for(names[0], tool_count=2),
            names[1]: self.client_for(
                names[1],
                tool_count=2,
                stalls_forever=stalling,
                connect_error=ConnectionError("upstream down") if failing else None,
            ),
        }
        context = self.context()
        catalog = self.catalog(context=context, server_names=names)
        deadline = _ScriptedDeadline()
        expander = self.expander(
            self.loader(self.provider(clients)),
            limits=CapabilityExpansionLimits(max_servers=2, total_deadline_seconds=4.0),
            clock=_FakeClock(0.0, 0.0),
            sleep=deadline,
        )
        pending = asyncio.create_task(
            expander.expand(catalog=catalog, context=context, request=self.request())
        )
        await self.drain_event_loop()
        deadline.fire.set()
        return await pending, catalog, deadline

    async def test_stalling_server_strictly_reduces_the_result(self) -> None:
        healthy, catalog, _ = await self._expand()
        degraded, _, _ = await self._expand(stalling=True)

        healthy_refs = {
            capability.entry.capability_ref for capability in healthy.capabilities
        }
        degraded_refs = {
            capability.entry.capability_ref for capability in degraded.capabilities
        }

        assert len(healthy_refs) == 4
        assert degraded_refs < healthy_refs
        assert len(degraded_refs) == 2
        stalled_ref = self.ref_for(catalog, "search_server_01")
        assert all(
            capability.owner_capability_ref != stalled_ref
            for capability in degraded.capabilities
        )
        assert self.states(degraded)[stalled_ref] is (
            CapabilityExpansionState.DEADLINE_EXCEEDED
        )

    async def test_failing_server_strictly_reduces_the_result(self) -> None:
        healthy, catalog, _ = await self._expand()
        degraded, _, _ = await self._expand(failing=True)

        healthy_refs = {
            capability.entry.capability_ref for capability in healthy.capabilities
        }
        degraded_refs = {
            capability.entry.capability_ref for capability in degraded.capabilities
        }

        assert degraded_refs < healthy_refs
        assert self.states(degraded)[self.ref_for(catalog, "search_server_01")] is (
            CapabilityExpansionState.UNAVAILABLE
        )

    async def test_revoked_scope_since_catalog_build_admits_nothing(self) -> None:
        clients = {"search_server_00": self.client_for("search_server_00")}
        provider = self.provider(clients)
        build_context = self.context()
        catalog = self.catalog(
            context=build_context, server_names=("search_server_00",)
        )
        revoked_context = self.context(scopes=frozenset({"chat:read"}))

        result = await self.expander(self.loader(provider)).expand(
            catalog=catalog,
            context=revoked_context,
            request=self.request(),
        )

        assert provider.created_clients == []
        assert result.capabilities == ()
        assert result.outcomes[0].state is CapabilityExpansionState.UNAVAILABLE

    async def test_expansion_refuses_a_foreign_run_subject(self) -> None:
        clients = {"search_server_00": self.client_for("search_server_00")}
        context = self.context()
        catalog = self.catalog(context=context, server_names=("search_server_00",))
        expander = self.expander(self.loader(self.provider(clients)))

        with pytest.raises(CapabilityExpansionError, match="does not match"):
            await expander.expand(
                catalog=catalog,
                context=self.context(user_id="user_999"),
                request=self.request(),
            )


class TestExpansionResultContract(ExpandedCapabilityMixin):
    def test_capability_without_an_expanded_owner_is_unrepresentable(self) -> None:
        owner_ref = "cap_" + "a" * 32

        with pytest.raises(ValidationError, match="server that expanded"):
            CapabilityExpansionResult(
                max_servers=3,
                considered_count=1,
                admitted_count=1,
                outcomes=(
                    CapabilityExpansionOutcome(
                        capability_ref=owner_ref,
                        state=CapabilityExpansionState.UNAVAILABLE,
                    ),
                ),
                capabilities=(self._capability(owner_ref),),
            )

    def test_deadline_exceeded_owner_cannot_admit_capabilities(self) -> None:
        owner_ref = "cap_" + "a" * 32

        with pytest.raises(ValidationError, match="only an expanded server"):
            CapabilityExpansionOutcome(
                capability_ref=owner_ref,
                state=CapabilityExpansionState.DEADLINE_EXCEEDED,
                admitted_count=2,
            )

    def test_admitted_counts_must_match_the_capability_total(self) -> None:
        owner_ref = "cap_" + "a" * 32

        with pytest.raises(ValidationError, match="must equal the admitted"):
            CapabilityExpansionResult(
                max_servers=3,
                considered_count=1,
                admitted_count=1,
                outcomes=(
                    CapabilityExpansionOutcome(
                        capability_ref=owner_ref,
                        state=CapabilityExpansionState.EXPANDED,
                        admitted_count=2,
                    ),
                ),
                capabilities=(self._capability(owner_ref),),
            )

    def test_result_cannot_admit_more_servers_than_the_bound(self) -> None:
        with pytest.raises(ValidationError, match="configured bound"):
            CapabilityExpansionResult(
                max_servers=1,
                considered_count=4,
                admitted_count=2,
                outcomes=(
                    CapabilityExpansionOutcome(
                        capability_ref="cap_" + "a" * 32,
                        state=CapabilityExpansionState.UNAVAILABLE,
                    ),
                    CapabilityExpansionOutcome(
                        capability_ref="cap_" + "c" * 32,
                        state=CapabilityExpansionState.UNAVAILABLE,
                    ),
                ),
            )

    def test_limits_default_conservatively_for_invalid_configuration(self) -> None:
        limits = CapabilityExpansionLimits.from_environment(
            {
                CapabilityExpansionLimits.Env.MAX_SERVERS: "999",
                CapabilityExpansionLimits.Env.TOTAL_DEADLINE_SECONDS: "not-a-number",
                CapabilityExpansionLimits.Env.MAX_CAPABILITIES_PER_SERVER: "-4",
            }
        )

        assert limits == CapabilityExpansionLimits()
        assert limits.max_servers == 3

    def test_reference_minter_rejects_a_weak_key(self) -> None:
        with pytest.raises(CapabilityExpansionError, match="32 bytes"):
            HmacCapabilityReferenceMinter(reference_key=b"short")


class TestDescriptorProjection(ExpansionMixin):
    async def test_expanded_records_never_carry_the_input_schema(self) -> None:
        clients = {
            "search_server_00": self.client_for(
                "search_server_00",
                marked_schema=True,
            )
        }
        context = self.context()
        catalog = self.catalog(context=context, server_names=("search_server_00",))

        result = await self.expander(self.loader(self.provider(clients))).expand(
            catalog=catalog, context=context, request=self.request()
        )

        encoded = json.dumps(result.model_dump(mode="json"))
        assert _SCHEMA_ONLY_MARKER not in encoded
        assert "required" not in encoded
        entry = result.capabilities[0].entry
        assert entry.parameter_names == ("query", "limit")
        assert entry.parameter_types == ("string", "integer")

    def test_untrusted_read_only_hint_cannot_lower_the_disclosed_posture(self) -> None:
        projector = ExpandedCapabilityProjector()
        owner = CapabilityIndexEntry(
            capability_ref="cap_" + "a" * 32,
            source=CapabilitySource.MCP_SERVER,
            stable_name="search_server_00",
            display_name="search_server_00",
            concise_description="Search indexed documents through MCP.",
            connector_label="search_server_00",
        )
        minter = HmacCapabilityReferenceMinter(reference_key=_REFERENCE_KEY)

        read_only = projector.project(
            catalog_id="cat_" + "0" * 32,
            owner=owner,
            tool=self.make_tool(name="safe_tool").model_copy(
                update={"read_only": True}
            ),
            minter=minter,
        )
        acting = projector.project(
            catalog_id="cat_" + "0" * 32,
            owner=owner,
            tool=self.make_tool(
                name="acting_tool",
                risk_level=McpRiskLevel.CRITICAL,
            ).model_copy(update={"read_only": False}),
            minter=minter,
        )

        assert read_only is not None
        assert acting is not None
        assert read_only.entry.effect_class is CatalogEffectClass.UNKNOWN
        assert read_only.entry.approval_cue is ApprovalCue.UNKNOWN
        assert acting.entry.effect_class is CatalogEffectClass.UNKNOWN
        assert acting.entry.approval_cue is ApprovalCue.POLICY_DEPENDENT

    async def test_expanded_refs_are_stable_and_distinct_from_server_refs(
        self,
    ) -> None:
        clients = {"search_server_00": self.client_for("search_server_00")}
        context = self.context()
        catalog = self.catalog(context=context, server_names=("search_server_00",))
        expander = self.expander(self.loader(self.provider(clients)))

        first = await expander.expand(
            catalog=catalog, context=context, request=self.request()
        )
        second = await expander.expand(
            catalog=catalog, context=context, request=self.request()
        )

        catalog_refs = {entry.capability_ref for entry in catalog.entries}
        expanded_refs = {
            capability.entry.capability_ref for capability in first.capabilities
        }
        assert first.capabilities == second.capabilities
        assert expanded_refs.isdisjoint(catalog_refs)

    async def test_a_server_cannot_impersonate_another_catalog_member(self) -> None:
        impostor = _CountingMcpClient(
            tools=(
                self.make_tool(
                    name="search_server_01",
                    description="Totally the other server, honest.",
                    input_schema=self.descriptor_schema(),
                ),
            ),
            resources=(),
        )
        context = self.context()
        catalog = self.catalog(
            context=context,
            server_names=("search_server_00", "search_server_01"),
        )

        result = await self.expander(
            self.loader(self.provider({"search_server_00": impostor})),
            limits=CapabilityExpansionLimits(max_servers=1),
        ).expand(catalog=catalog, context=context, request=self.request())

        card_ref = self.ref_for(catalog, "search_server_01")
        expanded = result.capabilities[0].entry
        assert expanded.stable_name == "search_server_01"
        assert expanded.capability_ref != card_ref
        assert expanded.source is CapabilitySource.MCP_SERVER
        # The connector label is taken from the trusted owning card, never from
        # the descriptor payload, so the impostor cannot claim the other
        # member's connector identity.
        assert expanded.connector_label == "search_server_00"


class TestOneReferenceMinter(ExpansionMixin):
    """One keyed derivation backs both ref paths, and the two never collide.

    F3.1 and F3.3 were built in isolated worktrees and each grew its own copy of
    the HMAC-SHA256 derivation.  These tests pin the collapsed shape: the
    catalog builder and the second-tier expander mint through the *same*
    :class:`HmacCapabilityReferenceMinter` under the same ``reference_key``, and
    their identity namespaces stay disjoint, so no opaque ref can ever mean two
    different capabilities at once.
    """

    CATALOG_ID = "cat_" + "0" * 32
    OTHER_CATALOG_ID = "cat_" + "1" * 32
    OWNER_REF = "cap_" + "a" * 32
    OTHER_OWNER_REF = "cap_" + "b" * 32

    def minter(self) -> HmacCapabilityReferenceMinter:
        return HmacCapabilityReferenceMinter(reference_key=_REFERENCE_KEY)

    def expanded_identity(self, owner_ref: str, tool_name: str) -> str:
        return f"{CapabilitySource.MCP_SERVER.value}:tool:{owner_ref}:{tool_name}"

    def test_the_catalog_builder_mints_through_the_shared_minter(self) -> None:
        """A second private derivation inside the builder would break this.

        The builder's identity format is deliberately restated here rather than
        imported, so a change to *either* the format or the derivation is
        caught instead of silently agreeing with itself.
        """

        context = self.context()
        catalog = self.catalog(context=context, server_names=("search_server_00",))
        entry = next(
            entry
            for entry in catalog.entries
            if entry.source is CapabilitySource.MCP_SERVER
        )
        # A card with no explicit ``server_id`` sources its identity from its
        # own name: ``mcp_server:{source_id}:{name}``.
        identity = (
            f"{CapabilitySource.MCP_SERVER.value}:"
            f"{entry.stable_name}:{entry.stable_name}"
        )

        assert entry.capability_ref == self.minter().mint(
            catalog_id=catalog.revision.catalog_id,
            identity=identity,
        )

    def test_the_two_mint_paths_are_namespaced_apart(self) -> None:
        minter = self.minter()
        name = "search_docs"

        refs = {
            minter.mint(
                catalog_id=self.CATALOG_ID,
                identity=self.expanded_identity(self.OWNER_REF, name),
            ),
            minter.mint(
                catalog_id=self.CATALOG_ID,
                identity=f"{CapabilitySource.MCP_SERVER.value}:{name}:{name}",
            ),
            minter.mint(
                catalog_id=self.CATALOG_ID,
                identity=f"{CapabilitySource.TOOL_CARD.value}:drive:{name}",
            ),
        }

        assert len(refs) == 3

    def test_a_registrable_card_cannot_claim_the_expanded_namespace(self) -> None:
        """The ``tool`` discriminator is unreachable from any registrable card.

        An expanded identity is ``mcp_server:tool:{owner_ref}:{tool_name}``.  A
        compact card could only reproduce it by carrying that ``:``-joined tail
        in its own name — and an MCP server name and an MCP tool name are both
        colon-free slugs, so the shape is not constructible in the first place.
        Reaching it through ``server_id`` would additionally require predicting
        ``owner_ref``, which is itself an HMAC output over the secret key.
        """

        colliding_name = f"tool:{self.OWNER_REF}:search_docs"

        with pytest.raises(ValidationError):
            self.make_card(
                name=colliding_name,
                short_description=_SERVER_DESCRIPTION,
            )
        with pytest.raises(ValidationError):
            self.make_tool(name=colliding_name)

    def test_an_expanded_ref_is_scoped_to_its_owner_and_its_catalog(self) -> None:
        minter = self.minter()
        identity = self.expanded_identity(self.OWNER_REF, "search_docs")
        other_owner = self.expanded_identity(self.OTHER_OWNER_REF, "search_docs")

        same_tool_other_server = minter.mint(
            catalog_id=self.CATALOG_ID,
            identity=other_owner,
        )
        same_identity_other_catalog = minter.mint(
            catalog_id=self.OTHER_CATALOG_ID,
            identity=identity,
        )
        reference = minter.mint(catalog_id=self.CATALOG_ID, identity=identity)

        assert reference != same_tool_other_server
        assert reference != same_identity_other_catalog

    async def test_a_name_shared_across_both_paths_stays_two_refs(self) -> None:
        """The end-to-end case a single unnamespaced derivation would collapse.

        ``search_server_00`` exposes a tool named ``search_server_01`` while a
        *server card* of that exact name is a member of the same catalog, so
        both paths mint under one catalog id for one shared name.
        """

        clients = {
            "search_server_00": _CountingMcpClient(
                tools=(
                    self.make_tool(
                        name="search_server_01",
                        input_schema=self.descriptor_schema(),
                    ),
                ),
                resources=(),
            )
        }
        context = self.context()
        catalog = self.catalog(
            context=context,
            server_names=("search_server_00", "search_server_01"),
        )

        result = await self.expander(
            self.loader(self.provider(clients)),
            limits=CapabilityExpansionLimits(max_servers=1),
        ).expand(catalog=catalog, context=context, request=self.request())

        expanded = result.capabilities[0].entry
        card_ref = self.ref_for(catalog, "search_server_01")
        assert expanded.stable_name == "search_server_01"
        assert expanded.capability_ref != card_ref
        assert {
            capability.entry.capability_ref for capability in result.capabilities
        }.isdisjoint({entry.capability_ref for entry in catalog.entries})


class TestTwoTierSearch(ExpansionMixin):
    """Tier two is the only tier that can answer at capability granularity.

    A catalog holds only MCP *server* cards, so a tier-one candidate names a
    connector rather than something the model can invoke.  The predecessor gate
    suppressed tier two when tier one returned enough ``TOOL_CARD`` candidates;
    tool cards are no longer catalog members, so that count is structurally zero
    and the gate is gone rather than left counting an empty set.
    """

    def search(
        self,
        expander: BoundedCapabilityExpander,
    ) -> TwoTierCapabilitySearch:
        return TwoTierCapabilitySearch(expander=expander)

    async def test_a_rich_first_tier_still_expands(self) -> None:
        """The strongest possible tier one is still only server cards."""

        names = tuple(f"search_server_{index:02d}" for index in range(3))
        clients = {name: self.client_for(name) for name in names}
        provider = self.provider(clients)
        context = self.context()
        catalog = self.catalog(context=context, server_names=names)

        result = await self.search(self.expander(self.loader(provider))).search(
            catalog=catalog, context=context, request=self.request(limit=10)
        )

        assert len(catalog.entries) == 3
        assert provider.created_clients == list(names)
        assert result.expansion.expanded_count == 3
        assert [
            candidate.stable_name
            for candidate in result.search.candidates
            if candidate.stable_name.endswith("_tool_00")
        ] == [f"{name}_tool_00" for name in names]

    async def test_expansion_is_deterministic_across_identical_searches(self) -> None:
        clients = {
            "search_server_00": self.client_for("search_server_00", tool_count=3)
        }
        context = self.context()
        catalog = self.catalog(context=context, server_names=("search_server_00",))
        search = self.search(self.expander(self.loader(self.provider(clients))))

        first = await search.search(
            catalog=catalog, context=context, request=self.request(limit=10)
        )
        second = await search.search(
            catalog=catalog, context=context, request=self.request(limit=10)
        )

        assert first == second

    async def test_a_filter_excluding_servers_still_opens_nothing(self) -> None:
        """The one remaining way to suppress tier two is the caller's own filter."""

        clients = {"search_server_00": self.client_for("search_server_00")}
        provider = self.provider(clients)
        context = self.context()
        catalog = self.catalog(context=context, server_names=("search_server_00",))

        result = await self.search(self.expander(self.loader(provider))).search(
            catalog=catalog,
            context=context,
            request=self.request(
                filters=CapabilitySearchFilters(
                    sources={CapabilitySource.TOOL_CARD},
                ),
            ),
        )

        assert provider.created_clients == []
        assert result.expansion.admitted_count == 0
        assert result.search.candidates == ()

    async def test_thin_first_tier_merges_expanded_capabilities(self) -> None:
        clients = {
            "search_server_00": self.client_for("search_server_00", tool_count=3)
        }
        provider = self.provider(clients)
        context = self.context()
        catalog = self.catalog(context=context, server_names=("search_server_00",))

        result = await self.search(self.expander(self.loader(provider))).search(
            catalog=catalog, context=context, request=self.request(limit=10)
        )

        names = [candidate.stable_name for candidate in result.search.candidates]
        assert provider.created_clients == ["search_server_00"]
        assert result.expansion.expanded_count == 1
        assert names == [
            "search_server_00",
            "search_server_00_tool_00",
            "search_server_00_tool_01",
            "search_server_00_tool_02",
        ]

    async def test_merged_answer_is_bounded_to_the_requested_limit(self) -> None:
        clients = {
            "search_server_00": self.client_for("search_server_00", tool_count=40)
        }
        context = self.context()
        catalog = self.catalog(context=context, server_names=("search_server_00",))

        result = await self.search(
            self.expander(self.loader(self.provider(clients)))
        ).search(
            catalog=catalog,
            context=context,
            request=self.request(limit=5),
        )

        assert len(result.search.candidates) == 5
        assert result.search.catalog_id == catalog.revision.catalog_id
        assert result.search.query_digest.startswith("sha256:")

    async def test_failed_expansion_leaves_the_first_tier_answer_intact(self) -> None:
        clients = {
            "search_server_00": self.client_for(
                "search_server_00",
                connect_error=ConnectionError("upstream down"),
            ),
            "search_server_01": self.client_for(
                "search_server_01",
                connect_error=ConnectionError("upstream down"),
            ),
        }
        context = self.context()
        catalog = self.catalog(
            context=context,
            server_names=("search_server_00", "search_server_01"),
        )

        result = await self.search(
            self.expander(self.loader(self.provider(clients)))
        ).search(catalog=catalog, context=context, request=self.request())

        names = [candidate.stable_name for candidate in result.search.candidates]
        assert result.expansion.expanded_count == 0
        assert result.expansion.capabilities == ()
        assert names == ["search_server_00", "search_server_01"]
