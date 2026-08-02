"""The catalog's composition seam: one store, written by the loader, read at ``/mcp/``.

A mechanism nobody mounts is a dead feature, so these assert the wiring itself —
that the route lands on the composed Deep Agents backend, that it joins an
existing composite instead of replacing it, and that every run which composed
without a catalog before still composes exactly the same way.
"""

from __future__ import annotations

from collections.abc import Sequence

from deepagents.backends.composite import CompositeBackend

from agent_runtime.capabilities.mcp.catalog import McpCatalogBuilder, McpCatalogStore
from agent_runtime.capabilities.mcp.catalog_backend import McpCatalogBackend
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from agent_runtime.capabilities.skills.sources import SkillSourceConfig
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeDependencies
from agent_runtime.execution.factory import (
    _mcp_catalog_store,
    _with_mcp_catalog_route,
    acreate_agent_runtime,
)

from tests.unit.agent_runtime.capabilities.mcp.test_catalog_backend import (
    CatalogBackendMixin,
    EmptyDefaultBackend,
)
from tests.unit.fakes import (
    FakeMemoryBackendFactory,
    FakeSubagentCatalog,
    FakeToolRegistry,
)


class FakeMcpRegistry:
    """A registry exposing the MCP seam the loader tool is gated on."""

    async def resolve_server(self, server_name: str) -> None:
        del server_name


class FakeDeepAgentsMemoryBackend(EmptyDefaultBackend):
    """A memory backend the builder passes straight through when uncomposed."""

    memory_paths: Sequence[str] = ("/memories/",)

    def download_files(self, paths: list[str]) -> dict[str, str]:
        del paths
        return {}

    def upload_files(self, files: dict[str, str]) -> None:  # type: ignore[override]
        del files

    async def adownload_files(self, paths: list[str]) -> dict[str, str]:
        del paths
        return {}

    async def aupload_files(self, files: dict[str, str]) -> None:  # type: ignore[override]
        del files


