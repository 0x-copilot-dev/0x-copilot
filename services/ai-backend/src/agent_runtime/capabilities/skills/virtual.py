"""Virtual, backend-backed Skill registry for user-created Markdown skills."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import os
from typing import Protocol

from enterprise_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
import httpx
from pydantic import Field, ValidationError

from agent_runtime.capabilities.http_pool import BackendHttpPool
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeContract,
    RuntimeErrorCode,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.validation import ValueNormalizer


class VirtualSkillCard(RuntimeContract):
    """Compact Skill summary for listing and capability gating."""

    skill_id: str
    name: str
    display_name: str
    description: str
    virtual_path: str
    scope: str
    source_type: str
    version: int
    allowed_tools: tuple[str, ...] = ()
    enabled: bool = True


class VirtualSkillBundle(RuntimeContract):
    """Full Skill payload including markdown content, returned on explicit load."""

    skill_id: str
    name: str
    display_name: str
    description: str
    markdown: str
    virtual_path: str
    version: int
    allowed_tools: tuple[str, ...] = ()
    metadata: dict[str, object] = Field(default_factory=dict)


RawSkillCard = VirtualSkillCard | Mapping[str, object]


class SkillProvider(Protocol):
    """Adapter boundary for Skill card listing and on-demand bundle loading."""

    async def list_skill_cards(self) -> Sequence[RawSkillCard]:
        """Return compact, model-visible Skill cards."""

    async def load_skill_by_name(self, name: str) -> VirtualSkillBundle:
        """Return the full Skill markdown bundle by stable name."""


@dataclass(frozen=True)
class BackendSkillProvider:
    """Skill provider that reads authorized Skills from the core backend.

    ``http_client`` defaults to the process-shared :class:`BackendHttpPool`
    so Skill list + bundle loads share a TLS connection with the rest of
    the runtime's backend traffic.
    """

    backend_url: str
    runtime_context: AgentRuntimeContext
    timeout_seconds: float = 10
    http_client: httpx.AsyncClient = field(
        default_factory=BackendHttpPool.get,
        repr=False,
        compare=False,
    )

    async def list_skill_cards(self) -> tuple[VirtualSkillCard, ...]:
        """Fetch compact Skill cards for the runtime context from the backend."""
        response = await self.http_client.get(
            f"{self.backend_url.rstrip('/')}/internal/v1/skills/cards",
            params={
                "org_id": self.runtime_context.org_id,
                "user_id": self.runtime_context.user_id,
            },
            headers=BackendSkillServiceAuth.headers(self.runtime_context),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return tuple(
            VirtualSkillCard.model_validate(card) for card in payload.get("skills", ())
        )

    async def load_skill_by_name(self, name: str) -> VirtualSkillBundle:
        """Fetch the full Skill bundle by stable name from the backend."""
        response = await self.http_client.get(
            f"{self.backend_url.rstrip('/')}/internal/v1/skills/by-name/{name}",
            params={
                "org_id": self.runtime_context.org_id,
                "user_id": self.runtime_context.user_id,
            },
            headers=BackendSkillServiceAuth.headers(self.runtime_context),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return VirtualSkillBundle.model_validate(response.json())


@dataclass
class VirtualSkillRegistry:
    """Lists and loads user-created Skills without writing markdown to disk."""

    providers: Sequence[SkillProvider]
    _card_cache: tuple[VirtualSkillCard, ...] | None = field(default=None, init=False)
    _bundle_cache: dict[str, VirtualSkillBundle] = field(
        default_factory=dict, init=False
    )

    async def list_available_skills(
        self, context: object
    ) -> tuple[VirtualSkillCard, ...]:
        """Return enabled Skill cards visible to the runtime context, sorted by name."""
        runtime_context = ValueNormalizer.coerce_runtime_context(context)
        cards = self._card_cache
        if cards is None:
            cards = await self._collect_cards(runtime_context)
            self._card_cache = cards
        duplicate = ValueNormalizer.first_duplicate_name(card.name for card in cards)
        if duplicate is not None:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "Duplicate Skill names are configured.",
                retryable=False,
                correlation_id=runtime_context.trace_id,
            )
        return tuple(
            sorted((card for card in cards if card.enabled), key=lambda card: card.name)
        )

    async def load_skill_by_name(self, name: str) -> VirtualSkillBundle:
        """Load and cache the full Skill bundle by name; raise on unknown or duplicate."""
        if name in self._bundle_cache:
            return self._bundle_cache[name]
        matches: list[SkillProvider] = []
        for provider in self.providers:
            for card in await provider.list_skill_cards():
                parsed = (
                    card
                    if isinstance(card, VirtualSkillCard)
                    else VirtualSkillCard.model_validate(card)
                )
                if parsed.name == name and parsed.enabled:
                    matches.append(provider)
        if not matches:
            raise AgentRuntimeError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                "Skill was not found for this runtime context.",
                retryable=False,
            )
        if len(matches) > 1:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "Duplicate Skill names are configured.",
                retryable=False,
            )
        bundle = await matches[0].load_skill_by_name(name)
        self._bundle_cache[name] = bundle
        return bundle

    async def _collect_cards(
        self, context: AgentRuntimeContext
    ) -> tuple[VirtualSkillCard, ...]:
        """Fetch and validate raw cards from all registered providers."""
        cards: list[VirtualSkillCard] = []
        for provider in self.providers:
            try:
                raw_cards = await provider.list_skill_cards()
            except AgentRuntimeError:
                raise
            except Exception as exc:
                raise AgentRuntimeError(
                    RuntimeErrorCode.CAPABILITY_LOAD_ERROR,
                    "Skill cards could not be loaded.",
                    retryable=True,
                    correlation_id=context.trace_id,
                ) from exc
            for raw_card in raw_cards:
                try:
                    cards.append(
                        raw_card
                        if isinstance(raw_card, VirtualSkillCard)
                        else VirtualSkillCard.model_validate(raw_card)
                    )
                except ValidationError as exc:
                    raise AgentRuntimeError(
                        RuntimeErrorCode.CONFIGURATION_ERROR,
                        "Skill card is invalid.",
                        retryable=False,
                        correlation_id=context.trace_id,
                    ) from exc
        return tuple(cards)


class BackendSkillServiceAuth:
    """Service-auth header construction for backend Skill calls."""

    @staticmethod
    def headers(runtime_context: AgentRuntimeContext) -> dict[str, str]:
        """Return service-token headers when ``ENTERPRISE_SERVICE_TOKEN`` is set; else ``{}``."""
        token = os.environ.get("ENTERPRISE_SERVICE_TOKEN", "").strip()
        if not token:
            return {}
        return {
            SERVICE_TOKEN_HEADER: token,
            ORG_HEADER: runtime_context.org_id,
            USER_HEADER: runtime_context.user_id,
        }
