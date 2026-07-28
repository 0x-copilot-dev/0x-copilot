"""F2 provider prompt cache decoration parity tests."""

from __future__ import annotations

from copy import deepcopy
import random

from langchain_core.messages import SystemMessage
import pytest

from agent_runtime.prompts import (
    AnthropicProductPromptCacheAdapter,
    PromptAssembler,
    PromptCacheEligibility,
    PromptFragment,
    PromptFragmentScope,
    PromptFragmentTier,
    ProviderCacheAdapterRegistry,
    ProviderCacheFallbackSignal,
    ProviderCacheOwner,
    ProviderPromptDecorator,
)


def _plan(*, non_contiguous: bool = False):
    fragments = [
        PromptFragment(
            fragment_id="policy",
            revision="v1",
            tier=PromptFragmentTier.SYSTEM_POLICY,
            scope=PromptFragmentScope.INSTALLATION,
            content="Policy text",
            cache_eligibility=(
                PromptCacheEligibility.NEVER
                if non_contiguous
                else PromptCacheEligibility.STABLE_PREFIX
            ),
        ),
        PromptFragment(
            fragment_id="run",
            revision="v1",
            tier=PromptFragmentTier.VOLATILE,
            scope=PromptFragmentScope.RUN,
            scope_fingerprint="a" * 64,
            content="Run-specific text",
        ),
    ]
    if non_contiguous:
        fragments.append(
            PromptFragment(
                fragment_id="late-stable",
                revision="v1",
                tier=PromptFragmentTier.STABLE,
                scope=PromptFragmentScope.INSTALLATION,
                content="Late stable text",
                cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
            )
        )
    return PromptAssembler().assemble(fragments)


def test_unsupported_provider_receives_byte_identical_legacy_prompt() -> None:
    plan = _plan()

    decoration = ProviderPromptDecorator().decorate(
        provider="openai",
        plan=plan,
        cache_owner=ProviderCacheOwner.PRODUCT,
        supports_explicit_cache_controls=True,
    )

    assert decoration.system_prompt == plan.rendered_prompt
    assert not decoration.provider_cache_enabled


def test_anthropic_marks_only_last_stable_prefix_block_cacheable() -> None:
    plan = _plan()

    decoration = ProviderPromptDecorator().decorate(
        provider="anthropic",
        plan=plan,
        cache_owner=ProviderCacheOwner.PRODUCT,
        supports_explicit_cache_controls=True,
    )

    assert isinstance(decoration.system_prompt, SystemMessage)
    assert decoration.provider_cache_enabled
    blocks = decoration.system_prompt.content
    assert isinstance(blocks, list)
    assert blocks[0]["text"] == "Policy text"
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[1] == {"type": "text", "text": "Run-specific text"}
    assert decoration.cached_prefix_digest == plan.stable_prefix_digest


def test_non_contiguous_eligible_fragment_fails_closed_to_legacy_string() -> None:
    plan = _plan(non_contiguous=True)

    decoration = ProviderPromptDecorator().decorate(
        provider="anthropic",
        plan=plan,
        cache_owner=ProviderCacheOwner.PRODUCT,
        supports_explicit_cache_controls=True,
    )

    assert decoration.system_prompt == plan.rendered_prompt
    assert decoration.reason_code == "non_contiguous_cacheable_prefix"


def test_framework_owner_receives_unmarked_caller_prompt() -> None:
    plan = _plan()

    decoration = ProviderPromptDecorator().decorate(
        provider="anthropic",
        plan=plan,
        cache_owner=ProviderCacheOwner.FRAMEWORK,
    )

    assert decoration.system_prompt == plan.rendered_prompt
    assert decoration.cache_owner is ProviderCacheOwner.FRAMEWORK
    assert not decoration.provider_cache_enabled
    assert decoration.cached_prefix_digest is None
    assert decoration.reason_code == "delegated_to_framework_middleware"


