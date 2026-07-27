"""The identity hop that lets a slug-keyed host connect a server-keyed gate.

The in-chat consent card learns a ``server_id`` and nothing else. That is
enough for the web, whose connect is server-keyed, but not for the desktop:
there the whole OAuth path is slug-keyed on purpose, because the backend
*reconstructs* the loopback redirect from a validated port rather than
accepting a redirect URI from the client. So a desktop gate had no way to
start a connect, and its Connect button was inert.

``mcp_servers.connector_slug`` has existed since PR #387 — the runtime just
could not see it. This pins the hop end to end, plus the one distinction that
is easy to lose: ``connector_slug`` (which catalog connector this server IS)
is NOT ``catalog_slug`` (this connector is not installed yet). The client keys
its "never suggest this again" mute on the latter, so merging them would let a
gate for a connector the user deliberately installed be muted away.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.capabilities.mcp.middleware.auth_mcp import (
    AuthMcpTool,
    McpAuthSession,
)
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_123",
        org_id="org_456",
        roles={"employee"},
        permission_scopes={"docs:read"},
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-4o-mini",
            max_input_tokens=4096,
            timeout_seconds=30,
            temperature=0.0,
        ),
        run_id="run_abcdef",
        trace_id="trace_slug",
    )


class _StubSessionCreator:
    """Returns a fixed session; records what it was asked for."""

    def __init__(self, session: McpAuthSession) -> None:
        self._session = session
        self.requested_server_id: str | None = None

    async def create_auth_session(
        self, *, server_id: str, runtime_context: object
    ) -> McpAuthSession:
        self.requested_server_id = server_id
        return self._session


def _session(**overrides: Any) -> McpAuthSession:
    base: dict[str, Any] = {
        "server_id": "seed:linear",
        "server_name": "linear",
        "display_name": "Linear",
        "auth_url": "https://linear.app/oauth/authorize?state=abc",
        "expires_at": datetime(2026, 7, 27, tzinfo=UTC),
    }
    base.update(overrides)
    return McpAuthSession(**base)


async def _gate_payload(session: McpAuthSession, runtime_context: object) -> dict:
    """Drive the tool far enough to capture the interrupt payload."""

    captured: dict[str, Any] = {}

    def capture(payload: dict[str, Any]) -> object:
        captured.update(payload)
        # Shape the tool's `_resume_result` expects for a skipped gate; the
        # resume path is not what this file is about.
        return {"ok": False}

    tool = AuthMcpTool(
        auth_session_creator=_StubSessionCreator(session),
        runtime_context=runtime_context,
        interrupt_handler=capture,
    )
    await tool.ainvoke({"server_name": "linear", "server_id": "seed:linear"})
    return captured


class TestTheCardCarriesTheCatalogIdentity:
    def test_a_catalog_server_reports_its_slug(self) -> None:
        card = McpServerCard(
            name="linear",
            server_id="seed:linear",
            short_description="Linear issues",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            health=McpServerHealth.HEALTHY,
            load_cost=1,
            connector_slug="linear",
        )
        assert card.connector_slug == "linear"

    def test_a_custom_server_has_none(self) -> None:
        """A server the user pasted a URL for has no catalog identity, and the
        desktop connect flow correctly cannot start one."""

        card = McpServerCard(
            name="my-internal-tool",
            server_id="custom:abc123",
            short_description="An internal MCP server",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            health=McpServerHealth.HEALTHY,
            load_cost=1,
        )
        assert card.connector_slug is None


@pytest.mark.asyncio
class TestTheGatePayload:
    async def test_names_the_connector_so_a_slug_keyed_host_can_connect(
        self,
    ) -> None:
        payload = await _gate_payload(
            _session(connector_slug="linear"), runtime_context=_context()
        )
        assert payload["server_id"] == "seed:linear"
        assert payload["connector_slug"] == "linear"

    async def test_is_none_for_a_custom_server(self) -> None:
        """Honest absence rather than a guessed slug. The desktop card then
        stays inert for that gate, which is the truth — its OAuth path is
        driven by the profile catalog and a custom server is not in it."""

        payload = await _gate_payload(_session(), runtime_context=_context())
        assert payload["connector_slug"] is None

    async def test_never_carries_catalog_slug(self) -> None:
        """The distinction the mute depends on.

        ``catalog_slug`` means "not installed yet" and is stamped only by the
        discovery card. A gate is by definition a server the user HAS, so the
        gate payload must not carry it — otherwise denying a gate would mute a
        connector the user deliberately installed.
        """

        payload = await _gate_payload(
            _session(connector_slug="linear"), runtime_context=_context()
        )
        assert "catalog_slug" not in payload
