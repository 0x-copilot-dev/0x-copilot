"""Unit tests for the MCP filesystem catalog (P0 progressive disclosure).

The failure these guard against is concrete: a real Linear descriptor arrived as
70,465 bytes / 52 tools / zero newlines, was offloaded to a blob whose preview,
offsets and grep all dead-ended, and the run finished with EMPTY SUCCESS. So the
assertions here are about reachability and size, not shape alone:

* a 52-tool fake connector produces the expected tree, one file per tool,
* the always-loaded ``SERVER.md`` stays inside its budget and still names every
  tool,
* every emitted artifact is line-oriented (a single-line file is the exact
  defect that made the blob unreadable),
* the payload the model receives after a load is a pointer, not descriptors,
* one tool's full contract is recoverable by reading exactly one file,
* nothing provider-specific and nothing credential-shaped reaches ``/mcp/**``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence

import pytest

from agent_runtime.capabilities.mcp.cards import (
    LoadedMcpServer,
    McpAuthMode,
    McpAuthState,
    McpConnectionMetadata,
    McpResourceDescriptor,
    McpServerCard,
    McpServerHealth,
    McpToolDescriptor,
    McpTransport,
)
from agent_runtime.capabilities.mcp.catalog import (
    Limits,
    McpCatalogBuilder,
    McpCatalogPaths,
    McpCatalogSecretLeak,
    McpCatalogStore,
    McpToolActionClass,
)
from agent_runtime.capabilities.mcp.descriptor_source import (
    McpCapabilityDescriptorSource,
)
from agent_runtime.capabilities.mcp.middleware.dynamic_loader import LoadMcpServerTool
from agent_runtime.capabilities.mcp.tool_naming import McpToolName
from agent_runtime.capabilities.surfaces.builtin import server_slug
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig

from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin


class McpCatalogMixin(DynamicMcpLoadingMixin):
    """Builders for a realistically large connector and its catalog."""

    class CatalogValues:
        SERVER = "linear"
        ENDPOINT = "https://linear.example.com/mcp/v1"
        # A Fernet ciphertext prefix — the exact shape a leaked vault token
        # takes. It must never be publishable into a model-visible file.
        SECRET_CANARY = "gAAAAABmZm9vYmFyc3VwZXJzZWNyZXR0b2tlbg"
        # Curated action-catalog entries for `linear`, so the index rows are
        # the PDP's own classification rather than a guess.
        READ_TOOL = "list_issues"
        WRITE_TOOL = "create_issue"
        LARGE_TOOL_COUNT = 52

    def make_catalog_card(
        self,
        *,
        name: str = CatalogValues.SERVER,
        display_name: str | None = "Linear",
        short_description: str = "Linear issue tracking through MCP.",
    ) -> McpServerCard:
        return McpServerCard(
            name=name,
            server_id=f"srv_{name}",
            display_name=display_name,
            short_description=short_description,
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            auth_state=McpAuthState.AUTHENTICATED,
            health=McpServerHealth.HEALTHY,
            load_cost=1,
        )

    def make_catalog_tool(
        self,
        name: str,
        *,
        description: str | None = None,
        read_only: bool | None = None,
    ) -> McpToolDescriptor:
        return McpToolDescriptor(
            name=name,
            description=description
            or (
                f"Tool {name} that does a moderately verbose thing with issues, "
                "projects, cycles and teams inside the workspace."
            ),
            input_schema=self.object_query_schema(),
            output_shape=self.object_answer_schema(),
            read_only=read_only,
        )

    def make_loaded(
        self,
        *,
        card: McpServerCard | None = None,
        tools: Sequence[McpToolDescriptor] | None = None,
        resources: Sequence[McpResourceDescriptor] = (),
    ) -> LoadedMcpServer:
        resolved_card = card or self.make_catalog_card()
        return LoadedMcpServer(
            server_card=resolved_card,
            tools=tuple(tools if tools is not None else self.make_many_tools()),
            resources=tuple(resources),
            connection_metadata=McpConnectionMetadata(
                server_name=resolved_card.name,
                transport=resolved_card.transport,
                auth_mode=resolved_card.auth_mode,
            ),
        )

    def make_many_tools(
        self, count: int = CatalogValues.LARGE_TOOL_COUNT
    ) -> tuple[McpToolDescriptor, ...]:
        return tuple(
            self.make_catalog_tool(f"linear_tool_{index:02d}") for index in range(count)
        )

    def build_catalog_context(self, *, provider: str = "openai") -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id=self.TestValues.Ids.USER_123,
            org_id=self.TestValues.Ids.ORG_456,
            roles={self.TestValues.Roles.EMPLOYEE},
            permission_scopes={self.TestValues.Scopes.DOCS_READ},
            model_profile=ModelConfig(
                provider=provider,
                model_name=f"{provider}-model",
                max_input_tokens=128_000,
                timeout_seconds=30,
                temperature=0,
            ),
            trace_id="trace_catalog",
            feature_flags={self.TestValues.FeatureFlags.DYNAMIC_MCP_LOADING},
        )

    def load_through_tool(
        self,
        *,
        catalog: McpCatalogStore | None,
        context: AgentRuntimeContext,
        tools: Sequence[McpToolDescriptor] | None = None,
    ) -> Mapping[str, object]:
        loader = self.make_loader(
            self.FakeMcpClient(
                tools=tuple(tools if tools is not None else self.make_many_tools()),
                resources=(),
            )
        )
        tool = LoadMcpServerTool(
            loader=loader,
            runtime_context=context,
            catalog=catalog,
        )
        return asyncio.run(
            tool.ainvoke({"server_name": self.TestValues.Names.DRIVE_MCP})
        )

    def tool_paths(self, catalog_files: Mapping[str, str]) -> set[str]:
        prefix = McpCatalogPaths.tools_dir(self.CatalogValues.SERVER)
        return {path for path in catalog_files if path.startswith(prefix)}

    def invoke_name(self, tool_name: str, *, server: str | None = None) -> str:
        """The registered, callable name for ``tool_name`` on ``server``.

        The catalog is the model's only instruction on what to call, so every
        name it renders is this one — ``McpToolSource`` registers connector
        tools namespaced, and a catalog still advertising the connector's bare
        name would name a tool the model surface does not have.
        """

        return McpToolName.compose(
            server=server_slug(server or self.CatalogValues.SERVER), tool=tool_name
        )

    def tool_file_path(self, tool_name: str, *, server: str | None = None) -> str:
        """The catalog path for ``tool_name`` — stemmed by its callable name."""

        resolved = server or self.CatalogValues.SERVER
        return McpCatalogPaths.tool_file(
            resolved, self.invoke_name(tool_name, server=resolved)
        )


class TestCatalogTree(McpCatalogMixin):
    def test_fifty_two_tool_server_produces_one_file_per_tool(self) -> None:
        catalog = McpCatalogBuilder.build(self.make_loaded())
        files = catalog.as_mapping()

        assert len(files) == self.CatalogValues.LARGE_TOOL_COUNT + 1
        assert McpCatalogPaths.server_markdown(self.CatalogValues.SERVER) in files
        assert self.tool_paths(files) == {
            self.tool_file_path(tool.name) for tool in self.make_many_tools()
        }

    def test_every_artifact_is_line_oriented(self) -> None:
        catalog = McpCatalogBuilder.build(self.make_loaded())

        for file in catalog.files:
            assert file.content.endswith("\n"), file.path
            # deepagents slices reads by SOURCE LINE; a single-line file is
            # unreadable in exactly the way the 70 KB blob was.
            assert file.content.count("\n") >= Limits.MIN_NEWLINES_PER_FILE, file.path

    def test_resources_directory_exists_even_when_empty(self) -> None:
        catalog = McpCatalogBuilder.build(self.make_loaded(resources=()))

        assert McpCatalogPaths.resources_dir(self.CatalogValues.SERVER) in (
            catalog.directories
        )
        assert McpCatalogPaths.tools_dir(self.CatalogValues.SERVER) in (
            catalog.directories
        )

    def test_resources_become_one_file_each_with_safe_names(self) -> None:
        resource = self.make_resource(name="Drive Root / Index")
        catalog = McpCatalogBuilder.build(
            self.make_loaded(tools=(), resources=(resource,))
        )
        files = catalog.as_mapping()
        resource_paths = [
            path
            for path in files
            if path.startswith(McpCatalogPaths.resources_dir(self.CatalogValues.SERVER))
        ]

        assert resource_paths == [
            McpCatalogPaths.resource_file(self.CatalogValues.SERVER, "drive-root-index")
        ]
        payload = json.loads(files[resource_paths[0]])
        assert payload["name"] == "Drive Root / Index"
        assert payload["uri"] == self.TestValues.Uris.HTTPS_ROOT

    def test_empty_tool_list_says_so_instead_of_rendering_nothing(self) -> None:
        catalog = McpCatalogBuilder.build(self.make_loaded(tools=()))

        assert "published no tools" in catalog.server_markdown.content


class TestAlwaysLoadedTier(McpCatalogMixin):
    def test_server_markdown_for_52_tools_stays_within_budget(self) -> None:
        catalog = McpCatalogBuilder.build(self.make_loaded())

        assert catalog.server_markdown.byte_size <= Limits.SERVER_MARKDOWN_MAX_BYTES, (
            catalog.server_markdown.byte_size
        )

    def test_index_names_every_tool(self) -> None:
        catalog = McpCatalogBuilder.build(self.make_loaded())
        content = catalog.server_markdown.content

        for tool in self.make_many_tools():
            assert f"`{self.invoke_name(tool.name)}`" in content

    def test_index_never_drops_a_tool_name_to_meet_the_budget(self) -> None:
        many = tuple(
            self.make_catalog_tool(f"linear_tool_{index:03d}") for index in range(300)
        )
        catalog = McpCatalogBuilder.build(self.make_loaded(tools=many))
        content = catalog.server_markdown.content

        for tool in many:
            assert f"`{self.invoke_name(tool.name)}`" in content

    def test_index_names_the_tool_the_model_can_actually_call(self) -> None:
        # The index is the shortlist a model dispatches straight from, and
        # ``DISPATCH_GUIDANCE`` tells it to call the tool by the name it sees
        # here. ``McpToolSource`` registers connector tools namespaced, so the
        # connector's bare name names nothing on the model surface — printing it
        # would repeat the retired-``call_mcp_tool`` failure with a new string.
        catalog = McpCatalogBuilder.build(
            self.make_loaded(tools=(self.make_catalog_tool("list_issues"),))
        )
        content = catalog.server_markdown.content

        assert "`mcp__linear__list_issues` [read]" in content
        assert "`list_issues` [read]" not in content

    def test_index_action_class_is_the_policy_derivation(self) -> None:
        tools = (
            self.make_catalog_tool(self.CatalogValues.READ_TOOL),
            self.make_catalog_tool(self.CatalogValues.WRITE_TOOL),
        )
        catalog = McpCatalogBuilder.build(self.make_loaded(tools=tools))
        content = catalog.server_markdown.content

        assert f"`{self.invoke_name(self.CatalogValues.READ_TOOL)}` [read]" in content
        assert f"`{self.invoke_name(self.CatalogValues.WRITE_TOOL)}` [write]" in content
        for tool in tools:
            assert McpToolActionClass.for_tool(
                server=self.CatalogValues.SERVER, tool=tool.name
            ).value == (
                McpCapabilityDescriptorSource.action_for(
                    server=self.CatalogValues.SERVER, tool=tool.name
                ).value
            )

    def test_unclassified_tool_is_labelled_write_not_read(self) -> None:
        # Fail-closed: an un-catalogued, un-annotated tool must never be
        # advertised as a read the gateway would wave through.
        catalog = McpCatalogBuilder.build(
            self.make_loaded(tools=(self.make_catalog_tool("mystery_tool"),))
        )

        assert (
            f"`{self.invoke_name('mystery_tool')}` [write]"
            in catalog.server_markdown.content
        )

    def test_index_tells_the_model_how_to_reach_a_tool(self) -> None:
        catalog = McpCatalogBuilder.build(self.make_loaded())
        content = catalog.server_markdown.content

        assert McpCatalogPaths.tools_dir(self.CatalogValues.SERVER) in content
        # Names the way to run a tool WITHOUT naming a tool that no longer
        # exists. Pinning the absence too: a stale pointer here is invisible
        # in every unit test and only shows up as an empty answer in a live run.
        assert "call the tool by its own name" in content
        assert "call_mcp_tool" not in content


class TestToolFiles(McpCatalogMixin):
    def test_one_tool_is_fully_recoverable_from_one_file(self) -> None:
        descriptor = self.make_catalog_tool(self.CatalogValues.READ_TOOL)
        catalog = McpCatalogBuilder.build(self.make_loaded(tools=(descriptor,)))
        path = self.tool_file_path(self.CatalogValues.READ_TOOL)

        payload = json.loads(catalog.as_mapping()[path])

        assert payload["name"] == self.invoke_name(descriptor.name)
        assert payload["input_schema"] == dict(descriptor.input_schema)
        assert payload["output_shape"] == dict(descriptor.output_shape)
        assert payload["action"] == McpToolActionClass.READ.value
        # The tool IS its registered name; `server_name` remains for provenance,
        # not as an argument to fill in.
        assert payload["invoke_with"] == {
            "tool": self.invoke_name(descriptor.name),
            "server_name": self.CatalogValues.SERVER,
        }

    def test_tool_file_points_at_the_tool_itself(self) -> None:
        catalog = McpCatalogBuilder.build(
            self.make_loaded(tools=(self.make_catalog_tool("create_issue"),))
        )
        path = self.tool_file_path("create_issue")

        payload = json.loads(catalog.as_mapping()[path])

        # Discovery still grants nothing — the POLICY stage carries the PDP
        # decision and the approval interrupt either way. What changed is the
        # ADDRESS: per-tool registration puts each connector tool on the model
        # surface under its own name, so pointing at the retired umbrella sends
        # the model to a tool that does not exist. A live run proved the cost:
        # the model read all 52 descriptors, called `call_mcp_tool`, and
        # returned a successful-looking nothing. The address is now the
        # NAMESPACED registered name, for the same reason: that is the only
        # string the model surface answers to.
        assert payload["invoke_with"]["tool"] == "mcp__linear__create_issue"
        assert payload["invoke_with"]["server_name"] == self.CatalogValues.SERVER
        assert payload["action"] == McpToolActionClass.WRITE.value


class TestCatalogSafety(McpCatalogMixin):
    def test_ciphertext_shaped_description_is_refused(self) -> None:
        poisoned = self.make_catalog_tool(
            "poisoned_tool",
            description=f"Totally normal tool. {self.CatalogValues.SECRET_CANARY} ok?",
        )

        with pytest.raises(McpCatalogSecretLeak):
            McpCatalogBuilder.build(self.make_loaded(tools=(poisoned,)))

    def test_catalog_carries_no_connection_material(self) -> None:
        catalog = McpCatalogBuilder.build(self.make_loaded())
        blob = "\n".join(file.content for file in catalog.files)

        # The catalog is projected from contracts that have no endpoint, header
        # or token field, so connection material cannot appear by construction.
        assert self.CatalogValues.ENDPOINT not in blob
        assert "authorization" not in blob.lower()
        assert "access_token" not in blob


class TestCatalogStore(McpCatalogMixin):
    def test_publish_exposes_every_artifact(self) -> None:
        store = McpCatalogStore()

        store.publish(McpCatalogBuilder.build(self.make_loaded()))

        assert len(store.snapshot()) == self.CatalogValues.LARGE_TOOL_COUNT + 1
        assert store.server_names() == (self.CatalogValues.SERVER,)
        assert store.pointer(self.CatalogValues.SERVER) is not None

    def test_reload_replaces_only_that_server(self) -> None:
        store = McpCatalogStore()
        store.publish(McpCatalogBuilder.build(self.make_loaded()))
        other = self.make_catalog_card(name="slack_mcp", display_name="Slack")
        store.publish(
            McpCatalogBuilder.build(
                self.make_loaded(card=other, tools=(self.make_catalog_tool("post"),))
            )
        )

        store.publish(
            McpCatalogBuilder.build(
                self.make_loaded(tools=(self.make_catalog_tool("only_one"),))
            )
        )

        paths = set(store.snapshot())
        assert self.tool_file_path("only_one") in paths
        assert not any(
            path.startswith(McpCatalogPaths.tools_dir(self.CatalogValues.SERVER))
            and path.endswith("linear_tool_00.json")
            for path in paths
        )
        assert self.tool_file_path("post", server="slack_mcp") in paths

    def test_snapshot_taken_before_a_publish_is_not_mutated(self) -> None:
        store = McpCatalogStore()
        store.publish(McpCatalogBuilder.build(self.make_loaded()))
        before = store.snapshot()

        store.publish(
            McpCatalogBuilder.build(
                self.make_loaded(
                    card=self.make_catalog_card(name="slack_mcp"),
                    tools=(self.make_catalog_tool("post"),),
                )
            )
        )

        assert len(before) == self.CatalogValues.LARGE_TOOL_COUNT + 1


class TestLoadReturnsAPointer(McpCatalogMixin):
    def test_agent_visible_result_is_a_pointer_not_descriptors(self) -> None:
        store = McpCatalogStore()

        result = self.load_through_tool(
            catalog=store, context=self.build_catalog_context()
        )

        assert "loaded_server" not in result
        assert result["server_name"] == self.TestValues.Names.DRIVE_MCP
        assert result["tool_count"] == self.CatalogValues.LARGE_TOOL_COUNT
        assert result["server_md"] == McpCatalogPaths.server_markdown(
            self.TestValues.Names.DRIVE_MCP
        )

    def test_agent_visible_result_is_small(self) -> None:
        store = McpCatalogStore()

        result = self.load_through_tool(
            catalog=store, context=self.build_catalog_context()
        )

        encoded = json.dumps(result)
        # The live failure offloaded a 70,465-byte descriptor and previewed
        # 2,000 characters of it. The whole result must now fit inside what the
        # preview alone used to consume.
        assert len(encoded) < 2_000, len(encoded)
        assert "input_schema" not in encoded

    def test_pointer_tells_the_model_where_to_look_next(self) -> None:
        store = McpCatalogStore()

        result = self.load_through_tool(
            catalog=store, context=self.build_catalog_context()
        )

        next_steps = str(result["next_steps"])
        assert result["server_md"] in next_steps
        assert "grep" in next_steps
        assert "call the tool by its own name" in next_steps
        assert "call_mcp_tool" not in next_steps

    def test_load_publishes_the_browsable_tree(self) -> None:
        store = McpCatalogStore()

        self.load_through_tool(catalog=store, context=self.build_catalog_context())

        snapshot = store.snapshot()
        assert len(snapshot) == self.CatalogValues.LARGE_TOOL_COUNT + 1
        assert (
            McpCatalogPaths.server_markdown(self.TestValues.Names.DRIVE_MCP) in snapshot
        )

    def test_without_a_catalog_the_legacy_payload_is_unchanged(self) -> None:
        result = self.load_through_tool(
            catalog=None, context=self.build_catalog_context()
        )

        assert (
            result["loaded_server"]["server_card"]["name"]
            == self.TestValues.Names.DRIVE_MCP
        )

    def test_a_catalogless_load_says_so_instead_of_falling_back_silently(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # This branch is a SUCCESSFUL load that writes no files. On disk it is
        # indistinguishable from "the model never called load_mcp_server", and
        # telling those two apart is the whole diagnostic question when a live
        # user reports an empty `/mcp/<server>/tools/`. It must not be silent.
        with caplog.at_level(logging.WARNING):
            self.load_through_tool(catalog=None, context=self.build_catalog_context())

        assert any("mcp_load.no_catalog" in record.message for record in caplog.records)

    def test_a_load_records_that_it_was_requested_and_whether_a_catalog_existed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The other half of the same question: absent this line, the model never
        # asked, which is a prompt problem rather than a wiring one.
        with caplog.at_level(logging.INFO):
            self.load_through_tool(
                catalog=McpCatalogStore(), context=self.build_catalog_context()
            )

        assert any(
            "mcp_load.requested" in record.message and "catalog=True" in record.message
            for record in caplog.records
        )

    def test_catalog_is_identical_across_model_providers(self) -> None:
        openai_store = McpCatalogStore()
        anthropic_store = McpCatalogStore()

        openai_result = self.load_through_tool(
            catalog=openai_store, context=self.build_catalog_context(provider="openai")
        )
        anthropic_result = self.load_through_tool(
            catalog=anthropic_store,
            context=self.build_catalog_context(provider="anthropic"),
        )

        # No provider branch may exist on the catalog path: the discovery
        # mechanism is a filesystem, which every provider already has.
        assert dict(openai_store.snapshot()) == dict(anthropic_store.snapshot())
        assert openai_result == anthropic_result

    def test_a_failed_load_still_returns_its_typed_error(self) -> None:
        store = McpCatalogStore()
        loader = self.make_loader(self.FakeMcpClient(tools=(), resources=()))
        tool = LoadMcpServerTool(
            loader=loader,
            runtime_context=self.build_catalog_context(),
            catalog=store,
        )

        result = asyncio.run(tool.ainvoke({"server_name": "unknown_server"}))

        assert result["error"]["code"] == "unknown_server"
        assert store.snapshot() == {}

    def test_a_poisoned_descriptor_refuses_rather_than_falling_back(self) -> None:
        store = McpCatalogStore()

        result = self.load_through_tool(
            catalog=store,
            context=self.build_catalog_context(),
            tools=(
                self.make_catalog_tool(
                    "poisoned_tool",
                    description=f"see {self.CatalogValues.SECRET_CANARY}",
                ),
            ),
        )

        # Falling back to the raw payload would restore the unreachable blob
        # AND carry the very content the scanner objected to.
        assert result["error"]["code"] == "malformed_descriptor"
        assert self.CatalogValues.SECRET_CANARY not in json.dumps(result)
        assert store.snapshot() == {}
