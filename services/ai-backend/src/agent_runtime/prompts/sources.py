"""Attributable system-prompt sources and deterministic provider registry.

The registry converts each model-facing system source into exactly one typed
fragment.  Source renderers may live with the subsystem that owns the bytes;
this module owns their precedence, classification, and assembly boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import Field, field_validator

from agent_runtime.control_plane.context import TaskPolicyProgressProjection
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.prompts.assembly import (
    PromptAssembler,
    PromptAssemblyContext,
    PromptAssemblyPlan,
    PromptCacheEligibility,
    PromptFragment,
    PromptFragmentScope,
    PromptFragmentTier,
    PromptSensitivity,
    PromptTrustLabel,
)


class PromptSource(StrEnum):
    """Closed set of system sources at the current runtime seam."""

    BASE_RUNTIME_SAFETY = "base_runtime_safety"
    APPLICATION_BOUNDARY = "application_boundary"
    OPERATION_TOOL_PROTOCOL = "operation_tool_protocol"
    CAPABILITY_DISCOVERY_PROTOCOL = "capability_discovery_protocol"
    MCP_CARDS = "mcp_cards"
    SKILL_CARDS = "skill_cards"
    SUGGESTED_CONNECTORS = "suggested_connectors"
    WORKSPACE_GUIDANCE = "workspace_guidance"
    CAPABILITY_GUIDANCE = "capability_guidance"
    APPROVAL_RUN_STATE = "approval_run_state"
    TASK_POLICY_PROGRESS = "task_policy_progress"


class PromptSourceMaterial(RuntimeContract):
    """Source-owned bytes plus their immutable classification."""

    source_owner: str = Field(min_length=1, max_length=120)
    source_revision: str = Field(min_length=1, max_length=160)
    source_scope: PromptFragmentScope
    scope: PromptFragmentScope
    sensitivity: PromptSensitivity
    trust: PromptTrustLabel
    cache_eligibility: PromptCacheEligibility
    scope_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    content: str = Field(
        min_length=1,
        max_length=200_000,
        exclude=True,
        repr=False,
    )

    @field_validator("content")
    @classmethod
    def _reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt source content must not be blank")
        return value


class PromptAssemblyInputs(RuntimeContract):
    """Complete typed input surface for one effective system prompt."""

    context: PromptAssemblyContext
    base_runtime_safety: PromptSourceMaterial
    application_boundary: PromptSourceMaterial
    operation_tool_protocol: PromptSourceMaterial | None = None
    capability_discovery_protocol: PromptSourceMaterial | None = None
    mcp_cards: PromptSourceMaterial | None = None
    skill_cards: PromptSourceMaterial | None = None
    suggested_connectors: PromptSourceMaterial | None = None
    workspace_guidance: PromptSourceMaterial | None = None
    capability_guidance: PromptSourceMaterial | None = None
    approval_run_state: PromptSourceMaterial | None = None
    task_policy_progress: PromptSourceMaterial | None = None

    def material_for(self, source: PromptSource) -> PromptSourceMaterial | None:
        """Return a source without accepting open-ended dynamic fields."""

        return getattr(self, source.value)


class PromptFragmentProvider(Protocol):
    """One source-to-fragment conversion owned by the assembly registry."""

    source: PromptSource
    fragment_id: str
    tier: PromptFragmentTier

    def fragments(
        self,
        inputs: PromptAssemblyInputs,
    ) -> tuple[PromptFragment, ...]: ...


@dataclass(frozen=True, slots=True)
class RegisteredPromptFragmentProvider:
    """Declarative provider for one closed ``PromptAssemblyInputs`` field."""

    source: PromptSource
    fragment_id: str
    tier: PromptFragmentTier

    def fragments(
        self,
        inputs: PromptAssemblyInputs,
    ) -> tuple[PromptFragment, ...]:
        material = inputs.material_for(self.source)
        if material is None or not material.content.strip():
            return ()
        return (
            PromptFragment(
                fragment_id=self.fragment_id,
                source_owner=material.source_owner,
                source_revision=material.source_revision,
                tier=self.tier,
                source_scope=material.source_scope,
                scope=material.scope,
                sensitivity=material.sensitivity,
                trust=material.trust,
                content=material.content,
                cache_eligibility=material.cache_eligibility,
                scope_fingerprint=material.scope_fingerprint,
            ),
        )


class PromptFragmentProviderRegistry:
    """Closed deterministic source registry, independent of registration order."""

    def __init__(self, providers: tuple[PromptFragmentProvider, ...]) -> None:
        if not providers:
            raise ValueError("prompt fragment provider registry must not be empty")
        ordered = tuple(
            sorted(
                providers,
                key=lambda provider: (
                    int(provider.tier),
                    provider.fragment_id,
                    provider.source.value,
                ),
            )
        )
        sources = [provider.source for provider in ordered]
        fragment_ids = [provider.fragment_id for provider in ordered]
        if len(sources) != len(set(sources)):
            raise ValueError("prompt source providers must be unique")
        if len(fragment_ids) != len(set(fragment_ids)):
            raise ValueError("prompt provider fragment ids must be unique")
        self._providers = ordered

    @property
    def providers(self) -> tuple[PromptFragmentProvider, ...]:
        return self._providers

    def fragments(
        self,
        inputs: PromptAssemblyInputs,
    ) -> tuple[PromptFragment, ...]:
        return tuple(
            fragment
            for provider in self._providers
            for fragment in provider.fragments(inputs)
        )

    def assemble(self, inputs: PromptAssemblyInputs) -> PromptAssemblyPlan:
        return PromptAssembler(context=inputs.context).assemble(self.fragments(inputs))


DEFAULT_PROMPT_FRAGMENT_PROVIDERS = PromptFragmentProviderRegistry(
    (
        RegisteredPromptFragmentProvider(
            source=PromptSource.BASE_RUNTIME_SAFETY,
            fragment_id="00_base_runtime",
            tier=PromptFragmentTier.SYSTEM_POLICY,
        ),
        RegisteredPromptFragmentProvider(
            source=PromptSource.APPLICATION_BOUNDARY,
            fragment_id="10_application_context_boundary",
            tier=PromptFragmentTier.STABLE,
        ),
        RegisteredPromptFragmentProvider(
            source=PromptSource.OPERATION_TOOL_PROTOCOL,
            fragment_id="15_operation_tool_protocol",
            tier=PromptFragmentTier.STABLE,
        ),
        # Rendered where the MCP card block would be, and mutually exclusive
        # with it: F3's ``deferred`` posture replaces a per-server enumeration
        # with one static protocol paragraph. It is a STABLE-tier source rather
        # than a CONTEXTUAL one because its bytes carry no subject data and do
        # not vary by connector, which is exactly what lets it join the
        # cacheable stable prefix that the card block can never be part of.
        RegisteredPromptFragmentProvider(
            source=PromptSource.CAPABILITY_DISCOVERY_PROTOCOL,
            fragment_id="16_capability_discovery_protocol",
            tier=PromptFragmentTier.STABLE,
        ),
        RegisteredPromptFragmentProvider(
            source=PromptSource.MCP_CARDS,
            fragment_id="20_mcp_cards",
            tier=PromptFragmentTier.CONTEXTUAL,
        ),
        RegisteredPromptFragmentProvider(
            source=PromptSource.SKILL_CARDS,
            fragment_id="30_skill_cards",
            tier=PromptFragmentTier.CONTEXTUAL,
        ),
        RegisteredPromptFragmentProvider(
            source=PromptSource.SUGGESTED_CONNECTORS,
            fragment_id="40_suggested_connectors",
            tier=PromptFragmentTier.CONTEXTUAL,
        ),
        RegisteredPromptFragmentProvider(
            source=PromptSource.WORKSPACE_GUIDANCE,
            fragment_id="50_workspace_guidance",
            tier=PromptFragmentTier.VOLATILE,
        ),
        RegisteredPromptFragmentProvider(
            source=PromptSource.CAPABILITY_GUIDANCE,
            fragment_id="60_capability_guidance",
            tier=PromptFragmentTier.VOLATILE,
        ),
        RegisteredPromptFragmentProvider(
            source=PromptSource.APPROVAL_RUN_STATE,
            fragment_id="70_approval_run_state",
            tier=PromptFragmentTier.VOLATILE,
        ),
        RegisteredPromptFragmentProvider(
            source=PromptSource.TASK_POLICY_PROGRESS,
            fragment_id="80_task_policy_progress",
            tier=PromptFragmentTier.VOLATILE,
        ),
    )
)


def render_task_policy_progress(progress: TaskPolicyProgressProjection) -> str:
    """Render the sole typed F4 handoff in stable, bounded order."""

    lines = [
        "## Current task progress and limits",
        "",
        f"- Task family: {progress.task_family}",
        (
            "- Plan steps completed: "
            f"{progress.completed_steps} of {progress.total_steps}"
        ),
        _usage_line(
            label="Model turns",
            used=progress.model_turns_used,
            limit=progress.model_turn_limit,
        ),
        _usage_line(
            label="Tool calls",
            used=progress.tool_calls_used,
            limit=progress.tool_call_limit,
        ),
        _usage_line(
            label="Cost (micro-USD)",
            used=progress.cost_microusd_used,
            limit=progress.cost_microusd_limit,
        ),
    ]
    for capability in sorted(
        progress.capabilities,
        key=lambda item: item.capability_id,
    ):
        lines.append(
            _usage_line(
                label=f"Capability {capability.capability_id}",
                used=capability.tool_calls_used,
                limit=capability.tool_call_limit,
            )
        )
    lines.extend(
        (
            "",
            "Respect the remaining limits. Do not repeat completed work or "
            "restart the plan after a resume.",
        )
    )
    return "\n".join(lines)


def task_policy_progress_material(
    *,
    progress: TaskPolicyProgressProjection,
    scope_fingerprint: str,
) -> PromptSourceMaterial:
    """Convert F4 progress to an attributable run-scoped source."""

    return PromptSourceMaterial(
        source_owner="agent_runtime.capabilities.task_policy",
        source_revision=f"{progress.profile_id}:{progress.profile_revision}",
        source_scope=PromptFragmentScope.RUN,
        scope=PromptFragmentScope.RUN,
        sensitivity=PromptSensitivity.INTERNAL,
        trust=PromptTrustLabel.TRUSTED_RUNTIME,
        cache_eligibility=PromptCacheEligibility.NEVER,
        scope_fingerprint=scope_fingerprint,
        content=render_task_policy_progress(progress),
    )


def _usage_line(*, label: str, used: int, limit: int | None) -> str:
    return f"- {label}: {used} used" + (
        "" if limit is None else f", {max(limit - used, 0)} remaining"
    )


__all__ = [
    "DEFAULT_PROMPT_FRAGMENT_PROVIDERS",
    "PromptAssemblyInputs",
    "PromptFragmentProvider",
    "PromptFragmentProviderRegistry",
    "PromptSource",
    "PromptSourceMaterial",
    "RegisteredPromptFragmentProvider",
    "render_task_policy_progress",
    "task_policy_progress_material",
]
