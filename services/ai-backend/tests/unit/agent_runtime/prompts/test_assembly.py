from __future__ import annotations

import json

import pytest

from agent_runtime.prompts import (
    LockedTaskProfile,
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
)


def _context(**changes: object) -> PromptAssemblyContext:
    values: dict[str, object] = {
        "provider": "OpenAI",
        "model_family": "GPT_5.2",
        "harness_revision": "harness-v1",
        "capability_bridge_revision": "bridge-v1",
        "tool_schema_revision": "tools-v1",
        "policy_revision": "policy-v1",
        "authorization_revision": "auth-v1",
    }
    values.update(changes)
    return PromptAssemblyContext.model_validate(values)


def _fragment(
    fragment_id: str,
    *,
    content: str,
    tier: PromptFragmentTier,
    source_scope: PromptFragmentScope = PromptFragmentScope.INSTALLATION,
    scope: PromptFragmentScope = PromptFragmentScope.INSTALLATION,
    cache_eligibility: PromptCacheEligibility = PromptCacheEligibility.NEVER,
    scope_fingerprint: str | None = None,
    sensitivity: PromptSensitivity = PromptSensitivity.INTERNAL,
    trust: PromptTrustLabel = PromptTrustLabel.TRUSTED_RUNTIME,
) -> PromptFragment:
    return PromptFragment(
        fragment_id=fragment_id,
        source_owner="test.prompt",
        source_revision="r1",
        tier=tier,
        source_scope=source_scope,
        scope=scope,
        sensitivity=sensitivity,
        trust=trust,
        content=content,
        cache_eligibility=cache_eligibility,
        scope_fingerprint=scope_fingerprint,
    )


def _policy() -> PromptFragment:
    return _fragment(
        "base",
        content="Stable safety policy.",
        tier=PromptFragmentTier.SYSTEM_POLICY,
        cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
        trust=PromptTrustLabel.IMMUTABLE_POLICY,
    )


def _assemble(*fragments: PromptFragment, context=None):
    return PromptAssembler(context=context or _context()).assemble(fragments)


def test_assembly_is_deterministic_and_all_serialization_is_body_free() -> None:
    fragments = (
        _fragment(
            "current",
            content="Untrusted current context.",
            tier=PromptFragmentTier.CURRENT_TURN,
            source_scope=PromptFragmentScope.RUN,
            scope=PromptFragmentScope.RUN,
            scope_fingerprint="a" * 64,
            sensitivity=PromptSensitivity.PERSONAL,
            trust=PromptTrustLabel.UNTRUSTED_USER,
        ),
        _policy(),
    )

    plan = _assemble(*fragments)
    reversed_plan = _assemble(*reversed(fragments))

    assert plan.rendered_prompt == "Stable safety policy.\n\nUntrusted current context."
    assert plan.plan_digest == reversed_plan.plan_digest
    assert plan.stable_prefix_digest == reversed_plan.stable_prefix_digest
    assert plan.provider == "openai"
    assert plan.model_family == "gpt-5.2"
    serialized = json.dumps(plan.model_dump(mode="json"), sort_keys=True)
    diagnostic = json.dumps(plan.diagnostic(), sort_keys=True)
    fragment_dump = json.dumps(plan.fragments[0].model_dump(mode="json"))
    for body in ("Stable safety policy.", "Untrusted current context."):
        assert body not in serialized
        assert body not in diagnostic
        assert body not in fragment_dump


@pytest.mark.parametrize(
    ("fragment", "reason"),
    (
        (
            _fragment(
                "broad",
                content="Scoped source.",
                tier=PromptFragmentTier.CONTEXTUAL,
                source_scope=PromptFragmentScope.CONVERSATION,
                scope=PromptFragmentScope.PROFILE,
                scope_fingerprint="a" * 64,
            ),
            PromptAssemblyFailureReason.SOURCE_SCOPE_BROADENED,
        ),
        (
            _fragment(
                "secret",
                content="credential material",
                tier=PromptFragmentTier.CONTEXTUAL,
                sensitivity=PromptSensitivity.SECRET,
            ),
            PromptAssemblyFailureReason.SECRET_FRAGMENT_FORBIDDEN,
        ),
        (
            _fragment(
                "mutable",
                content="Current state",
                tier=PromptFragmentTier.VOLATILE,
                cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
            ),
            PromptAssemblyFailureReason.MUTABLE_FRAGMENT_CACHEABLE,
        ),
        (
            _fragment(
                "missing-scope",
                content="Profile state",
                tier=PromptFragmentTier.CONTEXTUAL,
                source_scope=PromptFragmentScope.PROFILE,
                scope=PromptFragmentScope.PROFILE,
            ),
            PromptAssemblyFailureReason.MISSING_SCOPE_FINGERPRINT,
        ),
    ),
)
def test_closed_fragment_validation_reasons(
    fragment: PromptFragment,
    reason: PromptAssemblyFailureReason,
) -> None:
    with pytest.raises(PromptAssemblyValidationError) as caught:
        _assemble(_policy(), fragment)
    assert caught.value.reason is reason


