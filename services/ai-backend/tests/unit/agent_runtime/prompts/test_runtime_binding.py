"""Per-call F2 runtime binding tests."""

from __future__ import annotations

from langchain_core.messages import SystemMessage
import pytest

from agent_runtime.control_plane.context import TaskPolicyProgressProjection
from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.prompts import (
    FactoryPromptFragmentProvider,
    PromptAssemblyContext,
    PromptAssembler,
    PromptCacheEligibility,
    PromptFragment,
    PromptFragmentScope,
    PromptFragmentTier,
    PromptRuntimeBinding,
    PromptRuntimeObservation,
    PromptSensitivity,
    PromptTrustLabel,
    ProviderCacheAdapterRegistry,
    ProviderCacheOwner,
)


def _context() -> PromptAssemblyContext:
    return PromptAssemblyContext(
        provider="anthropic",
        model_family="claude-sonnet-4-6",
        harness_revision="harness-v4",
        capability_bridge_revision="bridge-v1",
        tool_schema_revision="tools-v1",
        policy_revision="policy-v1",
        authorization_revision="authorization-v1",
    )


def _legacy_plan():
    return PromptAssembler(context=_context()).assemble(
        (
            PromptFragment(
                fragment_id="00_policy",
                source_owner="test.runtime",
                source_revision="v1",
                tier=PromptFragmentTier.SYSTEM_POLICY,
                source_scope=PromptFragmentScope.INSTALLATION,
                scope=PromptFragmentScope.INSTALLATION,
                sensitivity=PromptSensitivity.INTERNAL,
                trust=PromptTrustLabel.IMMUTABLE_POLICY,
                content="Stable policy.",
                cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
            ),
            PromptFragment(
                fragment_id="10_context",
                source_owner="test.runtime",
                source_revision="v1",
                tier=PromptFragmentTier.CONTEXTUAL,
                source_scope=PromptFragmentScope.RUN,
                scope=PromptFragmentScope.RUN,
                sensitivity=PromptSensitivity.PERSONAL,
                trust=PromptTrustLabel.UNTRUSTED_RETRIEVED,
                scope_fingerprint="b" * 64,
                content="Current authorized context.",
                cache_eligibility=PromptCacheEligibility.NEVER,
            ),
        )
    )


class _Observer:
    def __init__(self) -> None:
        self.observations: list[PromptRuntimeObservation] = []

    def observe(self, observation: PromptRuntimeObservation) -> None:
        self.observations.append(observation)


class _BrokenProvider:
    def assembly_context(self, _call: object) -> PromptAssemblyContext:
        return _context()

    def fragments(self, _call: object) -> tuple[PromptFragment, ...]:
        raise ValueError("shadow-only private assembly failure")


def _binding(
    *,
    mode: FeatureMode,
    owner: ProviderCacheOwner = ProviderCacheOwner.NONE,
    framework_cache_installed: bool = False,
    observer: _Observer | None = None,
) -> PromptRuntimeBinding:
    return PromptRuntimeBinding(
        mode=mode,
        provider="anthropic",
        model_family="claude-sonnet-4-6",
        harness_revision="harness-v4",
        fragment_provider=FactoryPromptFragmentProvider(
            legacy_plan=_legacy_plan(),
            run_scope_fingerprint="b" * 64,
        ),
        cache_registry=ProviderCacheAdapterRegistry.default(),
        cache_owner=owner,
        framework_cache_installed=framework_cache_installed,
        observer=observer,
    )


@pytest.mark.parametrize("scope", ["supervisor", "subagent:task-123"])
def test_supervisor_and_local_subagent_assemble_at_each_call(scope: str) -> None:
    binding = _binding(mode=FeatureMode.ENFORCE)
    legacy = _legacy_plan().rendered_prompt

    result = binding.prepare(
        system_message=SystemMessage(content=f"{legacy}\n\nDeep Agents profile."),
        state={},
        tools=({"name": "search", "description": "Search.", "parameters": {}},),
        execution_scope=scope,
        task_policy_progress=None,
    )

    assert result.observation.execution_scope == scope
    assert result.plan is not None
    assert result.plan.rendered_prompt == f"{legacy}\n\nDeep Agents profile."
    assert result.system_message == SystemMessage(content=result.plan.rendered_prompt)


