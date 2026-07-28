"""F2 provider prompt cache decoration parity tests."""

from __future__ import annotations

from copy import deepcopy
import random

from langchain_core.messages import SystemMessage
import pytest

from agent_runtime.prompts import (
    AnthropicProductPromptCacheAdapter,
    PromptAssembler,
    PromptAssemblyContext,
    PromptAssemblyFailureReason,
    PromptAssemblyValidationError,
    PromptCacheEligibility,
    PromptFragment,
    PromptFragmentScope,
    PromptFragmentTier,
    ProviderCacheAdapterRegistry,
    ProviderCacheFallbackSignal,
    ProviderCacheRejectionAdapterRegistry,
    ProviderCacheRejectionRule,
    PromptSensitivity,
    PromptTrustLabel,
    ProviderCacheOwner,
    ProviderPromptDecorator,
)


def _context() -> PromptAssemblyContext:
    return PromptAssemblyContext(
        provider="anthropic",
        model_family="claude-4",
        harness_revision="harness-v1",
        capability_bridge_revision="bridge-v1",
        tool_schema_revision="tools-v1",
        policy_revision="policy-v1",
        authorization_revision="auth-v1",
    )


def _fragment(
    *,
    fragment_id: str,
    tier: PromptFragmentTier,
    scope: PromptFragmentScope,
    content: str,
    cache_eligibility: PromptCacheEligibility,
    scope_fingerprint: str | None = None,
    trust: PromptTrustLabel = PromptTrustLabel.TRUSTED_RUNTIME,
) -> PromptFragment:
    return PromptFragment(
        fragment_id=fragment_id,
        source_owner="test.prompt",
        source_revision="v1",
        tier=tier,
        source_scope=scope,
        scope=scope,
        sensitivity=PromptSensitivity.INTERNAL,
        trust=trust,
        content=content,
        cache_eligibility=cache_eligibility,
        scope_fingerprint=scope_fingerprint,
    )


def _plan():
    fragments = [
        _fragment(
            fragment_id="policy",
            tier=PromptFragmentTier.SYSTEM_POLICY,
            scope=PromptFragmentScope.INSTALLATION,
            content="Policy text",
            cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
            trust=PromptTrustLabel.IMMUTABLE_POLICY,
        ),
        _fragment(
            fragment_id="run",
            tier=PromptFragmentTier.VOLATILE,
            scope=PromptFragmentScope.RUN,
            scope_fingerprint="a" * 64,
            content="Run-specific text",
            cache_eligibility=PromptCacheEligibility.NEVER,
        ),
    ]
    return PromptAssembler(context=_context()).assemble(fragments)


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


def test_non_contiguous_eligible_fragment_never_reaches_provider_adapter() -> None:
    with pytest.raises(PromptAssemblyValidationError) as caught:
        PromptAssembler(context=_context()).assemble(
            (
                _fragment(
                    fragment_id="policy",
                    tier=PromptFragmentTier.SYSTEM_POLICY,
                    scope=PromptFragmentScope.INSTALLATION,
                    content="Policy",
                    cache_eligibility=PromptCacheEligibility.NEVER,
                    trust=PromptTrustLabel.IMMUTABLE_POLICY,
                ),
                _fragment(
                    fragment_id="late-stable",
                    tier=PromptFragmentTier.STABLE,
                    scope=PromptFragmentScope.INSTALLATION,
                    content="Late stable",
                    cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
                ),
            )
        )
    assert (
        caught.value.reason is PromptAssemblyFailureReason.NON_CONTIGUOUS_STABLE_PREFIX
    )


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


def test_cache_rejection_adapter_matches_exact_class_and_never_message_text() -> None:
    generic = type("BadRequestError", (Exception,), {})
    generic.__module__ = "anthropic"
    recognized = type("CacheMetadataRejectedError", (generic,), {})
    recognized.__module__ = "reviewed_cache_adapter"
    registry = ProviderCacheRejectionAdapterRegistry(
        (
            ProviderCacheRejectionRule(
                provider="anthropic",
                adapter_ref="anthropic-system-prefix:v1",
                exception_module=recognized.__module__,
                exception_qualname=recognized.__qualname__,
            ),
        )
    )

    assert (
        registry.observe(
            provider="anthropic",
            adapter_ref="anthropic-system-prefix:v1",
            error=recognized("opaque"),
        )
        is not None
    )
    assert (
        registry.observe(
            provider="anthropic",
            adapter_ref="anthropic-system-prefix:v1",
            error=generic("cache metadata rejected"),
        )
        is None
    )