class CapturingBuilder:
    """Records the ``DeepAgentBuildRequest`` instead of compiling a graph."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def __call__(self, request: object) -> object:
        self.calls.append(request)
        return {"agent": "fake"}


class CatalogWiringMixin(CatalogBackendMixin):
    """Composition inputs for the ``/mcp/`` route."""

    class Routes:
        DRAFTS = "/drafts/"

    def existing_composite(self) -> CompositeBackend:
        return CompositeBackend(
            default=EmptyDefaultBackend(),
            routes={self.Routes.DRAFTS: EmptyDefaultBackend()},
        )

    def live_dependencies(self) -> RuntimeDependencies:
        """Dependencies whose MCP registry exposes the real discovery seam."""

        provider = self.FakeMcpProvider(
            cards=(self.make_card(name=self.TestValues.Names.DRIVE_MCP),),
            clients={
                self.TestValues.Names.DRIVE_MCP: self.FakeMcpClient(
                    tools=(self.make_tool(name=self.TestValues.Names.DRIVE_SEARCH),),
                    resources=(),
                )
            },
        )
        return RuntimeDependencies(
            tool_registry=FakeToolRegistry(),
            mcp_registry=DynamicMcpRegistry(providers=(provider,)),
            skill_source_config=SkillSourceConfig(roots=("skills",)),
            memory_backend_factory=FakeMemoryBackendFactory(),
            subagent_catalog=FakeSubagentCatalog(),
        )

    def named_tool(self, tools: Sequence[object], name: str) -> object:
        for tool in tools:
            if str(getattr(tool, "name", "")) == name:
                return tool
        raise AssertionError(f"no {name} tool on the model surface")


class TestCatalogStoreGate(CatalogWiringMixin):
    def test_a_run_with_an_mcp_seam_gets_a_store(self) -> None:
        assert isinstance(_mcp_catalog_store(FakeMcpRegistry()), McpCatalogStore)

    def test_a_run_without_an_mcp_seam_gets_none(self) -> None:
        # No loader tool, so no catalog: the model never sees an empty /mcp/.
        assert _mcp_catalog_store(object()) is None


class TestCatalogRouteComposition(CatalogWiringMixin):
    def test_route_joins_an_existing_composite_without_disturbing_it(self) -> None:
        composite = self.existing_composite()
        store = McpCatalogStore()

        composed, mounted = _with_mcp_catalog_route(
            composite, catalog=store, memory_backend=None
        )

        # The mount is what the load tool is keyed on, so it must be returned.
        assert mounted is store
        assert isinstance(composed, CompositeBackend)
        assert composed.default is composite.default
        assert (
            composed.routes[self.Routes.DRAFTS] is composite.routes[self.Routes.DRAFTS]
        )
        assert isinstance(
            composed.routes[McpCatalogBackend.PATH_PREFIX], McpCatalogBackend
        )

    def test_route_is_composed_when_no_backend_existed(self) -> None:
        store = McpCatalogStore()

        composed, mounted = _with_mcp_catalog_route(
            None, catalog=store, memory_backend=None
        )

        assert mounted is store
        assert isinstance(composed, CompositeBackend)
        assert set(composed.routes) == {McpCatalogBackend.PATH_PREFIX}

    def test_no_catalog_leaves_composition_untouched(self) -> None:
        composite = self.existing_composite()

        assert _with_mcp_catalog_route(
            composite, catalog=None, memory_backend=None
        ) == (composite, None)
        assert _with_mcp_catalog_route(None, catalog=None, memory_backend=None) == (
            None,
            None,
        )

    def test_a_passthrough_memory_backend_keeps_its_exact_composition(self) -> None:
        memory_backend = FakeDeepAgentsMemoryBackend()

        composed, mounted = _with_mcp_catalog_route(
            None, catalog=McpCatalogStore(), memory_backend=memory_backend
        )

        # Wrapping this backend would drop the ``memory_paths`` attribute the
        # builder reads off it — a working memory surface is not worth trading
        # for a browsable one.
        assert composed is None
        # And because nothing is mounted, the load tool must NOT be handed the
        # store: publishing into a store no route reads would return a pointer
        # to a file that does not exist, losing the descriptors outright.
        assert mounted is None


class TestCatalogRouteServesPublishedFiles(CatalogWiringMixin):
    def test_a_published_catalog_is_readable_through_the_composed_backend(
        self,
    ) -> None:
        store = _mcp_catalog_store(FakeMcpRegistry())
        assert store is not None
        composed, _mounted = _with_mcp_catalog_route(
            None, catalog=store, memory_backend=None
        )
        assert isinstance(composed, CompositeBackend)

        # The loader writes into this store; the mounted route reads the same
        # object, which is the whole write surface.
        store.publish(McpCatalogBuilder.build(self.make_loaded()))

        listing = composed.ls("/mcp/linear/tools")

        assert len(listing.entries or []) == self.CatalogValues.LARGE_TOOL_COUNT


class TestCatalogReachesTheBuiltAgent(CatalogWiringMixin):
    async def test_built_runtime_mounts_the_catalog_route(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        builder = CapturingBuilder()

        await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=self.live_dependencies(),
            agent_builder=builder,
        )

        backend = getattr(builder.calls[0], "memory_backend", None)
        assert isinstance(backend, CompositeBackend)
        assert isinstance(
            backend.routes[McpCatalogBackend.PATH_PREFIX], McpCatalogBackend
        )

    async def test_loading_a_server_publishes_files_the_agent_can_list(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        builder = CapturingBuilder()
        await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=self.live_dependencies(),
            agent_builder=builder,
        )
        request = builder.calls[0]
        backend = getattr(request, "memory_backend")
        load_tool = self.named_tool(getattr(request, "tools"), "load_mcp_server")

        result = await load_tool.ainvoke(
            {
                "args": {"server_name": self.TestValues.Names.DRIVE_MCP},
                "name": "load_mcp_server",
                "type": "tool_call",
                "id": "call_catalog_1",
            }
        )

        # The store the tool writes to and the route the model reads from are
        # the same object — that is the entire write surface.
        rendered = str(getattr(result, "content", result))
        assert "loaded_server" not in rendered
        assert f"/mcp/{self.TestValues.Names.DRIVE_MCP}/SERVER.md" in rendered
        listing = backend.ls(f"/mcp/{self.TestValues.Names.DRIVE_MCP}/tools")
        assert [entry["path"] for entry in (listing.entries or [])] == [
            f"/mcp/{self.TestValues.Names.DRIVE_MCP}/tools/"
            f"{self.TestValues.Names.DRIVE_SEARCH}.json"
        ]
