"""Typed, call-local F2 authority for one undecorated cache retry."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Lock
from typing import Iterator, Sequence

from langchain_core.messages import SystemMessage

from agent_runtime.prompts.provider_cache import (
    ProviderCacheFallbackSignal,
    ProviderCacheOwner,
    ProviderCacheRejectionAdapterRegistry,
)
from agent_runtime.prompts.runtime_binding import (
    PromptRuntimeResult,
    tool_schema_revision,
)


class _ConsumeOnce:
    """Thread-safe one-shot permit shared only by one model-call context."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._consumed = False

    def consume(self) -> bool:
        with self._lock:
            if self._consumed:
                return False
            self._consumed = True
            return True


@dataclass(frozen=True, slots=True)
class PromptCacheFallbackHandoff:
    """Exact F2 plan/decoration identity visible only to its inner F10 call."""

    result: PromptRuntimeResult
    rejection_adapters: ProviderCacheRejectionAdapterRegistry
    _once: _ConsumeOnce = field(default_factory=_ConsumeOnce, compare=False, repr=False)

    def validate_provider_request(
        self,
        *,
        system_message: SystemMessage | None,
        tools: Sequence[object],
    ) -> None:
        """Fail closed if middleware ordering changed the F2-owned request."""

        observation = self.result.observation
        if tool_schema_revision(tools) != observation.tool_schema_revision:
            raise RuntimeError("F2 cache fallback handoff tool surface changed")
        expected_system = self.result.system_message
        if _message_value(system_message) != _message_value(expected_system):
            raise RuntimeError("F2 cache fallback handoff system prompt changed")

    def semantic_system_message(self) -> SystemMessage | None:
        """Return the same assembled semantics without transport cache metadata."""

        if not self.result.observation.sent_assembled_prompt:
            return deepcopy(self.result.system_message)
        plan = self.result.plan
        if plan is None:
            raise RuntimeError("sent F2 prompt has no typed assembly plan")
        return SystemMessage(content=plan.rendered_prompt)

    def undecorated_system_message(self) -> SystemMessage:
        """Return the reviewed product-owned undecorated retry payload."""

        if not self._supports_undecorated_retry():
            raise RuntimeError("F2 cache decoration has no supported undecoration")
        plan = self.result.plan
        assert plan is not None
        return SystemMessage(content=plan.rendered_prompt)

    def consume_rejection(
        self,
        error: BaseException,
        *,
        provider: str,
        model_family: str,
        provider_acknowledged: bool,
        content_observed: bool,
        tool_call_observed: bool,
        usage_observed: bool,
        external_effect_observed: bool,
    ) -> ProviderCacheFallbackSignal | None:
        """Consume one exact adapter result only at the safe pre-ack boundary."""

        if (
            not self._supports_undecorated_retry()
            or external_effect_observed
            or provider_acknowledged
            or content_observed
            or tool_call_observed
            or usage_observed
        ):
            return None
        observation = self.result.observation
        if (
            provider.strip().lower() != observation.provider.strip().lower()
            or model_family.strip().lower() != observation.model_family.strip().lower()
        ):
            return None
        adapter_ref = observation.cache_adapter_ref
        plan = self.result.plan
        assert adapter_ref is not None and plan is not None
        rejected = self.rejection_adapters.observe(
            provider=observation.provider,
            adapter_ref=adapter_ref,
            error=error,
        )
        if rejected is None or rejected.provider_acknowledged:
            return None
        signal = ProviderCacheFallbackSignal.before_content(
            provider=rejected.provider,
            model_family=observation.model_family,
            plan_digest=plan.plan_digest,
            rejected_adapter_ref=rejected.adapter_ref,
        )
        return signal if self._once.consume() else None

    def _supports_undecorated_retry(self) -> bool:
        """Framework-owned decoration denies until it exposes a skip contract."""

        result = self.result
        observation = result.observation
        decoration = result.decoration
        return (
            result.plan is not None
            and decoration is not None
            and observation.sent_assembled_prompt
            and observation.cache_owner is ProviderCacheOwner.PRODUCT
            and decoration.cache_owner is ProviderCacheOwner.PRODUCT
            and observation.provider_cache_enabled
            and decoration.provider_cache_enabled
            and bool(observation.cache_adapter_ref)
            and observation.cache_adapter_ref == decoration.adapter_ref
        )


_CURRENT_PROMPT_CACHE_FALLBACK: ContextVar[PromptCacheFallbackHandoff | None] = (
    ContextVar("agent_runtime_prompt_cache_fallback", default=None)
)


class PromptCacheFallbackContext:
    """Lexically scoped F2 handoff; ContextVar isolation supports parallel calls."""

    @staticmethod
    @contextmanager
    def bind(
        handoff: PromptCacheFallbackHandoff | None,
    ) -> Iterator[PromptCacheFallbackHandoff | None]:
        token: Token[PromptCacheFallbackHandoff | None] = (
            _CURRENT_PROMPT_CACHE_FALLBACK.set(handoff)
        )
        try:
            yield handoff
        finally:
            _CURRENT_PROMPT_CACHE_FALLBACK.reset(token)

    @staticmethod
    def current() -> PromptCacheFallbackHandoff | None:
        return _CURRENT_PROMPT_CACHE_FALLBACK.get()


def _message_value(message: SystemMessage | None) -> object:
    return None if message is None else message.model_dump(mode="json")


__all__ = (
    "PromptCacheFallbackContext",
    "PromptCacheFallbackHandoff",
)
