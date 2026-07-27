from __future__ import annotations

import pytest

from agent_runtime.prompts import (
    PromptAssembler,
    PromptCacheEligibility,
    PromptFragment,
    PromptFragmentScope,
    PromptFragmentTier,
)


def _fragment(
    fragment_id: str,
    *,
    content: str,
    tier: PromptFragmentTier,
    scope: PromptFragmentScope = PromptFragmentScope.INSTALLATION,
    cache_eligibility: PromptCacheEligibility = PromptCacheEligibility.NEVER,
    scope_fingerprint: str | None = None,
) -> PromptFragment:
    return PromptFragment(
        fragment_id=fragment_id,
        revision="r1",
        tier=tier,
        scope=scope,
        content=content,
        cache_eligibility=cache_eligibility,
        scope_fingerprint=scope_fingerprint,
    )


def test_assembly_is_deterministic_and_diagnostics_never_contain_prompt_body() -> None:
    plan = PromptAssembler().assemble(
        (
            _fragment(
                "current",
                content="Untrusted current context.",
                tier=PromptFragmentTier.CURRENT_TURN,
                scope=PromptFragmentScope.RUN,
                scope_fingerprint="a" * 64,
            ),
            _fragment(
                "base",
                content="Stable safety policy.",
                tier=PromptFragmentTier.SYSTEM_POLICY,
                cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
            ),
        )
    )

    assert plan.rendered_prompt == "Stable safety policy.\n\nUntrusted current context."
    assert plan.stable_prefix_digest is not None
    diagnostic = plan.diagnostic()
    assert "Stable safety policy." not in str(diagnostic)
    assert "Untrusted current context." not in str(diagnostic)


def test_cacheable_fragments_cannot_carry_profile_or_authorization_scope() -> None:
    with pytest.raises(
        ValueError, match="cacheable fragments must be installation-scoped"
    ):
        _fragment(
            "profile",
            content="Profile-specific instruction.",
            tier=PromptFragmentTier.STABLE,
            scope=PromptFragmentScope.PROFILE,
            scope_fingerprint="a" * 64,
            cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
        )


def test_duplicate_fragment_ids_are_rejected_even_at_different_tiers() -> None:
    with pytest.raises(ValueError, match="ids must be unique"):
        PromptAssembler().assemble(
            (
                _fragment(
                    "same",
                    content="One",
                    tier=PromptFragmentTier.SYSTEM_POLICY,
                ),
                _fragment(
                    "same",
                    content="Two",
                    tier=PromptFragmentTier.CONTEXTUAL,
                ),
            )
        )
