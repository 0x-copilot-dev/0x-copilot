"""MCP-as-files — persist MCP server configs and tool metadata on a file store.

An MCP server registration is persisted as an inspectable, editable pair of
files on the runtime's file store:

    mcp/<server>.json                   — the server config (endpoint,
                                          transport, auth mode/state, tool
                                          allowlist). No tokens, ever.
    mcp/<server>/tools/<tool>.json      — one machine-readable tool descriptor.
    mcp/<server>/tools/<tool>.md        — the same descriptor as human-readable
                                          markdown for review/editing.

The loader path reads server cards from these config files through
:class:`FileMcpServerProvider`, which plugs into the existing
:class:`~agent_runtime.capabilities.mcp.registry.DynamicMcpRegistry`. Because
the config files carry no credentials, they can be committed, diffed, and
hand-edited; the actual access/refresh tokens stay in the backend
``TokenVault`` and only ever reach the wire through the backend RPC proxy.

Secret safety is enforced two ways:

1. :class:`McpServerConfigFile` / :class:`McpToolMetadataFile` are strict
   (``extra="forbid"``) contracts with **no** credential fields, so a token
   cannot be represented on disk by construction.
2. :meth:`FileMcpConfigStore.write_server` runs every serialized payload
   through :class:`SecretShapeScanner` before it touches the filesystem and
   raises :class:`McpConfigSecretLeak` if anything secret-shaped (a Fernet
   ciphertext, a ``kms_v1:`` envelope, or a credential-shaped key) appears —
   e.g. a malicious server descriptor trying to smuggle a token through a
   tool description.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpAuthState,
    McpRiskLevel,
    McpServerCard,
    McpServerHealth,
    McpToolDescriptor,
    McpTransport,
    McpValueNormalizer,
)
from agent_runtime.capabilities.mcp.client import McpClient, McpClientFactory


class Keys:
    """Stable file-layout and payload keys for the MCP file store."""

    class Dir:
        ROOT = "mcp"
        TOOLS = "tools"

    class Ext:
        JSON = ".json"
        MARKDOWN = ".md"

    class Field:
        TOOLS = "tools"


class Values:
    """Stable string values for the MCP file store."""

    class Scan:
        # Fernet ciphertexts always start with these bytes (base64 of the
        # 0x80 version byte + timestamp); the managed vault prefixes its
        # envelope with ``kms_v1:``. Either shape on disk means a token leaked.
        FERNET_PREFIX = "gAAAAA"
        KMS_PREFIX = "kms_v1:"

    # Substrings that mark a mapping key as credential-bearing. A config or
    # descriptor that carries any of these keys is rejected before write.
    SECRET_KEY_MARKERS: frozenset[str] = frozenset(
        {
            "access_token",
            "refresh_token",
            "client_secret",
            "api_key",
            "apikey",
            "private_key",
            "authorization",
            "password",
            "passwd",
            "secret",
            "bearer",
            "credential",
        }
    )


class McpFilesError(Exception):
    """Base error for the MCP file store."""


class McpConfigSecretLeak(McpFilesError):
    """Raised when a config/descriptor about to be written looks secret-shaped."""


class SecretShapeScanner:
    """Rejects payloads that carry credential-shaped keys or ciphertext values.

    Structural safety (no token fields on the contracts) is the primary
    control; this scanner is defense-in-depth for free-form content — tool
    descriptions, schemas, and any hand-edited config — that a hostile MCP
    server could try to use to smuggle a secret onto disk.
    """

    @classmethod
    def assert_clean(cls, payload: object, *, where: str) -> None:
        """Raise :class:`McpConfigSecretLeak` if ``payload`` is secret-shaped."""

        for key, value in cls._walk(payload):
            if key is not None and cls._is_secret_key(key):
                raise McpConfigSecretLeak(
                    f"credential-shaped key {key!r} is not allowed in {where}"
                )
            if isinstance(value, str) and cls._is_secret_value(value):
                raise McpConfigSecretLeak(
                    f"ciphertext-shaped value is not allowed in {where}"
                )

    @classmethod
    def _walk(cls, payload: object, key: str | None = None):
        """Yield ``(key, value)`` for every scalar reachable in ``payload``."""

        if isinstance(payload, dict):
            for child_key, child_value in payload.items():
                yield from cls._walk(child_value, str(child_key))
            return
        if isinstance(payload, (list, tuple)):
            for item in payload:
                yield from cls._walk(item, key)
            return
        yield key, payload

    @classmethod
    def _is_secret_key(cls, key: str) -> bool:
        lowered = key.lower()
        return any(marker in lowered for marker in Values.SECRET_KEY_MARKERS)

    @classmethod
    def _is_secret_value(cls, value: str) -> bool:
        # Match anywhere in the string: a hostile server can embed a token
        # mid-sentence in a tool description, not only as the whole value.
        return Values.Scan.FERNET_PREFIX in value or Values.Scan.KMS_PREFIX in value


class McpToolMetadataFile(RuntimeContract):
    """One tool descriptor as persisted next to a server config.

    Carries only public tool contract metadata — never arguments captured
    from a live call and never credentials.
    """

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_shape: dict[str, Any] = Field(default_factory=dict)
    risk_level: McpRiskLevel = McpRiskLevel.MEDIUM
    product_scope: str = "read"

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        return McpValueNormalizer.normalize_slug(value, "name")

    @classmethod
    def from_descriptor(cls, descriptor: McpToolDescriptor) -> "McpToolMetadataFile":
        """Project a live :class:`McpToolDescriptor` into its on-disk shape."""

        return cls(
            name=descriptor.name,
            description=descriptor.description,
            input_schema=dict(descriptor.input_schema),
            output_shape=dict(descriptor.output_shape),
            risk_level=descriptor.risk_level,
        )

    def to_markdown(self) -> str:
        """Render a human-readable review copy of this tool descriptor."""

        schema = json.dumps(self.input_schema, indent=2, sort_keys=True)
        return (
            f"# {self.name}\n\n"
            f"{self.description}\n\n"
            f"- **Risk:** {self.risk_level.value}\n"
            f"- **Product scope:** {self.product_scope}\n\n"
            "## Input schema\n\n"
            f"```json\n{schema}\n```\n"
        )


class McpServerConfigFile(RuntimeContract):
    """The persisted, inspectable config for one MCP server.

    Contains everything the loader needs to build a compact
    :class:`McpServerCard` — and nothing a token could hide in. The strict
    ``extra="forbid"`` base makes it impossible to attach a credential field.
    """

    name: str
    server_id: str | None = None
    display_name: str | None = None
    short_description: str = "MCP server."
    endpoint: str
    transport: McpTransport = McpTransport.HTTP
    auth_mode: McpAuthMode = McpAuthMode.OAUTH2
    auth_state: McpAuthState = McpAuthState.AUTHENTICATED
    health: McpServerHealth = McpServerHealth.HEALTHY
    enabled: bool = True
    load_cost: int = Field(default=1, ge=1, le=100_000)
    required_scopes: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        return McpValueNormalizer.normalize_slug(value, "name")

    @model_validator(mode="after")
    def _forbid_secret_shapes(self) -> "McpServerConfigFile":
        SecretShapeScanner.assert_clean(
            self.model_dump(mode="json"), where=f"mcp config {self.name!r}"
        )
        return self

    def to_card(self) -> McpServerCard:
        """Build the compact server card the registry exposes to the loader."""

        return McpServerCard(
            name=self.name,
            server_id=self.server_id,
            display_name=self.display_name,
            short_description=self.short_description,
            transport=self.transport,
            auth_mode=self.auth_mode,
            auth_state=self.auth_state,
            required_scopes=frozenset(self.required_scopes),
            health=self.health,
            load_cost=self.load_cost,
            enabled=self.enabled,
        )

    @classmethod
    def from_card(cls, card: McpServerCard, *, endpoint: str) -> "McpServerConfigFile":
        """Project a compact server card into its on-disk config shape."""

        return cls(
            name=card.name,
            server_id=card.server_id,
            display_name=card.display_name,
            short_description=card.short_description,
            endpoint=endpoint,
            transport=card.transport,
            auth_mode=card.auth_mode,
            auth_state=card.auth_state,
            health=card.health,
            enabled=card.enabled,
            load_cost=card.load_cost,
            required_scopes=tuple(sorted(card.required_scopes)),
        )


class McpFileLayout:
    """Resolves the on-disk paths for a server's config and tool metadata."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def mcp_dir(self) -> Path:
        return self._root / Keys.Dir.ROOT

    def config_path(self, name: str) -> Path:
        return self.mcp_dir / f"{name}{Keys.Ext.JSON}"

    def tools_dir(self, name: str) -> Path:
        return self.mcp_dir / name / Keys.Dir.TOOLS

    def tool_json_path(self, name: str, tool_name: str) -> Path:
        return self.tools_dir(name) / f"{tool_name}{Keys.Ext.JSON}"

    def tool_markdown_path(self, name: str, tool_name: str) -> Path:
        return self.tools_dir(name) / f"{tool_name}{Keys.Ext.MARKDOWN}"

    def config_paths(self) -> tuple[Path, ...]:
        if not self.mcp_dir.is_dir():
            return ()
        return tuple(sorted(self.mcp_dir.glob(f"*{Keys.Ext.JSON}")))


