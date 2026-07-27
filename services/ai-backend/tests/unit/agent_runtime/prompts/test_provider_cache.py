"""F2 provider prompt cache decoration parity tests."""

from __future__ import annotations

from langchain_core.messages import SystemMessage

from agent_runtime.prompts import (
    PromptAssembler,
    PromptCacheEligibility,
    PromptFragment,
    PromptFragmentScope,
    PromptFragmentTier,
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
