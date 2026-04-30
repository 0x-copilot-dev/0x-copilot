"""Virtual, backend-backed Skill registry for user-created Markdown skills."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import os
from typing import Protocol

from enterprise_service_contracts.headers import SERVICE_TOKEN_HEADER
import httpx
from pydantic import Field, ValidationError

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract, RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError


class VirtualSkillCard(RuntimeContract):
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
    def list_skill_cards(self) -> Sequence[RawSkillCard]:
        """Return compact, model-visible Skill cards."""

    def load_skill_by_name(self, name: str) -> VirtualSkillBundle:
        """Return the full Skill markdown by stable name."""


@dataclass(frozen=True)
class BackendSkillProvider:
    """Skill provider that reads authorized Skills from the core backend."""

    backend_url: str
    runtime_context: AgentRuntimeContext
    timeout_seconds: float = 10

    def list_skill_cards(self) -> tuple[VirtualSkillCard, ...]:
        response = httpx.get(
            f"{self.backend_url.rstrip('/')}/internal/v1/skills/cards",
            params={"org_id": self.runtime_context.org_id, "user_id": self.runtime_context.user_id},
            headers=BackendSkillServiceAuth.headers(),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return tuple(VirtualSkillCard.model_validate(card) for card in payload.get("skills", ()))

    def load_skill_by_name(self, name: str) -> VirtualSkillBundle:
        response = httpx.get(
            f"{self.backend_url.rstrip('/')}/internal/v1/skills/by-name/{name}",
            params={"org_id": self.runtime_context.org_id, "user_id": self.runtime_context.user_id},
            headers=BackendSkillServiceAuth.headers(),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return VirtualSkillBundle.model_validate(response.json())


@dataclass
class VirtualSkillRegistry:
    """Lists and loads user-created Skills without writing markdown to disk."""

    providers: Sequence[SkillProvider]
    _card_cache: tuple[VirtualSkillCard, ...] | None = field(default=None, init=False)
    _bundle_cache: dict[str, VirtualSkillBundle] = field(default_factory=dict, init=False)

    def list_available_skills(self, context: object) -> tuple[VirtualSkillCard, ...]:
        runtime_context = self._coerce_context(context)
        cards = self._card_cache
        if cards is None:
            cards = self._collect_cards(runtime_context)
            self._card_cache = cards
        duplicate = self._first_duplicate_name(cards)
        if duplicate is not None:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "Duplicate Skill names are configured.",
                retryable=False,
                correlation_id=runtime_context.trace_id,
            )
        return tuple(sorted((card for card in cards if card.enabled), key=lambda card: card.name))

    def load_skill_by_name(self, name: str) -> VirtualSkillBundle:
        if name in self._bundle_cache:
            return self._bundle_cache[name]
        matches: list[SkillProvider] = []
        for provider in self.providers:
            for card in provider.list_skill_cards():
                parsed = card if isinstance(card, VirtualSkillCard) else VirtualSkillCard.model_validate(card)
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
        bundle = matches[0].load_skill_by_name(name)
        self._bundle_cache[name] = bundle
        return bundle

    def _collect_cards(self, context: AgentRuntimeContext) -> tuple[VirtualSkillCard, ...]:
        cards: list[VirtualSkillCard] = []
        for provider in self.providers:
            try:
                raw_cards = provider.list_skill_cards()
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

    @classmethod
    def _first_duplicate_name(cls, cards: Sequence[VirtualSkillCard]) -> str | None:
        counts = Counter(card.name for card in cards)
        duplicates = sorted(name for name, count in counts.items() if count > 1)
        return duplicates[0] if duplicates else None

    @classmethod
    def _coerce_context(cls, context: object) -> AgentRuntimeContext:
        if isinstance(context, AgentRuntimeContext):
            return context
        try:
            return AgentRuntimeContext.model_validate(context)
        except ValidationError as exc:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Runtime context is invalid.",
                retryable=False,
            ) from exc


class BackendSkillServiceAuth:
    """Service-auth header construction for backend Skill calls."""

    @staticmethod
    def headers() -> dict[str, str]:
        token = os.environ.get("ENTERPRISE_SERVICE_TOKEN", "").strip()
        return {SERVICE_TOKEN_HEADER: token} if token else {}