def test_duplicate_fragment_ids_are_rejected_even_at_different_tiers() -> None:
    with pytest.raises(PromptAssemblyValidationError) as caught:
        _assemble(
            _fragment(
                "same",
                content="One",
                tier=PromptFragmentTier.SYSTEM_POLICY,
                trust=PromptTrustLabel.IMMUTABLE_POLICY,
            ),
            _fragment(
                "same",
                content="Two",
                tier=PromptFragmentTier.CONTEXTUAL,
            ),
        )
    assert caught.value.reason is PromptAssemblyFailureReason.DUPLICATE_FRAGMENT_ID


def test_cacheable_fragments_must_form_one_contiguous_prefix() -> None:
    with pytest.raises(PromptAssemblyValidationError) as caught:
        _assemble(
            _fragment(
                "00-policy",
                content="Policy",
                tier=PromptFragmentTier.SYSTEM_POLICY,
                trust=PromptTrustLabel.IMMUTABLE_POLICY,
            ),
            _fragment(
                "10-late-cache",
                content="Late stable",
                tier=PromptFragmentTier.STABLE,
                cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
            ),
        )
    assert (
        caught.value.reason is PromptAssemblyFailureReason.NON_CONTIGUOUS_STABLE_PREFIX
    )


def test_profile_fragments_cannot_mix_scope_fingerprints() -> None:
    with pytest.raises(PromptAssemblyValidationError) as caught:
        _assemble(
            _policy(),
            _fragment(
                "profile-a",
                content="A",
                tier=PromptFragmentTier.CONTEXTUAL,
                source_scope=PromptFragmentScope.PROFILE,
                scope=PromptFragmentScope.PROFILE,
                scope_fingerprint="a" * 64,
            ),
            _fragment(
                "profile-b",
                content="B",
                tier=PromptFragmentTier.CONTEXTUAL,
                source_scope=PromptFragmentScope.PROFILE,
                scope=PromptFragmentScope.PROFILE,
                scope_fingerprint="b" * 64,
            ),
        )
    assert (
        caught.value.reason is PromptAssemblyFailureReason.CONFLICTING_SCOPE_FINGERPRINT
    )


@pytest.mark.parametrize(
    "changed",
    (
        {"model_family": "claude-4"},
        {"harness_revision": "harness-v2"},
        {"capability_bridge_revision": "bridge-v2"},
        {"tool_schema_revision": "tools-v2"},
        {"policy_revision": "policy-v2"},
        {"authorization_revision": "auth-v2"},
        {
            "locked_task_profile": LockedTaskProfile(
                task_family="research",
                profile_revision="profile-v2",
                lock_revision="lock-v1",
            )
        },
    ),
)
def test_every_authority_revision_invalidates_plan_and_stable_prefix(
    changed: dict[str, object],
) -> None:
    baseline = _assemble(_policy(), context=_context())
    updated = _assemble(_policy(), context=_context(**changed))

    assert updated.plan_digest != baseline.plan_digest
    assert updated.stable_prefix_digest != baseline.stable_prefix_digest
    assert updated.complete_system_digest == baseline.complete_system_digest


def test_body_hashing_is_bounded_to_one_per_fragment_plus_complete_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_runtime.prompts import assembly

    calls = 0
    real_sha256_hex = assembly._sha256_hex

    def counted_sha256_hex(value: bytes):
        nonlocal calls
        calls += 1
        return real_sha256_hex(value)

    monkeypatch.setattr(assembly, "_sha256_hex", counted_sha256_hex)
    fragments = [_policy()]
    fragments.extend(
        _fragment(
            f"run-{index:03}",
            content=f"Run material {index}",
            tier=PromptFragmentTier.VOLATILE,
            source_scope=PromptFragmentScope.RUN,
            scope=PromptFragmentScope.RUN,
            scope_fingerprint="a" * 64,
        )
        for index in range(64)
    )

    plan = _assemble(*fragments)
    _ = plan.diagnostic()
    _ = plan.diagnostic()

    assert calls == len(fragments) + 1