@dataclass(frozen=True)
class FileMcpConfigStore:
    """Reads and writes MCP server configs + tool metadata on a file store.

    ``root`` is the runtime file-store root; all MCP files live under
    ``root/mcp``. Instances are cheap — construct one per file-store root.
    """

    root: Path

    @property
    def layout(self) -> McpFileLayout:
        return McpFileLayout(self.root)

    def write_server(
        self,
        config: McpServerConfigFile,
        tools: Sequence[McpToolMetadataFile] = (),
    ) -> None:
        """Persist ``config`` and ``tools`` as files, refusing secret-shaped data.

        The config's own validator already scans it; tool metadata is scanned
        here because descriptions/schemas come from an untrusted server.
        """

        layout = self.layout
        config_payload = config.model_dump(mode="json")
        SecretShapeScanner.assert_clean(
            config_payload, where=f"mcp config {config.name!r}"
        )
        tool_payloads = [tool.model_dump(mode="json") for tool in tools]
        for tool, payload in zip(tools, tool_payloads, strict=True):
            SecretShapeScanner.assert_clean(
                payload, where=f"mcp tool {config.name}/{tool.name}"
            )
            SecretShapeScanner.assert_clean(
                tool.to_markdown(), where=f"mcp tool markdown {config.name}/{tool.name}"
            )

        layout.mcp_dir.mkdir(parents=True, exist_ok=True)
        self._replace_tools_dir(layout.tools_dir(config.name))
        layout.config_path(config.name).write_text(
            json.dumps(config_payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        if tools:
            layout.tools_dir(config.name).mkdir(parents=True, exist_ok=True)
        for tool, payload in zip(tools, tool_payloads, strict=True):
            layout.tool_json_path(config.name, tool.name).write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
            layout.tool_markdown_path(config.name, tool.name).write_text(
                tool.to_markdown(), encoding="utf-8"
            )

    def read_server(self, name: str) -> McpServerConfigFile:
        """Load one server config from disk (re-validates on read)."""

        path = self.layout.config_path(name)
        if not path.is_file():
            raise McpFilesError(f"no MCP config file for server {name!r}")
        return McpServerConfigFile.model_validate_json(path.read_text(encoding="utf-8"))

    def read_tools(self, name: str) -> tuple[McpToolMetadataFile, ...]:
        """Load a server's tool descriptors from disk in stable name order."""

        tools_dir = self.layout.tools_dir(name)
        if not tools_dir.is_dir():
            return ()
        tools = [
            McpToolMetadataFile.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(tools_dir.glob(f"*{Keys.Ext.JSON}"))
        ]
        return tuple(tools)

    def list_configs(self) -> tuple[McpServerConfigFile, ...]:
        """Load every persisted server config, sorted by server name."""

        configs = [
            McpServerConfigFile.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.layout.config_paths()
        ]
        return tuple(sorted(configs, key=lambda config: config.name))

    def delete_server(self, name: str) -> bool:
        """Remove a server's config + tool files. Returns ``True`` if present."""

        import shutil

        layout = self.layout
        config_path = layout.config_path(name)
        existed = config_path.is_file()
        config_path.unlink(missing_ok=True)
        server_dir = layout.mcp_dir / name
        if server_dir.is_dir():
            shutil.rmtree(server_dir)
        return existed

    def rebuild_from_configs(
        self, configs: Iterable[McpServerConfigFile]
    ) -> tuple[str, ...]:
        """Rebuild the file store from a set of configs (no tool metadata).

        Used to reconstruct the on-disk view from an authoritative source
        (e.g. the backend registry) after a wipe. Returns the server names
        written, sorted.
        """

        written: list[str] = []
        for config in configs:
            self.write_server(config)
            written.append(config.name)
        return tuple(sorted(written))

    @staticmethod
    def _replace_tools_dir(tools_dir: Path) -> None:
        """Clear a server's tools dir so a rewrite never leaves stale files."""

        import shutil

        if tools_dir.exists():
            shutil.rmtree(tools_dir)


@dataclass(frozen=True)
class FileMcpServerProvider:
    """`McpServerProvider` that sources cards from the file config store.

    Client creation is delegated to ``client_factory`` — in production the
    backend token-proxy provider, in tests a fake. This keeps the token path
    unchanged (credentials never touch these files) while making the set of
    servers the agent sees a pure function of what is on disk.
    """

    store: FileMcpConfigStore
    client_factory: McpClientFactory

    async def list_server_cards(self) -> tuple[McpServerCard, ...]:
        """Return compact cards for every enabled config on the file store."""

        return tuple(config.to_card() for config in self.store.list_configs())

    def create_client(self, card: McpServerCard) -> McpClient:
        """Delegate client creation to the injected factory."""

        return self.client_factory.create_client(card)


__all__ = [
    "FileMcpConfigStore",
    "FileMcpServerProvider",
    "McpConfigSecretLeak",
    "McpFileLayout",
    "McpFilesError",
    "McpServerConfigFile",
    "McpToolMetadataFile",
    "SecretShapeScanner",
]
