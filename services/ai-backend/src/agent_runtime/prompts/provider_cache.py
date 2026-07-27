"""Provider-specific cache decoration for a validated F2 prompt plan.

The assembly plan remains the source of prompt bytes. This adapter only changes
the transport representation where a provider supports explicit cache controls;
unsupported providers receive the exact legacy string.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from langchain_core.messages import SystemMessage

from agent_runtime.prompts.assembly import (
    PromptAssemblyPlan,
    PromptCacheEligibility,
)


class ProviderCacheOwner(StrEnum):
    """The one layer permitted to add provider cache controls."""

    NONE = "none"
    FRAMEWORK = "framework"
    PRODUCT = "product"


@dataclass(frozen=True)
class ProviderPromptDecoration:
    """Provider payload plus content-free cache diagnostics."""

    system_prompt: str | SystemMessage
    cache_owner: ProviderCacheOwner
    provider_cache_enabled: bool
    cached_prefix_digest: str | None
    reason_code: str


class ProviderPromptDecorator:
    """Decorate only a contiguous stable prefix for providers that support it."""

    def decorate(
        self,
        *,
        provider: str,
        plan: PromptAssemblyPlan,
        cache_owner: ProviderCacheOwner,
        supports_explicit_cache_controls: bool = False,
    ) -> ProviderPromptDecoration:
        if cache_owner is ProviderCacheOwner.FRAMEWORK:
            return ProviderPromptDecoration(
                system_prompt=plan.rendered_prompt,
                cache_owner=cache_owner,
                provider_cache_enabled=False,
                cached_prefix_digest=None,
                reason_code="delegated_to_framework_middleware",
            )
        if cache_owner is ProviderCacheOwner.NONE:
            return ProviderPromptDecoration(
                system_prompt=plan.rendered_prompt,
                cache_owner=cache_owner,
                provider_cache_enabled=False,
                cached_prefix_digest=None,
                reason_code="cache_controls_disabled",
            )
        if provider.strip().lower() != "anthropic":
            return ProviderPromptDecoration(
                system_prompt=plan.rendered_prompt,
                cache_owner=cache_owner,
                provider_cache_enabled=False,
                cached_prefix_digest=None,
                reason_code="provider_implicit_or_unsupported",
            )
        if not supports_explicit_cache_controls:
            return ProviderPromptDecoration(
                system_prompt=plan.rendered_prompt,
                cache_owner=cache_owner,
                provider_cache_enabled=False,
                cached_prefix_digest=None,
                reason_code="model_not_qualified_for_explicit_cache_controls",
            )
        cacheable_count = 0
        for fragment in plan.fragments:
            if fragment.cache_eligibility is PromptCacheEligibility.STABLE_PREFIX:
                cacheable_count += 1
                continue
            break
        if cacheable_count == 0:
            return ProviderPromptDecoration(
                system_prompt=plan.rendered_prompt,
                cache_owner=cache_owner,
                provider_cache_enabled=False,
                cached_prefix_digest=None,
                reason_code=(
                    "non_contiguous_cacheable_prefix"
                    if any(
                        fragment.cache_eligibility
                        is PromptCacheEligibility.STABLE_PREFIX
                        for fragment in plan.fragments
                    )
                    else "no_cacheable_prefix"
                ),
            )
        # A cacheable fragment after the first mutable fragment would not be a
        # reusable prefix. Fail closed to the byte-identical string rather than
        # accidentally caching profile/conversation material.
        if any(
            fragment.cache_eligibility is PromptCacheEligibility.STABLE_PREFIX
            for fragment in plan.fragments[cacheable_count:]
        ):
            return ProviderPromptDecoration(
                system_prompt=plan.rendered_prompt,
                cache_owner=cache_owner,
                provider_cache_enabled=False,
                cached_prefix_digest=None,
                reason_code="non_contiguous_cacheable_prefix",
            )
        blocks: list[dict[str, object]] = []
        for index, fragment in enumerate(plan.fragments):
            block: dict[str, object] = {"type": "text", "text": fragment.content}
            if index == cacheable_count - 1:
                block["cache_control"] = {"type": "ephemeral"}
            blocks.append(block)
        return ProviderPromptDecoration(
            system_prompt=SystemMessage(content=blocks),
            cache_owner=cache_owner,
            provider_cache_enabled=True,
            cached_prefix_digest=plan.stable_prefix_digest,
            reason_code="anthropic_stable_prefix",
        )


__all__ = (
    "ProviderCacheOwner",
    "ProviderPromptDecoration",
    "ProviderPromptDecorator",
)
