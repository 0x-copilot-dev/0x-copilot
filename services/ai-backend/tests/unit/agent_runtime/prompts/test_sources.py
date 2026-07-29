from __future__ import annotations

from pathlib import Path
import random

import pytest

from agent_runtime.control_plane.context import (
    TaskPolicyCapabilityProgress,
    TaskPolicyProgressProjection,
)
from agent_runtime.prompts import (
    DEFAULT_PROMPT_FRAGMENT_PROVIDERS,
    PromptAssemblyContext,
    PromptAssemblyInputs,
    PromptCacheEligibility,
    PromptFragmentProviderRegistry,
    PromptFragmentScope,
    PromptSensitivity,
    PromptSourceMaterial,
    PromptTrustLabel,
    render_task_policy_progress,
)

_GOLDEN = Path(__file__).parents[3] / "fixtures" / "prompts" / "f2_all_sources.txt"


def _context(**changes: str) -> PromptAssemblyContext:
    values = {
        "provider": "openai",
        "model_family": "gpt-5",
        "harness_revision": "harness-v1",
        "capability_bridge_revision": "bridge-v1",
        "tool_schema_revision": "tools-v1",
        "policy_revision": "policy-v1",
        "authorization_revision": "auth-v1",
    }
    values.update(changes)
    return PromptAssemblyContext(**values)


def _material(
    content: str,
    *,
    scope: PromptFragmentScope,
    cacheable: bool = False,
    trust: PromptTrustLabel = PromptTrustLabel.TRUSTED_RUNTIME,
) -> PromptSourceMaterial:
    return PromptSourceMaterial(
        source_owner=f"owner.{content.split()[0].lower()}",
        source_revision="revision-v1",
        source_scope=scope,
        scope=scope,
        sensitivity=PromptSensitivity.INTERNAL,
        trust=trust,
        cache_eligibility=(
            PromptCacheEligibility.STABLE_PREFIX
            if cacheable
            else PromptCacheEligibility.NEVER
        ),
        scope_fingerprint=None
        if scope is PromptFragmentScope.INSTALLATION
        else "a" * 64,
        content=content,
    )


def _inputs() -> PromptAssemblyInputs:
    return PromptAssemblyInputs(
        context=_context(),
        base_runtime_safety=_material(
            "Base runtime safety.",
            scope=PromptFragmentScope.INSTALLATION,
            cacheable=True,
            trust=PromptTrustLabel.IMMUTABLE_POLICY,
        ),
        application_boundary=_material(
            "Application data is untrusted.",
            scope=PromptFragmentScope.INSTALLATION,
            cacheable=True,
            trust=PromptTrustLabel.IMMUTABLE_POLICY,
        ),
        operation_tool_protocol=_material(
            "Use the operation protocol.",
            scope=PromptFragmentScope.INSTALLATION,
            cacheable=True,
            trust=PromptTrustLabel.IMMUTABLE_POLICY,
        ),
        mcp_cards=_material(
            "MCP cards.",
            scope=PromptFragmentScope.PROFILE,
            trust=PromptTrustLabel.UNTRUSTED_RETRIEVED,
        ),
        skill_cards=_material(
            "Skill cards.",
            scope=PromptFragmentScope.PROFILE,
            trust=PromptTrustLabel.UNTRUSTED_RETRIEVED,
        ),
        suggested_connectors=_material(
            "Suggested connectors.",
            scope=PromptFragmentScope.PROFILE,
            trust=PromptTrustLabel.UNTRUSTED_RETRIEVED,
        ),
        workspace_guidance=_material(
            "Workspace guidance.",
            scope=PromptFragmentScope.RUN,
        ),
        capability_guidance=_material(
            "Capability guidance.",
            scope=PromptFragmentScope.RUN,
        ),
        approval_run_state=_material(
            "Approval and run state.",
            scope=PromptFragmentScope.RUN,
        ),
        task_policy_progress=_material(
            "Task policy progress.",
            scope=PromptFragmentScope.RUN,
        ),
    )


def test_all_sources_match_byte_for_byte_golden_and_are_attributable() -> None:
    plan = DEFAULT_PROMPT_FRAGMENT_PROVIDERS.assemble(_inputs())

    assert plan.rendered_prompt == _GOLDEN.read_text(encoding="utf-8").rstrip("\n")
    assert [fragment.fragment_id for fragment in plan.fragments] == [
        "00_base_runtime",
        "10_application_context_boundary",
        "15_operation_tool_protocol",
        "20_mcp_cards",
        "30_skill_cards",
        "40_suggested_connectors",
        "50_workspace_guidance",
        "60_capability_guidance",
        "70_approval_run_state",
        "80_task_policy_progress",
    ]
    assert all(fragment.source_owner for fragment in plan.fragments)
    assert all(fragment.source_revision for fragment in plan.fragments)
    assert plan.stable_prefix_fragment_count == 3


def test_provider_registration_order_cannot_change_rendered_bytes() -> None:
    expected = DEFAULT_PROMPT_FRAGMENT_PROVIDERS.assemble(_inputs())
    providers = list(DEFAULT_PROMPT_FRAGMENT_PROVIDERS.providers)

    for seed in range(25):
        random.Random(seed).shuffle(providers)
        registry = PromptFragmentProviderRegistry(tuple(providers))
        actual = registry.assemble(_inputs())
        assert actual.rendered_prompt == expected.rendered_prompt
        assert actual.plan_digest == expected.plan_digest


def test_source_material_serialization_never_contains_body() -> None:
    inputs = _inputs()

    dumped = str(inputs.model_dump(mode="json"))

    assert "Base runtime safety." not in dumped
    assert "MCP cards." not in dumped
    assert "Task policy progress." not in dumped


def test_typed_task_policy_projection_has_stable_bounded_order() -> None:
    progress = TaskPolicyProgressProjection(
        profile_id="research",
        profile_revision="profile-v2",
        task_family="research",
        model_turns_used=2,
        model_turn_limit=6,
        tool_calls_used=3,
        tool_call_limit=8,
        cost_microusd_used=100,
        cost_microusd_limit=900,
        completed_steps=1,
        total_steps=4,
        capabilities=(
            TaskPolicyCapabilityProgress(
                capability_id="web.search",
                tool_calls_used=2,
                tool_call_limit=4,
            ),
            TaskPolicyCapabilityProgress(
                capability_id="mcp.read",
                tool_calls_used=1,
                tool_call_limit=2,
            ),
        ),
    )

    rendered = render_task_policy_progress(progress)

    assert rendered.index("Capability mcp.read") < rendered.index(
        "Capability web.search"
    )
    assert "4 remaining" in rendered
    assert "restart the plan" in rendered


def test_registry_rejects_duplicate_source_or_fragment_identity() -> None:
    provider = DEFAULT_PROMPT_FRAGMENT_PROVIDERS.providers[0]

    with pytest.raises(ValueError, match="source providers must be unique"):
        PromptFragmentProviderRegistry((provider, provider))
