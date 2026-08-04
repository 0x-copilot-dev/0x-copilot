"""One malformed MCP card must not take down every run in the product.

The defect this pins, observed on a packaged desktop build: the user connected
Gmail, and from that moment **every message in every conversation failed** —
including conversations that used no MCP tool at all. The screen said only

    "Step failed. 0xCopilot couldn't complete this step."

and offered to start the run again, which failed identically. The log carried
``error_class: AgentRuntimeError`` and an outer traceback; the actual reason was
nowhere in it.

The cause was a chain of three separate faults, one per test class below.

1. ``desktop_profiles.yaml`` declares Gmail's OAuth scopes as Google publishes
   them — ``https://www.googleapis.com/auth/gmail.readonly`` — and the backend
   copies them verbatim onto the server card. The runtime validated
   ``required_scopes`` against a slug grammar modelled on our internal
   ``docs:read`` form, which cannot represent a URI. The card was rejected.
   (``BackendMcpProvider`` then *discards* required_scopes anyway, so the value
   that failed validation was one nothing would have read.)

2. That rejection was fatal, and its blast radius was the whole product.
   ``acreate_agent_runtime`` gathers five registries — tools, MCP, subagents,
   skill directories, skill cards — before the model is contacted, and
   ``asyncio.gather`` propagates the first exception. No harness meant no run,
   for every conversation, connector-using or not.

3. ``raise ... from exc`` dropped the cause: the worker logs the error class and
   the outer frames, so the ValidationError naming the field never reached the
   log. Diagnosing it required re-issuing the backend call by hand.

So these tests assert the three properties that keep it from recurring: a
provider-owned scope is representable, one bad card costs exactly that card, and
a skip says which card and which field.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import logging

import httpx
import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.mcp.backend_provider import BackendMcpProvider
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.capabilities.mcp.constants import Messages
from agent_runtime.capabilities.mcp.registry import (
    DynamicMcpRegistry,
    RawMcpServerCard,
)
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    ModelConfig,
    RuntimeDependencies,
    RuntimeErrorCode,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import acreate_agent_runtime

from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder

_REGISTRY_LOGGER = "agent_runtime.capabilities.mcp.registry"
# The exact scope that took the product down. Not a synthesised "weird string":
# it is what `desktop_profiles.yaml` declares and what Google publishes.
_GOOGLE_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class McpCardFixtureMixin:
    """Card payloads and fake providers shared by the classes below."""

    GOOD_NAME = "linear"
    BAD_NAME = "gmail"

    @staticmethod
    def context() -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id="user_123",
            org_id="org_456",
            roles={"employee"},
            model_profile=ModelConfig(
                provider="openai",
                model_name="gpt-4o-mini",
                max_input_tokens=4096,
                timeout_seconds=30,
                temperature=0.0,
            ),
            trace_id="trace_card_rejection",
        )

    @classmethod
    def raw_card(cls, name: str, **overrides: object) -> dict[str, object]:
        """A backend-shaped card payload, as `/internal/v1/mcp/cards` returns it."""
        payload: dict[str, object] = {
            "server_id": f"seed:{name}",
            "name": name,
            "display_name": name.title(),
            "short_description": f"{name.title()} through MCP.",
            "transport": "http",
            "auth_mode": "oauth2",
            "health": "healthy",
            "load_cost": 1,
            "enabled": True,
        }
        payload.update(overrides)
        return payload

    @classmethod
    def unparseable_card(cls) -> dict[str, object]:
        """A card no widening of the scope grammar can rescue.

        ``load_cost`` is a ``PositiveInt``; zero is invalid whatever else
        changes. Using a permanently-invalid field keeps these tests about
        ISOLATION rather than about which values happen to validate today.
        """
        return cls.raw_card(cls.BAD_NAME, load_cost=0)

    @staticmethod
    def typed_card(name: str) -> McpServerCard:
        return McpServerCard(
            name=name,
            server_id=f"seed:{name}",
            short_description=f"{name.title()} through MCP.",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            health=McpServerHealth.HEALTHY,
            load_cost=1,
        )

    @staticmethod
    def registry_over(cards: Sequence[RawMcpServerCard]) -> DynamicMcpRegistry:
        return DynamicMcpRegistry(providers=(FakeCardProvider(cards=tuple(cards)),))


@dataclass
class FakeCardProvider:
    """Provider returning scripted raw cards without validating them.

    Mirrors a provider that hands the registry whatever the backend said —
    which is what makes the registry the validation site under test.
    """

    cards: tuple[RawMcpServerCard, ...]
    created_for: list[str] = field(default_factory=list)

    async def list_server_cards(self) -> Sequence[RawMcpServerCard]:
        return self.cards

    def create_client(self, card: McpServerCard) -> object:
        self.created_for.append(card.name)
        return object()


@dataclass
class ExplodingCardProvider:
    """Provider that cannot answer at all — the backend being unreachable."""

    error: Exception

    async def list_server_cards(self) -> Sequence[RawMcpServerCard]:
        raise self.error

    def create_client(self, card: McpServerCard) -> object:
        return object()


class TestProviderOwnedScopesAreRepresentable(McpCardFixtureMixin):
    """Fault 1 — the scope grammar could not express an OAuth scope URI."""

    def test_google_oauth_scope_url_is_a_valid_required_scope(self) -> None:
        card = McpServerCard.model_validate(
            self.raw_card(self.BAD_NAME, required_scopes=[_GOOGLE_READONLY_SCOPE])
        )

        assert card.required_scopes == frozenset({_GOOGLE_READONLY_SCOPE})

    @pytest.mark.parametrize(
        "scope",
        [
            "read:jira-work",  # Atlassian, and our own internal form
            "https://www.googleapis.com/auth/drive.file",
            "https://graph.microsoft.com/Mail.Read",
        ],
    )
    def test_real_provider_scopes_are_accepted(self, scope: str) -> None:
        card = McpServerCard.model_validate(
            self.raw_card(self.GOOD_NAME, required_scopes=[scope])
        )

        assert card.required_scopes == frozenset({scope.lower()})

    @pytest.mark.parametrize(
        "scope",
        [
            "docs:read mail:read",  # space is the RFC 6749 delimiter
            'docs:"read"',
            "docs:read\\write",
            "",
        ],
    )
    def test_malformed_scope_tokens_are_still_rejected(self, scope: str) -> None:
        """Widened, not removed. A scope containing the delimiter is still wrong."""
        with pytest.raises(ValidationError):
            McpServerCard.model_validate(
                self.raw_card(self.GOOD_NAME, required_scopes=[scope])
            )


class TestOneBadCardCostsOnlyThatCard(McpCardFixtureMixin):
    """Fault 2 — a rejected card was fatal to the whole registry listing."""

    async def test_malformed_card_does_not_hide_the_healthy_ones(self) -> None:
        registry = self.registry_over(
            [self.unparseable_card(), self.raw_card(self.GOOD_NAME)]
        )

        cards = await registry.list_available_servers(self.context())

        assert tuple(card.name for card in cards) == (self.GOOD_NAME,)

    async def test_every_card_malformed_yields_an_empty_list_not_an_error(
        self,
    ) -> None:
        """Zero MCP servers is a legitimate runtime state — a user with none.

        A runtime that starts happily with no connectors must start with a
        broken one, or "broken" is a strictly worse outcome than "absent".
        """
        registry = self.registry_over([self.unparseable_card()])

        assert await registry.list_available_servers(self.context()) == ()

    async def test_typed_cards_are_passed_through_unvalidated(self) -> None:
        """A provider that already validated is not re-validated or dropped."""
        registry = self.registry_over([self.typed_card(self.GOOD_NAME)])

        cards = await registry.list_available_servers(self.context())

        assert tuple(card.name for card in cards) == (self.GOOD_NAME,)

    async def test_provider_wide_failure_still_raises_typed_error(self) -> None:
        """A provider that cannot answer is a dependency failure, not one bad row.

        Silently returning no connectors when the backend is down would be its
        own wrong answer — the model would be told the user has no tools.
        """
        registry = DynamicMcpRegistry(
            providers=(ExplodingCardProvider(error=httpx.ConnectError("refused")),)
        )

        with pytest.raises(AgentRuntimeError) as excinfo:
            await registry.list_available_servers(self.context())

        assert excinfo.value.code == RuntimeErrorCode.CAPABILITY_LOAD_ERROR
        assert excinfo.value.safe_message == Messages.Registry.CARDS_LOAD_FAILED
        assert excinfo.value.retryable is True


class TestARejectionSaysWhatWasRejected(McpCardFixtureMixin):
    """Fault 3 — `raise ... from exc` dropped the cause before it was logged."""

    async def test_skipped_card_is_logged_with_its_identity_and_field(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        registry = self.registry_over(
            [self.unparseable_card(), self.raw_card(self.GOOD_NAME)]
        )

        with caplog.at_level(logging.ERROR, logger=_REGISTRY_LOGGER):
            await registry.list_available_servers(self.context())

        assert len(caplog.records) == 1
        logged = caplog.records[0].getMessage()
        # Which card, and which field — the two facts whose absence made the
        # original failure undiagnosable from the log.
        assert f"seed:{self.BAD_NAME}" in logged
        assert "load_cost" in logged
        assert Messages.Registry.INVALID_SERVER_CARD in logged

    async def test_a_card_too_broken_to_name_is_still_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        registry = self.registry_over([{"short_description": "no name at all"}])

        with caplog.at_level(logging.ERROR, logger=_REGISTRY_LOGGER):
            await registry.list_available_servers(self.context())

        assert len(caplog.records) == 1
        assert "<unidentified card>" in caplog.records[0].getMessage()

    async def test_rejection_log_does_not_echo_the_offending_value(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Field and reason, never pydantic's ``input``.

        A card carries connector metadata; a log line is not the place to widen
        what that exposes.
        """
        secret = "tok_do_not_log_this_value"
        registry = self.registry_over(
            [self.raw_card(self.BAD_NAME, load_cost=0, display_name=secret)]
        )

        with caplog.at_level(logging.ERROR, logger=_REGISTRY_LOGGER):
            await registry.list_available_servers(self.context())

        assert secret not in caplog.records[0].getMessage()

    async def test_provider_wide_failure_logs_its_cause(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        registry = DynamicMcpRegistry(
            providers=(
                ExplodingCardProvider(error=httpx.ConnectError("connection refused")),
            )
        )

        with caplog.at_level(logging.ERROR, logger=_REGISTRY_LOGGER):
            with pytest.raises(AgentRuntimeError):
                await registry.list_available_servers(self.context())

        logged = caplog.records[0].getMessage()
        assert "ConnectError" in logged
        assert "connection refused" in logged


class TestBackendProviderSkipsMalformedCards(McpCardFixtureMixin):
    """The site that actually broke: the provider validates before the registry.

    Without a skip HERE the registry's tolerance is unreachable — a malformed
    card escapes ``list_server_cards`` as a provider-level failure, which is
    fatal by design, and the run dies exactly as before.
    """

    def provider_returning(
        self, cards: Sequence[dict[str, object]]
    ) -> BackendMcpProvider:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"servers": list(cards)})

        return BackendMcpProvider(
            backend_url="http://backend.local",
            runtime_context=self.context(),
            auth_redirect_uri="http://127.0.0.1:0/callback",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    async def test_malformed_card_is_skipped_not_raised(self) -> None:
        provider = self.provider_returning(
            [self.unparseable_card(), self.raw_card(self.GOOD_NAME)]
        )

        cards = await provider.list_server_cards()

        assert tuple(card.name for card in cards) == (self.GOOD_NAME,)

    async def test_gmail_card_with_google_scopes_survives_end_to_end(self) -> None:
        """The original payload, verbatim, through the original path.

        The scope grammar is why it failed; ``_runtime_visible_card`` clearing
        required_scopes is why nothing needed the value in the first place.
        """
        provider = self.provider_returning(
            [
                self.raw_card(
                    self.BAD_NAME,
                    auth_state="auth_pending",
                    connector_slug="gmail",
                    required_scopes=[_GOOGLE_READONLY_SCOPE],
                )
            ]
        )

        cards = await provider.list_server_cards()

        assert tuple(card.name for card in cards) == (self.BAD_NAME,)
        assert cards[0].required_scopes == frozenset()


class TestAgentConstructionSurvivesABadCard(McpCardFixtureMixin):
    """The blast radius: why a connector row stopped ordinary chat.

    ``acreate_agent_runtime`` lists MCP servers unconditionally, before the
    model is contacted, so a raise here is not "MCP is degraded" — it is "no
    run can start", in every conversation.
    """

    async def test_malformed_card_does_not_prevent_agent_construction(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        builder = CapturingAgentBuilder()
        dependencies = fake_dependencies.model_copy(
            update={
                "mcp_registry": self.registry_over(
                    [self.unparseable_card(), self.raw_card(self.GOOD_NAME)]
                )
            }
        )

        await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=dependencies,
            agent_builder=builder,
        )

        assert len(builder.calls) == 1
        assert self.GOOD_NAME in builder.calls[0].system_prompt
        assert self.BAD_NAME not in builder.calls[0].system_prompt

    async def test_agent_still_builds_when_every_card_is_malformed(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        """The exact shape of the outage: one connector, unparseable, no chat."""
        builder = CapturingAgentBuilder()
        dependencies = fake_dependencies.model_copy(
            update={"mcp_registry": self.registry_over([self.unparseable_card()])}
        )

        await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=dependencies,
            agent_builder=builder,
        )

        assert len(builder.calls) == 1