def test_progress_and_approval_change_without_rebuilding_binding() -> None:
    binding = _binding(mode=FeatureMode.ENFORCE)
    legacy = _legacy_plan().rendered_prompt
    first_progress = TaskPolicyProgressProjection(
        profile_id="research",
        profile_revision="v4",
        task_family="research",
        model_turns_used=1,
        model_turn_limit=8,
        tool_calls_used=2,
        tool_call_limit=12,
        completed_steps=1,
        total_steps=4,
    )
    second_progress = first_progress.model_copy(
        update={"model_turns_used": 2, "tool_calls_used": 4}
    )

    first = binding.prepare(
        system_message=SystemMessage(content=legacy),
        state={"runtime_prompt_approval": "pending"},
        tools=(),
        execution_scope="supervisor",
        task_policy_progress=first_progress,
    )
    second = binding.prepare(
        system_message=SystemMessage(content=legacy),
        state={"runtime_prompt_approval": "approved"},
        tools=(),
        execution_scope="supervisor",
        task_policy_progress=second_progress,
    )

    assert first.plan is not None
    assert second.plan is not None
    assert first.plan.rendered_digest != second.plan.rendered_digest
    assert "- Model turns: 1 used, 7 remaining" in first.plan.rendered_prompt
    assert "- Model turns: 2 used, 6 remaining" in second.plan.rendered_prompt
    assert "pending" in first.plan.rendered_prompt
    assert "approved" in second.plan.rendered_prompt


def test_final_tool_schema_revision_is_recomputed_per_call() -> None:
    binding = _binding(mode=FeatureMode.SHADOW)
    system = SystemMessage(content=_legacy_plan().rendered_prompt)

    first = binding.prepare(
        system_message=system,
        state={},
        tools=({"name": "search", "description": "Search.", "parameters": {}},),
        execution_scope="supervisor",
        task_policy_progress=None,
    )
    second = binding.prepare(
        system_message=system,
        state={},
        tools=(
            {"name": "search", "description": "Search.", "parameters": {}},
            {"name": "task", "description": "Delegate.", "parameters": {}},
        ),
        execution_scope="supervisor",
        task_policy_progress=None,
    )

    assert first.observation.tool_schema_revision != (
        second.observation.tool_schema_revision
    )


def test_provider_mapping_schema_revision_includes_nested_function_schema() -> None:
    binding = _binding(mode=FeatureMode.SHADOW)
    system = SystemMessage(content=_legacy_plan().rendered_prompt)

    def prepare(schema_type: str):
        return binding.prepare(
            system_message=system,
            state={},
            tools=(
                {
                    "type": "function",
                    "function": {
                        "name": "search",
                        "description": "Search.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": schema_type},
                            },
                        },
                    },
                },
            ),
            execution_scope="supervisor",
            task_policy_progress=None,
        )

    assert prepare("string").observation.tool_schema_revision != (
        prepare("integer").observation.tool_schema_revision
    )


@pytest.mark.parametrize(
    ("mode", "sent", "reason"),
    [
        (FeatureMode.OFF, False, "feature_off"),
        (FeatureMode.SHADOW, False, "shadow_legacy_render"),
        (FeatureMode.ENFORCE, True, "cache_controls_disabled"),
    ],
)
def test_release_modes_preserve_backout(
    mode: FeatureMode,
    sent: bool,
    reason: str,
) -> None:
    observer = _Observer()
    binding = _binding(mode=mode, observer=observer)
    system = SystemMessage(content=_legacy_plan().rendered_prompt)

    result = binding.prepare(
        system_message=system,
        state={},
        tools=(),
        execution_scope="supervisor",
        task_policy_progress=None,
    )
    binding.observe(result)

    assert result.observation.sent_assembled_prompt is sent
    assert result.observation.cache_reason_code == reason
    assert observer.observations == [result.observation]
    if not sent:
        assert result.system_message is system


def test_product_decoration_does_not_mutate_inbound_system_message() -> None:
    binding = _binding(
        mode=FeatureMode.ENFORCE,
        owner=ProviderCacheOwner.PRODUCT,
    )
    system = SystemMessage(
        content=_legacy_plan().rendered_prompt,
        additional_kwargs={"caller": {"unchanged": True}},
    )
    before = system.model_dump_json()

    result = binding.prepare(
        system_message=system,
        state={},
        tools=(),
        execution_scope="supervisor",
        task_policy_progress=None,
    )

    assert system.model_dump_json() == before
    assert result.system_message is not system
    assert result.observation.provider_cache_enabled


def test_shadow_assembly_failure_cannot_change_provider_request() -> None:
    baseline = _binding(mode=FeatureMode.SHADOW)
    binding = PromptRuntimeBinding(
        mode=baseline.mode,
        provider=baseline.provider,
        model_family=baseline.model_family,
        harness_revision=baseline.harness_revision,
        fragment_provider=_BrokenProvider(),
        cache_registry=baseline.cache_registry,
        cache_owner=baseline.cache_owner,
        framework_cache_installed=baseline.framework_cache_installed,
    )
    system = SystemMessage(content="Exact legacy prompt.")

    result = binding.prepare(
        system_message=system,
        state={},
        tools=(),
        execution_scope="supervisor",
        task_policy_progress=None,
    )

    assert result.system_message is system
    assert result.plan is None
    assert result.observation.cache_reason_code == "shadow_assembly_failed"
