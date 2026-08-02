"""Unit tests for the P2-1 credential-plane contracts + connector resolver.

Both modules are additive and unwired (nothing in the running app imports them
yet), so these tests pin the two properties later phases depend on:

* the contracts are frozen / ``extra="forbid"``, and a :class:`MintedToken`'s
  plaintext never renders into a ``repr`` / ``str`` / JSON dump (the secret-safety
  invariant the desktop broker read and the web mint both rely on);
* :class:`ToolConnectorResolver` reproduces
  ``McpDispatcherUnwrap.effective_server_name`` semantics in the per-tool world —
  a known tool name resolves to its connector, anything else resolves to ``None``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from agent_runtime.capabilities.mcp.cards import McpTransport
from agent_runtime.capabilities.mcp.connection import (
    McpServerConnectionConfig,
    MintedToken,
)
from agent_runtime.capabilities.mcp.connector_resolver import ToolConnectorResolver

_SECRET = "lin_oauth_super-secret-value"


class ConnectionFixtureMixin:
    """Builders shared by the contract test classes."""

    def _config(self, **overrides: object) -> McpServerConnectionConfig:
        payload: dict[str, object] = {
            "url": "https://mcp.linear.app/mcp",
            "transport": McpTransport.HTTP,
        }
        payload.update(overrides)
        return McpServerConnectionConfig(**payload)  # type: ignore[arg-type]

    def _token(self) -> MintedToken:
        return MintedToken(
            value=SecretStr(_SECRET),
            expires_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
        )


class TestMcpServerConnectionConfig(ConnectionFixtureMixin):
    def test_defaults_to_no_headers(self) -> None:
        config = self._config()
        assert config.headers == {}
        assert config.transport is McpTransport.HTTP

    def test_is_frozen(self) -> None:
        config = self._config()
        with pytest.raises(ValidationError):
            config.url = "https://evil.example/mcp"  # type: ignore[misc]

    def test_forbids_unknown_fields(self) -> None:
        # extra="forbid": a stray key is a contract violation, never absorbed.
        with pytest.raises(ValidationError):
            self._config(access_token="should-not-be-here")

    def test_rejects_an_unknown_transport(self) -> None:
        with pytest.raises(ValidationError):
            self._config(transport="carrier-pigeon")


class TestMintedTokenSecrecy(ConnectionFixtureMixin):
    def test_is_frozen_and_forbids_unknown_fields(self) -> None:
        token = self._token()
        with pytest.raises(ValidationError):
            token.expires_at = datetime(2030, 1, 1, tzinfo=UTC)  # type: ignore[misc]
        with pytest.raises(ValidationError):
            MintedToken(
                value=SecretStr(_SECRET),
                expires_at=datetime(2026, 8, 2, tzinfo=UTC),
                scope="extra",  # type: ignore[call-arg]
            )

    @pytest.mark.parametrize("render", [repr, str])
    def test_plaintext_never_renders(self, render) -> None:  # noqa: ANN001
        # The whole point of the contract: no repr/str path may leak the bearer.
        assert _SECRET not in render(self._token())

    def test_plaintext_never_reaches_a_structured_log_dump(self) -> None:
        token = self._token()
        assert _SECRET not in token.model_dump_json()
        assert _SECRET not in str(token.model_dump())

    def test_plaintext_is_recoverable_only_explicitly(self) -> None:
        assert self._token().value.get_secret_value() == _SECRET


class TestToolConnectorResolver:
    def _resolver(self) -> ToolConnectorResolver:
        return ToolConnectorResolver(
            {"linear.create_issue": "linear", "github.get_issue": "github"}
        )

    def test_known_tool_resolves_to_its_connector(self) -> None:
        resolver = self._resolver()
        assert resolver.server_for("linear.create_issue") == "linear"
        assert resolver.server_for("github.get_issue") == "github"

    @pytest.mark.parametrize("name", ["", "   ", "write_todos", "call_mcp_tool"])
    def test_unknown_or_blank_resolves_to_none(self, name: str) -> None:
        # Mirrors McpDispatcherUnwrap returning None for a non-MCP payload: the
        # caller treats it as a native, connector-less step.
        assert self._resolver().server_for(name) is None

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        assert self._resolver().server_for("  linear.create_issue  ") == "linear"

    def test_blank_entries_are_pruned_on_ingest(self) -> None:
        resolver = ToolConnectorResolver(
            {"": "linear", "   ": "linear", "ok.tool": "", "good.tool": "linear"}
        )
        assert resolver.tool_names == frozenset({"good.tool"})
        assert resolver.server_for("ok.tool") is None

    def test_registration_snapshot_is_immutable(self) -> None:
        # A later edit to the source mapping must not retroactively change a
        # published resolver — the registration snapshot is the whole truth.
        source = {"linear.create_issue": "linear"}
        resolver = ToolConnectorResolver(source)
        source["injected.tool"] = "attacker"
        assert resolver.server_for("injected.tool") is None
        assert resolver.tool_names == frozenset({"linear.create_issue"})
