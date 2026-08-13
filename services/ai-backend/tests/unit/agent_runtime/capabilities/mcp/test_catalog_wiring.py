"""The catalog's composition seam: one store, seeded at run start, read at ``/mcp/``.

A mechanism nobody mounts is a dead feature, so these assert the wiring itself —
that the route lands on the composed Deep Agents backend, that it joins an
existing composite instead of replacing it, and that every run which composed
without a catalog before still composes exactly the same way.

The eager-seed cases are the ones that guard the live failure. The
``load_mcp_server`` description advertises ``/mcp/<server>/`` in the tool schema,
so a model reads it and probes ``ls /mcp`` BEFORE calling anything. Nothing had
published yet, the listing came back empty AND successful, and the model
concluded the connector had no browsable filesystem and stopped. So: ``ls /mcp``
must list the connected servers on a freshly composed runtime, with no load
call anywhere in the test.
"""

from __future__ import annotations

from collections.abc import Sequence

from deepagents.backends.composite import CompositeBackend

from agent_runtime.capabilities.mcp.catalog import (
    McpCatalogBuilder,
    McpCatalogStore,
    McpCatalogTier,
    Messages,
)
from agent_runtime.capabilities.mcp.catalog_backend import McpCatalogBackend
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from agent_runtime.capabilities.skills.sources import SkillSourceConfig
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeDependencies
from agent_runtime.execution.factory import (
    _mcp_catalog_store,
    _seed_mcp_catalog,
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

    def test_an_injected_store_replaces_the_in_process_one(self) -> None:
        # Exactly one catalog per run. The desktop's durable store is not a
        # cache in front of an in-memory copy; it IS the store.
        injected = McpCatalogStore()

        assert _mcp_catalog_store(FakeMcpRegistry(), injected) is injected

    def test_an_unusable_injected_store_falls_back_rather_than_failing(
        self, caplog
    ) -> None:
        # A store that cannot serve the seam would raise at the model's first
        # ``ls``. Losing durability is the right trade; losing the run is not.
        with caplog.at_level("WARNING"):
            store = _mcp_catalog_store(FakeMcpRegistry(), object())

        assert isinstance(store, McpCatalogStore)
        assert "mcp_catalog.injected_store_unusable" in caplog.text

    def test_no_mcp_seam_logs_the_reason(self, caplog) -> None:
        with caplog.at_level("INFO"):
            _mcp_catalog_store(object())

        assert "mcp_catalog.not_mounted reason=no_mcp_seam" in caplog.text

    def test_the_in_process_fallback_says_it_is_not_durable(self, caplog) -> None:
        with caplog.at_level("INFO"):
            _mcp_catalog_store(FakeMcpRegistry())

        assert "mcp_catalog.store=memory durable=false" in caplog.text


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

    def test_the_mount_says_where_it_landed(self, caplog) -> None:
        with caplog.at_level("INFO"):
            _with_mcp_catalog_route(
                self.existing_composite(),
                catalog=McpCatalogStore(),
                memory_backend=None,
            )

        assert "mcp_catalog.mounted path=/mcp/ composite=True" in caplog.text

    def test_the_decline_says_why(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            _with_mcp_catalog_route(
                None,
                catalog=McpCatalogStore(),
                memory_backend=FakeDeepAgentsMemoryBackend(),
            )

        # "The model said /mcp was empty" and "no route was ever mounted" are
        # indistinguishable from a transcript. They must not be from a log.
        assert (
            "mcp_catalog.not_mounted reason=passthrough_memory_backend" in caplog.text
        )


class TestEagerSeeding(CatalogWiringMixin):
    def test_seeding_lists_every_authorized_card(self) -> None:
        store = McpCatalogStore()

        _seed_mcp_catalog(
            store,
            (
                self.make_catalog_card(name=self.CatalogValues.SERVER),
                self.make_catalog_card(name="slack_mcp"),
            ),
        )

        assert store.server_names() == ("linear", "slack_mcp")

    def test_a_seeded_server_is_browsable_without_any_load(self) -> None:
        store = McpCatalogStore()

        _seed_mcp_catalog(
            store, (self.make_catalog_card(name=self.CatalogValues.SERVER),)
        )

        listing = McpCatalogBackend(store).ls("/")
        assert self.entry_paths(listing) == ["/linear/"]
        assert listing.error is None

    def test_seeding_never_overwrites_a_loaded_server(self) -> None:
        store = McpCatalogStore()
        store.publish(McpCatalogBuilder.build(self.make_loaded()))

        _seed_mcp_catalog(
            store, (self.make_catalog_card(name=self.CatalogValues.SERVER),)
        )

        # Turn 2 of the same chat must not un-load turn 1's tool list.
        listing = McpCatalogBackend(store).ls("/linear/tools")
        assert len(listing.entries or []) == self.CatalogValues.LARGE_TOOL_COUNT

    def test_a_seed_catalog_declares_no_tools_directory(self) -> None:
        catalog = McpCatalogBuilder.seed(
            self.make_catalog_card(name=self.CatalogValues.SERVER)
        )

        assert catalog.tier is McpCatalogTier.SEED
        assert catalog.directories == ()
        assert Messages.Seed.TOOLS_UNKNOWN in catalog.server_markdown.content

    def test_seeding_a_run_with_no_catalog_is_a_no_op(self) -> None:
        _seed_mcp_catalog(None, (self.make_catalog_card(),))

    def test_a_non_card_listing_is_reported_not_swallowed(self, caplog) -> None:
        store = McpCatalogStore()

        with caplog.at_level("WARNING"):
            _seed_mcp_catalog(store, (self.make_catalog_card(name="linear"), object()))

        assert store.server_names() == ("linear",)
        assert "mcp_catalog.seed_skipped_non_cards count=1" in caplog.text


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
    async def test_ls_mcp_lists_connected_servers_before_any_load(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # THE regression test. No ``load_mcp_server`` call anywhere below: this
        # is the state the model finds on its first turn of a fresh chat, and
        # an empty listing here is the whole live failure.
        builder = CapturingBuilder()

        await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=self.live_dependencies(),
            agent_builder=builder,
        )

        backend = getattr(builder.calls[0], "memory_backend")
        listing = backend.ls("/mcp")
        assert listing.error is None
        assert self.entry_paths(listing) == [f"/mcp/{self.TestValues.Names.DRIVE_MCP}/"]

    async def test_the_seeded_server_markdown_says_how_to_get_the_tools(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        builder = CapturingBuilder()
        await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=self.live_dependencies(),
            agent_builder=builder,
        )
        backend = getattr(builder.calls[0], "memory_backend")

        result = backend.read(f"/mcp/{self.TestValues.Names.DRIVE_MCP}/SERVER.md")

        assert result.error is None
        content = (result.file_data or {}).get("content", "")
        assert "load_mcp_server" in content

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
        # The file is stemmed by the tool's REGISTERED name, so the one string
        # the model reads out of the catalog is also the one it can call. The
        # directory keeps the card's own name; the namespace carries its slug.
        assert [entry["path"] for entry in (listing.entries or [])] == [
            f"/mcp/{self.TestValues.Names.DRIVE_MCP}/tools/"
            f"{self.invoke_name(self.TestValues.Names.DRIVE_SEARCH, server=self.TestValues.Names.DRIVE_MCP)}.json"
        ]
