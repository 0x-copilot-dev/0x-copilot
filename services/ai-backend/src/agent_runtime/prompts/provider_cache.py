"""Versioned, single-owner provider prompt-cache adapters.

Deep Agents installs its provider cache middleware at the tail of each graph.
The product seam therefore selects exactly one owner for a model call:

* ``FRAMEWORK`` leaves the semantically assembled request undecorated so the
  pinned framework adapter can own all transport metadata;
* ``PRODUCT`` is valid only for a graph that proves the framework adapter is
  absent and applies one reviewed product adapter; and
* ``NONE`` is the immediate decoration backout.

Adapters never mutate an assembly plan, durable messages, tools, or caller
owned structured content.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from typing import Literal, Protocol, Sequence

from langchain_core.messages import SystemMessage
from pydantic import Field, model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.prompts.assembly import (
    PromptAssemblyPlan,
    PromptCacheEligibility,
)


class ProviderCacheOwner(StrEnum):
    """The one layer permitted to add provider cache controls."""

    NONE = "none"
    FRAMEWORK = "framework"
    PRODUCT = "product"


class ProviderCacheFallbackReason(StrEnum):
    """Closed reason set consumed by the Step 6/F10 attempt controller."""

    CACHE_METADATA_REJECTED = "cache_metadata_rejected"


class ProviderCacheRejectionRule(RuntimeContract):
    """Reviewed exact exception identity for one product cache adapter."""

    provider: str = Field(min_length=1, max_length=80)
    adapter_ref: str = Field(min_length=1, max_length=240)
    exception_module: str = Field(min_length=1, max_length=240)
    exception_qualname: str = Field(min_length=1, max_length=240)
    reason: ProviderCacheFallbackReason = (
        ProviderCacheFallbackReason.CACHE_METADATA_REJECTED
    )


class ProviderCacheRejectionObservation(RuntimeContract):
    """Typed proof that cache metadata was rejected before acknowledgement."""

    provider: str = Field(min_length=1, max_length=80)
    adapter_ref: str = Field(min_length=1, max_length=240)
    reason: ProviderCacheFallbackReason
    provider_acknowledged: Literal[False] = False


class ProviderCacheRejectionAdapterRegistry:
    """Closed exact-class cache rejection adapters; messages are never inspected."""

    def __init__(
        self,
        rules: Sequence[ProviderCacheRejectionRule] = (),
    ) -> None:
        ordered = tuple(
            sorted(
                rules,
                key=lambda item: (
                    item.provider,
                    item.adapter_ref,
                    item.exception_module,
                    item.exception_qualname,
                ),
            )
        )
        keys = tuple(
            (
                item.provider.strip().lower(),
                item.adapter_ref,
                item.exception_module,
                item.exception_qualname,
            )
            for item in ordered
        )
        if len(keys) != len(set(keys)):
            raise ValueError("provider cache rejection rules must be unique")
        self._rules = ordered

    def observe(
        self,
        *,
        provider: str,
        adapter_ref: str,
        error: BaseException,
    ) -> ProviderCacheRejectionObservation | None:
        """Return reviewed structured evidence or ``None``; never read error text."""

        provider_key = provider.strip().lower()
        error_type = type(error)
        for rule in self._rules:
            if (
                rule.provider.strip().lower() == provider_key
                and rule.adapter_ref == adapter_ref
                and error_type.__module__ == rule.exception_module
                and error_type.__qualname__ == rule.exception_qualname
            ):
                return ProviderCacheRejectionObservation(
                    provider=provider_key,
                    adapter_ref=adapter_ref,
                    reason=rule.reason,
                )
        return None


class ProviderCacheAdapterDescriptor(RuntimeContract):
    """Versioned qualification rule for one product-owned adapter."""

    adapter_id: str = Field(min_length=1, max_length=120)
    revision: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=80)
    model_patterns: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _normalize_patterns(self) -> "ProviderCacheAdapterDescriptor":
        if any(not pattern.strip() for pattern in self.model_patterns):
            raise ValueError("provider cache model patterns must be non-empty")
        return self

    @property
    def adapter_ref(self) -> str:
        return f"{self.adapter_id}:{self.revision}"

    def supports(self, *, provider: str, model_family: str) -> bool:
        normalized_provider = provider.strip().lower()
        normalized_model = model_family.strip().lower()
        return (
            normalized_provider == self.provider.strip().lower()
            and bool(normalized_model)
            and any(
                fnmatchcase(normalized_model, pattern.strip().lower())
                for pattern in self.model_patterns
            )
        )


@dataclass(frozen=True, slots=True)
class ProviderPromptDecoration:
    """Provider payload plus content-free cache diagnostics."""

    system_prompt: str | SystemMessage
    cache_owner: ProviderCacheOwner
    provider_cache_enabled: bool
    cached_prefix_digest: str | None
    reason_code: str
    adapter_ref: str | None = None


class ProviderPromptCacheAdapter(Protocol):
    """Pure product-owned decoration adapter."""

    @property
    def descriptor(self) -> ProviderCacheAdapterDescriptor: ...

    def decorate(self, plan: PromptAssemblyPlan) -> ProviderPromptDecoration: ...


class AnthropicProductPromptCacheAdapter:
    """Explicit Anthropic stable-prefix block decorator."""

    descriptor = ProviderCacheAdapterDescriptor(
        adapter_id="anthropic-system-prefix",
        revision="v1",
        provider="anthropic",
        model_patterns=("claude-*",),
    )

    def decorate(self, plan: PromptAssemblyPlan) -> ProviderPromptDecoration:
        cacheable_count = _contiguous_cacheable_prefix_length(plan)
        if cacheable_count == 0:
            reason = (
                "non_contiguous_cacheable_prefix"
                if any(
                    fragment.cache_eligibility is PromptCacheEligibility.STABLE_PREFIX
                    for fragment in plan.fragments
                )
                else "no_cacheable_prefix"
            )
            return _undecorated(
                plan=plan,
                owner=ProviderCacheOwner.PRODUCT,
                reason_code=reason,
                adapter_ref=self.descriptor.adapter_ref,
            )
        if any(
            fragment.cache_eligibility is PromptCacheEligibility.STABLE_PREFIX
            for fragment in plan.fragments[cacheable_count:]
        ):
            return _undecorated(
                plan=plan,
                owner=ProviderCacheOwner.PRODUCT,
                reason_code="non_contiguous_cacheable_prefix",
                adapter_ref=self.descriptor.adapter_ref,
            )

        # Build wholly new block dictionaries. A provider adapter must never
        # attach transport metadata to fragment or message objects owned by the
        # conversation/checkpoint path.
        blocks: list[dict[str, object]] = []
        for index, fragment in enumerate(plan.fragments):
            block: dict[str, object] = {
                "type": "text",
                "text": str(fragment.content),
            }
            if index == cacheable_count - 1:
                block["cache_control"] = {"type": "ephemeral"}
            blocks.append(block)
        return ProviderPromptDecoration(
            system_prompt=SystemMessage(content=deepcopy(blocks)),
            cache_owner=ProviderCacheOwner.PRODUCT,
            provider_cache_enabled=True,
            cached_prefix_digest=plan.stable_prefix_digest,
            reason_code="anthropic_stable_prefix",
            adapter_ref=self.descriptor.adapter_ref,
        )


class ProviderCacheAdapterRegistry:
    """Deterministic registry enforcing model qualification and one owner."""

    def __init__(
        self,
        adapters: Sequence[ProviderPromptCacheAdapter] = (),
        *,
        registry_revision: str = "provider-cache-registry-v1",
    ) -> None:
        normalized_revision = registry_revision.strip()
        if not normalized_revision:
            raise ValueError("provider cache registry revision must be non-empty")
        ordered = tuple(
            sorted(
                adapters,
                key=lambda adapter: (
                    adapter.descriptor.provider,
                    adapter.descriptor.adapter_id,
                    adapter.descriptor.revision,
                ),
            )
        )
        refs = [adapter.descriptor.adapter_ref for adapter in ordered]
        if len(refs) != len(set(refs)):
            raise ValueError("provider cache adapter refs must be unique")
        self._adapters = ordered
        self.registry_revision = normalized_revision

    @classmethod
    def default(cls) -> "ProviderCacheAdapterRegistry":
        return cls((AnthropicProductPromptCacheAdapter(),))

    @property
    def adapter_refs(self) -> tuple[str, ...]:
        return tuple(adapter.descriptor.adapter_ref for adapter in self._adapters)

    def decorate(
        self,
        *,
        provider: str,
        model_family: str,
        plan: PromptAssemblyPlan,
        cache_owner: ProviderCacheOwner,
        framework_cache_installed: bool,
    ) -> ProviderPromptDecoration:
        """Resolve one cache owner without ever stacking transport controls."""

        if cache_owner is ProviderCacheOwner.NONE:
            return _undecorated(
                plan=plan,
                owner=ProviderCacheOwner.NONE,
                reason_code="cache_controls_disabled",
            )
        if cache_owner is ProviderCacheOwner.FRAMEWORK:
            if not framework_cache_installed:
                return _undecorated(
                    plan=plan,
                    owner=ProviderCacheOwner.NONE,
                    reason_code="framework_cache_middleware_absent",
                )
            return _undecorated(
                plan=plan,
                owner=ProviderCacheOwner.FRAMEWORK,
                reason_code="delegated_to_framework_middleware",
            )
        if framework_cache_installed:
            # This is a composition error, not an opportunity to silently send
            # two sets of provider controls.
            raise RuntimeError(
                "product cache ownership cannot be combined with framework "
                "prompt-caching middleware"
            )

        matches = tuple(
            adapter
            for adapter in self._adapters
            if adapter.descriptor.supports(
                provider=provider,
                model_family=model_family,
            )
        )
        if not matches:
            return _undecorated(
                plan=plan,
                owner=ProviderCacheOwner.PRODUCT,
                reason_code="provider_or_model_unsupported",
            )
        if len(matches) != 1:
            raise RuntimeError(
                "provider cache registry matched more than one product adapter"
            )
        return matches[0].decorate(plan)


class ProviderPromptDecorator:
    """Compatibility facade over :class:`ProviderCacheAdapterRegistry`."""

    def __init__(
        self,
        registry: ProviderCacheAdapterRegistry | None = None,
    ) -> None:
        self._registry = registry or ProviderCacheAdapterRegistry.default()

    def decorate(
        self,
        *,
        provider: str,
        plan: PromptAssemblyPlan,
        cache_owner: ProviderCacheOwner,
        supports_explicit_cache_controls: bool = False,
        model_family: str = "",
        framework_cache_installed: bool | None = None,
    ) -> ProviderPromptDecoration:
        # Historical callers supplied a provider-level qualification boolean.
        # Keep that test/adapter surface while ensuring new runtime callers
        # always qualify a concrete model family.
        qualified_model = model_family.strip()
        if not qualified_model and supports_explicit_cache_controls:
            qualified_model = (
                "claude-compatible"
                if provider.strip().lower() == "anthropic"
                else "unsupported"
            )
        if (
            cache_owner is ProviderCacheOwner.PRODUCT
            and not supports_explicit_cache_controls
            and not model_family.strip()
        ):
            return _undecorated(
                plan=plan,
                owner=cache_owner,
                reason_code="model_not_qualified_for_explicit_cache_controls",
            )
        installed = (
            cache_owner is ProviderCacheOwner.FRAMEWORK
            if framework_cache_installed is None
            else framework_cache_installed
        )
        return self._registry.decorate(
            provider=provider,
            model_family=qualified_model,
            plan=plan,
            cache_owner=cache_owner,
            framework_cache_installed=installed,
        )


class ProviderCacheFallbackSignal(RuntimeContract):
    """One undecorated retry request, valid only before provider acknowledgement."""

    signal_revision: str = "provider-cache-fallback-v1"
    provider: str = Field(min_length=1, max_length=80)
    model_family: str = Field(min_length=1, max_length=200)
    plan_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    rejected_adapter_ref: str = Field(min_length=1, max_length=240)
    reason: ProviderCacheFallbackReason = (
        ProviderCacheFallbackReason.CACHE_METADATA_REJECTED
    )
    next_cache_owner: ProviderCacheOwner = ProviderCacheOwner.NONE

    @model_validator(mode="after")
    def _only_undecorated_attempt(self) -> "ProviderCacheFallbackSignal":
        if self.next_cache_owner is not ProviderCacheOwner.NONE:
            raise ValueError("cache fallback must select undecorated ownership")
        return self

    @classmethod
    def before_content(
        cls,
        *,
        provider: str,
        model_family: str,
        plan_digest: str,
        rejected_adapter_ref: str,
        provider_acknowledged: bool = False,
        content_observed: bool = False,
        tool_call_observed: bool = False,
        usage_observed: bool = False,
    ) -> "ProviderCacheFallbackSignal":
        """Create the Step 6/F10 signal or fail closed after ambiguous output."""

        if any(
            (
                provider_acknowledged,
                content_observed,
                tool_call_observed,
                usage_observed,
            )
        ):
            raise RuntimeError(
                "cache fallback is forbidden after acknowledgement, content, "
                "tool calls, or usage"
            )
        return cls(
            provider=provider,
            model_family=model_family,
            plan_digest=plan_digest,
            rejected_adapter_ref=rejected_adapter_ref,
        )


def _contiguous_cacheable_prefix_length(plan: PromptAssemblyPlan) -> int:
    count = 0
    for fragment in plan.fragments:
        if fragment.cache_eligibility is not PromptCacheEligibility.STABLE_PREFIX:
            break
        count += 1
    return count


def _undecorated(
    *,
    plan: PromptAssemblyPlan,
    owner: ProviderCacheOwner,
    reason_code: str,
    adapter_ref: str | None = None,
) -> ProviderPromptDecoration:
    return ProviderPromptDecoration(
        system_prompt=plan.rendered_prompt,
        cache_owner=owner,
        provider_cache_enabled=False,
        cached_prefix_digest=None,
        reason_code=reason_code,
        adapter_ref=adapter_ref,
    )


__all__ = (
    "AnthropicProductPromptCacheAdapter",
    "ProviderCacheAdapterDescriptor",
    "ProviderCacheAdapterRegistry",
    "ProviderCacheFallbackReason",
    "ProviderCacheFallbackSignal",
    "ProviderCacheOwner",
    "ProviderCacheRejectionAdapterRegistry",
    "ProviderCacheRejectionObservation",
    "ProviderCacheRejectionRule",
    "ProviderPromptCacheAdapter",
    "ProviderPromptDecoration",
    "ProviderPromptDecorator",
)