def test_product_owner_requires_model_qualification() -> None:
    plan = _plan()

    decoration = ProviderPromptDecorator().decorate(
        provider="anthropic",
        plan=plan,
        cache_owner=ProviderCacheOwner.PRODUCT,
    )

    assert decoration.system_prompt == plan.rendered_prompt
    assert not decoration.provider_cache_enabled
    assert decoration.reason_code == "model_not_qualified_for_explicit_cache_controls"


def test_product_and_framework_owners_cannot_stack() -> None:
    with pytest.raises(RuntimeError, match="cannot be combined"):
        ProviderCacheAdapterRegistry.default().decorate(
            provider="anthropic",
            model_family="claude-sonnet-4-6",
            plan=_plan(),
            cache_owner=ProviderCacheOwner.PRODUCT,
            framework_cache_installed=True,
        )


def test_framework_delegation_is_semantically_undecorated() -> None:
    plan = _plan()

    decoration = ProviderCacheAdapterRegistry.default().decorate(
        provider="anthropic",
        model_family="claude-sonnet-4-6",
        plan=plan,
        cache_owner=ProviderCacheOwner.FRAMEWORK,
        framework_cache_installed=True,
    )

    assert decoration.system_prompt == plan.rendered_prompt
    assert decoration.cache_owner is ProviderCacheOwner.FRAMEWORK
    assert decoration.adapter_ref is None
    assert decoration.reason_code == "delegated_to_framework_middleware"


def test_product_decoration_deep_copies_every_outbound_block() -> None:
    plan = _plan()
    plan_before = deepcopy(plan)

    decoration = ProviderCacheAdapterRegistry.default().decorate(
        provider="anthropic",
        model_family="claude-sonnet-4-6",
        plan=plan,
        cache_owner=ProviderCacheOwner.PRODUCT,
        framework_cache_installed=False,
    )

    assert isinstance(decoration.system_prompt, SystemMessage)
    blocks = decoration.system_prompt.content
    assert isinstance(blocks, list)
    blocks[0]["text"] = "mutated outbound copy"
    assert plan == plan_before
    assert plan.fragments[0].content == "Policy text"


def test_registry_order_is_deterministic() -> None:
    refs: set[tuple[str, ...]] = set()
    adapters = [
        AnthropicProductPromptCacheAdapter(),
    ]
    for seed in range(20):
        randomized = list(adapters)
        random.Random(seed).shuffle(randomized)
        refs.add(ProviderCacheAdapterRegistry(randomized).adapter_refs)

    assert refs == {("anthropic-system-prefix:v1",)}


def test_unsupported_or_model_unqualified_routes_are_byte_identical() -> None:
    plan = _plan()
    for provider, model in (
        ("openai", "gpt-5.4-mini"),
        ("anthropic", ""),
        ("anthropic", "not-a-claude-model"),
    ):
        decoration = ProviderCacheAdapterRegistry.default().decorate(
            provider=provider,
            model_family=model,
            plan=plan,
            cache_owner=ProviderCacheOwner.PRODUCT,
            framework_cache_installed=False,
        )
        assert decoration.system_prompt.encode() == plan.rendered_prompt.encode()
        assert not decoration.provider_cache_enabled


def test_pre_content_fallback_signal_is_closed_after_any_provider_observation() -> None:
    signal = ProviderCacheFallbackSignal.before_content(
        provider="anthropic",
        model_family="claude-sonnet-4-6",
        plan_digest="a" * 64,
        rejected_adapter_ref="anthropic-system-prefix:v1",
    )
    assert signal.next_cache_owner is ProviderCacheOwner.NONE

    for observed in (
        "provider_acknowledged",
        "content_observed",
        "tool_call_observed",
        "usage_observed",
    ):
        with pytest.raises(RuntimeError, match="fallback is forbidden"):
            ProviderCacheFallbackSignal.before_content(
                provider="anthropic",
                model_family="claude-sonnet-4-6",
                plan_digest="a" * 64,
                rejected_adapter_ref="anthropic-system-prefix:v1",
                **{observed: True},
            )
