"""Unit tests for MCP-as-files: config/tool persistence + file-backed provider.

Covers the DoD invariants:

* server configs persist and reload as files (round-trip),
* no tokens ever reach disk — a Fernet/KMS-shaped canary is refused and never
  appears in any written file,
* the file-backed provider exposes a persisted server's tools to the loader
  (fake-client end-to-end),
* the store rebuilds cleanly from an authoritative config set.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.capabilities.mcp import (
    DynamicMcpRegistry,
    McpLoadRequest,
    McpLoader,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.capabilities.mcp.files import (
    FileMcpConfigStore,
    FileMcpServerProvider,
    McpConfigSecretLeak,
    McpServerConfigFile,
    McpToolMetadataFile,
    SecretShapeScanner,
)

from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin


# A realistic Fernet ciphertext prefix — the exact shape a leaked vault token
# would take. The scanner must refuse it and it must never land on disk.
_SECRET_CANARY = "gAAAAABmZm9vYmFyc3VwZXJzZWNyZXR0b2tlbnZhbHVl"


class FilesMixin(DynamicMcpLoadingMixin):
    """Builders for the file-store tests."""

    def make_config(
        self,
        *,
        name: str = "drive_mcp",
        endpoint: str = "https://drivemcp.example.com/mcp/v1",
        health: McpServerHealth = McpServerHealth.HEALTHY,
        required_scopes: tuple[str, ...] = (),
    ) -> McpServerConfigFile:
        return McpServerConfigFile(
            name=name,
            server_id=f"desktop:{name}",
            display_name="Drive MCP",
            short_description="Search Drive through MCP.",
            endpoint=endpoint,
            transport=McpTransport.HTTP,
            health=health,
            required_scopes=required_scopes,
            allowed_tools=("drive_search",),
        )

    def make_metadata(
        self, *, description: str = "Search Drive."
    ) -> McpToolMetadataFile:
        return McpToolMetadataFile(
            name="drive_search",
            description=description,
            input_schema=self.object_query_schema(),
            output_shape=self.object_answer_schema(),
        )

    def build_context(self) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id=self.TestValues.Ids.USER_123,
            org_id=self.TestValues.Ids.ORG_456,
            roles={self.TestValues.Roles.EMPLOYEE},
            permission_scopes={self.TestValues.Scopes.DOCS_READ},
            model_profile=ModelConfig(
                provider="fake",
                model_name="fake",
                max_input_tokens=128_000,
                timeout_seconds=30,
                temperature=0,
            ),
            trace_id="trace_files",
            feature_flags={self.TestValues.FeatureFlags.DYNAMIC_MCP_LOADING},
        )


class TestConfigRoundTrip(FilesMixin):
    def test_write_then_read_config_round_trips(self, tmp_path: Path) -> None:
        store = FileMcpConfigStore(root=tmp_path)
        config = self.make_config()

        store.write_server(config, (self.make_metadata(),))
        loaded = store.read_server(config.name)

        assert loaded == config
        assert store.list_configs() == (config,)

    def test_tools_persist_as_json_and_markdown(self, tmp_path: Path) -> None:
        store = FileMcpConfigStore(root=tmp_path)
        config = self.make_config()

        store.write_server(config, (self.make_metadata(),))

        layout = store.layout
        assert layout.config_path(config.name).is_file()
        assert layout.tool_json_path(config.name, "drive_search").is_file()
        markdown = layout.tool_markdown_path(config.name, "drive_search")
        assert markdown.is_file()
        assert "# drive_search" in markdown.read_text(encoding="utf-8")
        assert store.read_tools(config.name)[0].name == "drive_search"

    def test_rebuild_from_configs_reconstructs_store(self, tmp_path: Path) -> None:
        store = FileMcpConfigStore(root=tmp_path)
        configs = (
            self.make_config(name="alpha_mcp"),
            self.make_config(name="beta_mcp"),
        )

        written = store.rebuild_from_configs(configs)

        assert written == ("alpha_mcp", "beta_mcp")
        assert {c.name for c in store.list_configs()} == {"alpha_mcp", "beta_mcp"}

    def test_delete_server_removes_config_and_tools(self, tmp_path: Path) -> None:
        store = FileMcpConfigStore(root=tmp_path)
        config = self.make_config()
        store.write_server(config, (self.make_metadata(),))

        assert store.delete_server(config.name) is True
        assert store.list_configs() == ()
        assert store.read_tools(config.name) == ()


class TestSecretCanary(FilesMixin):
    def test_config_contract_forbids_secret_key(self) -> None:
        with pytest.raises(ValueError):
            McpServerConfigFile(
                name="leaky_mcp",
                endpoint="https://x.example.com/mcp",
                access_token="nope",  # type: ignore[call-arg]
            )

    def test_scanner_rejects_ciphertext_shaped_value(self) -> None:
        with pytest.raises(McpConfigSecretLeak):
            SecretShapeScanner.assert_clean(
                {"description": _SECRET_CANARY}, where="test"
            )

    def test_write_rejects_token_shaped_tool_description(self, tmp_path: Path) -> None:
        store = FileMcpConfigStore(root=tmp_path)
        config = self.make_config()
        poisoned = self.make_metadata(description=f"leaked {_SECRET_CANARY} token")

        with pytest.raises(McpConfigSecretLeak):
            store.write_server(config, (poisoned,))

    def test_no_written_file_contains_the_canary(self, tmp_path: Path) -> None:
        store = FileMcpConfigStore(root=tmp_path)
        # A perfectly ordinary server: its tokens live in the vault, not here.
        store.write_server(self.make_config(), (self.make_metadata(),))

        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert _SECRET_CANARY not in path.read_text(encoding="utf-8")
                assert "gAAAAA" not in path.read_text(encoding="utf-8")


class TestFileProviderLoadsTools(FilesMixin):
    def test_file_provider_lists_persisted_cards(self, tmp_path: Path) -> None:
        store = FileMcpConfigStore(root=tmp_path)
        store.write_server(self.make_config(), (self.make_metadata(),))
        provider = FileMcpServerProvider(
            store=store,
            client_factory=self.FakeMcpProvider(cards=(), clients={}),
        )

        cards = asyncio.run(provider.list_server_cards())

        assert [card.name for card in cards] == ["drive_mcp"]
        assert cards[0].server_id == "desktop:drive_mcp"

    def test_connecting_persisted_server_exposes_its_tools(
        self, tmp_path: Path
    ) -> None:
        """Writing a config file makes the server loadable and its tools visible."""

        store = FileMcpConfigStore(root=tmp_path)
        store.write_server(self.make_config(), (self.make_metadata(),))
        client = self.FakeMcpClient(
            tools=(self.make_tool(name="drive_search"),),
            resources=(self.make_resource(),),
        )
        provider = FileMcpServerProvider(
            store=store,
            client_factory=self.FakeMcpProvider(
                cards=(), clients={"drive_mcp": client}
            ),
        )
        loader = McpLoader(DynamicMcpRegistry(providers=(provider,)))

        result = asyncio.run(
            loader.load_server(
                McpLoadRequest(
                    server_name="drive_mcp",
                    runtime_context=self.build_context(),
                )
            )
        )

        assert result.succeeded
        assert [tool.name for tool in result.loaded_server.tools] == ["drive_search"]

    def test_disabled_config_is_not_loadable(self, tmp_path: Path) -> None:
        store = FileMcpConfigStore(root=tmp_path)
        config = self.make_config(health=McpServerHealth.DISABLED)
        store.write_server(config, ())
        provider = FileMcpServerProvider(
            store=store,
            client_factory=self.FakeMcpProvider(cards=(), clients={}),
        )
        loader = McpLoader(DynamicMcpRegistry(providers=(provider,)))

        result = asyncio.run(
            loader.load_server(
                McpLoadRequest(
                    server_name="drive_mcp",
                    runtime_context=self.build_context(),
                )
            )
        )

        assert not result.succeeded
