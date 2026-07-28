"""F2 provider prompt cache decoration parity tests."""

from __future__ import annotations

from langchain_core.messages import SystemMessage
import pytest

from agent_runtime.prompts import (
    PromptAssembler,
    PromptAssemblyContext,
    PromptAssemblyFailureReason,
    PromptAssemblyValidationError,
    PromptCacheEligibility,
    PromptFragment,
    PromptFragmentScope,
    PromptFragmentTier,
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
