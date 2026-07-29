"""Run-scoped, per-model-call prompt assembly.

The binding is installed only for the lifetime of a verified run and is read
by the graph-wide runtime middleware. It carries pure providers and adapters,
not a graph-global rendered prompt. Each invocation therefore sees the final
tool surface and the latest typed F4 progress without rebuilding the graph or
mutating checkpointed conversation messages.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from langchain_core.messages import SystemMessage
from pydantic import Field

from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.control_plane.context import TaskPolicyProgressProjection
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.prompts.assembly import (
    PromptAssemblyContext,
    PromptAssembler,
    PromptAssemblyPlan,
    PromptCacheEligibility,
    PromptFragment,
    PromptFragmentScope,
    PromptFragmentTier,
    PromptSensitivity,
    PromptTrustLabel,
)
from agent_runtime.prompts.provider_cache import (
    ProviderCacheAdapterRegistry,
    ProviderCacheOwner,
    ProviderCacheRejectionAdapterRegistry,
    ProviderPromptDecoration,
)
from agent_runtime.prompts.observation import (
    PromptAssembledRecord,
    PromptAssemblyObservationInput,
    PromptAssemblyObserver,
    PromptAssemblyOutcome,
    PromptAssemblyReasonCode,
    PromptCacheOwner,
    PromptCacheReasonCode,
    PromptFragmentTokenTotals,
)
from agent_runtime.observability.token_usage import NormalizedTokenUsage
from agent_runtime.prompts.sources import render_task_policy_progress
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


@dataclass(frozen=True, slots=True)
class PromptRuntimeCall:
    """Ephemeral provider input for one final graph model invocation."""

    provider: str
    model_family: str
    execution_scope: str
    harness_revision: str
    system_message: SystemMessage | None
    state: Mapping[str, object]
    tools: tuple[object, ...]
    tool_schema_revision: str
    task_policy_progress: object | None


class PromptFragmentProviderPort(Protocol):
    """Pure current-call fragment provider."""

    def assembly_context(self, call: PromptRuntimeCall) -> PromptAssemblyContext: ...

    def fragments(self, call: PromptRuntimeCall) -> Sequence[PromptFragment]: ...


class PromptAssemblyObserverPort(Protocol):
    """Body-free observer hook; persistence is supplied by the owning lane."""

    def observe(self, observation: "PromptRuntimeObservation") -> None: ...


class PromptRuntimeObservation(RuntimeContract):
    """Content-free model-seam observation."""

    mode: FeatureMode
    provider: str = Field(min_length=1, max_length=80)
    model_family: str = Field(min_length=1, max_length=200)
    execution_scope: str = Field(min_length=1, max_length=320)
    harness_revision: str = Field(min_length=1, max_length=160)
    tool_schema_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_revision: str | None = None
    rendered_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    stable_prefix_digest: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    cache_owner: ProviderCacheOwner = ProviderCacheOwner.NONE
    cache_adapter_ref: str | None = None
    cache_reason_code: str
    provider_cache_enabled: bool = False
    sent_assembled_prompt: bool = False


@dataclass(frozen=True, slots=True)
class PromptRuntimeResult:
    """Immutable output applied with ``ModelRequest.override``."""

    system_message: SystemMessage | None
    tools: tuple[object, ...]
    plan: PromptAssemblyPlan | None
    decoration: ProviderPromptDecoration | None
    observation: PromptRuntimeObservation


class PromptCacheRecordStatus(StrEnum):
    """Explicit outcome of post-response cache-observation persistence."""

    RECORDED = "recorded"
    NOT_CONFIGURED = "not_configured"
    PERSISTENCE_FAILED = "persistence_failed"


@dataclass(frozen=True, slots=True)
class PromptRuntimeBinding:
    """One release-controlled F2 binding inherited by local subagents."""

    mode: FeatureMode
    provider: str
    model_family: str
    harness_revision: str
    fragment_provider: PromptFragmentProviderPort
    cache_registry: ProviderCacheAdapterRegistry
    cache_owner: ProviderCacheOwner
    framework_cache_installed: bool
    observer: PromptAssemblyObserverPort | None = None
    observation_publisher: PromptAssemblyObserver | None = None
    cache_rejection_adapters: ProviderCacheRejectionAdapterRegistry = field(
        default_factory=ProviderCacheRejectionAdapterRegistry
    )

    def prepare(
        self,
        *,
        system_message: SystemMessage | None,
        state: Mapping[str, object],
        tools: Sequence[object],
        execution_scope: str,
        task_policy_progress: object | None,
        provider: str | None = None,
        model_family: str | None = None,
    ) -> PromptRuntimeResult:
        """Purely assemble one current provider call."""

        resolved_provider = (provider or self.provider).strip().lower()
        resolved_model = (model_family or self.model_family).strip()
        if not resolved_provider or not resolved_model:
            raise RuntimeError("prompt runtime provider/model identity is incomplete")
        final_tools = tuple(tools)
        schema_revision = tool_schema_revision(final_tools)
        call = PromptRuntimeCall(
            provider=resolved_provider,
            model_family=resolved_model,
            execution_scope=execution_scope,
            harness_revision=self.harness_revision,
            system_message=system_message,
            state=state,
            tools=final_tools,
            tool_schema_revision=schema_revision,
            task_policy_progress=task_policy_progress,
        )
        if self.mode is FeatureMode.OFF:
            observation = PromptRuntimeObservation(
                mode=self.mode,
                provider=resolved_provider,
                model_family=resolved_model,
                execution_scope=execution_scope,
                harness_revision=self.harness_revision,
                tool_schema_revision=schema_revision,
                cache_reason_code="feature_off",
            )
            return PromptRuntimeResult(
                system_message=system_message,
                tools=final_tools,
                plan=None,
                decoration=None,
                observation=observation,
            )

        try:
            plan = PromptAssembler(
                context=self.fragment_provider.assembly_context(call)
            ).assemble(self.fragment_provider.fragments(call))
        except Exception:  # noqa: BLE001 - shadow failures cannot affect dispatch
            if self.mode is not FeatureMode.SHADOW:
                raise
            observation = PromptRuntimeObservation(
                mode=self.mode,
                provider=resolved_provider,
                model_family=resolved_model,
                execution_scope=execution_scope,
                harness_revision=self.harness_revision,
                tool_schema_revision=schema_revision,
                cache_reason_code="shadow_assembly_failed",
            )
            return PromptRuntimeResult(
                system_message=system_message,
                tools=final_tools,
                plan=None,
                decoration=None,
                observation=observation,
            )
        if self.mode is FeatureMode.SHADOW:
            observation = PromptRuntimeObservation(
                mode=self.mode,
                provider=resolved_provider,
                model_family=resolved_model,
                execution_scope=execution_scope,
                harness_revision=self.harness_revision,
                tool_schema_revision=schema_revision,
                plan_revision=plan.plan_revision,
                rendered_digest=plan.rendered_digest,
                stable_prefix_digest=plan.stable_prefix_digest,
                cache_owner=ProviderCacheOwner.NONE,
                cache_reason_code="shadow_legacy_render",
            )
            return PromptRuntimeResult(
                system_message=system_message,
                tools=final_tools,
                plan=plan,
                decoration=None,
                observation=observation,
            )

        decoration = self.cache_registry.decorate(
            provider=resolved_provider,
            model_family=resolved_model,
            plan=plan,
            cache_owner=self.cache_owner,
            framework_cache_installed=self.framework_cache_installed,
        )
        outbound = _as_system_message(decoration.system_prompt)
        observation = PromptRuntimeObservation(
            mode=self.mode,
            provider=resolved_provider,
            model_family=resolved_model,
            execution_scope=execution_scope,
            harness_revision=self.harness_revision,
            tool_schema_revision=schema_revision,
            plan_revision=plan.plan_revision,
            rendered_digest=plan.rendered_digest,
            stable_prefix_digest=plan.stable_prefix_digest,
            cache_owner=decoration.cache_owner,
            cache_adapter_ref=decoration.adapter_ref,
            cache_reason_code=decoration.reason_code,
            provider_cache_enabled=decoration.provider_cache_enabled,
            sent_assembled_prompt=True,
        )
        return PromptRuntimeResult(
            system_message=outbound,
            tools=final_tools,
            plan=plan,
            decoration=decoration,
            observation=observation,
        )

    def observe(self, result: PromptRuntimeResult) -> None:
        """Publish only the body-free projection to the installed hook."""

        if self.observer is not None:
            self.observer.observe(result.observation)

    async def record_assembled(
        self,
        *,
        result: PromptRuntimeResult,
        model_call_id: str,
    ) -> PromptAssembledRecord | None:
        """Persist the canonical body-free assembly before provider dispatch."""

        try:
            self.observe(result)
            publisher = self.observation_publisher
            plan = result.plan
            if publisher is None or plan is None:
                return None
            sequenced = await publisher.record_assembled(
                PromptAssemblyObservationInput(
                    model_call_id=model_call_id,
                    plan_id=plan.plan_id,
                    plan_revision=plan.plan_revision,
                    plan_digest=plan.plan_digest,
                    provider=result.observation.provider,
                    model_family=result.observation.model_family,
                    complete_system_digest=plan.complete_system_digest,
                    stable_prefix_digest=plan.stable_prefix_digest,
                    fragment_count=len(plan.fragments),
                    stable_prefix_fragment_count=plan.stable_prefix_fragment_count,
                    system_bytes=plan.total_bytes,
                    estimated_input_tokens=plan.estimated_tokens,
                    fragment_tokens=_fragment_token_totals(plan),
                    cache_owner=PromptCacheOwner(result.observation.cache_owner.value),
                    outcome=_assembly_outcome(result.observation.mode),
                    reason_code=_assembly_reason(result.observation.mode),
                )
            )
        except Exception:
            if self.mode is FeatureMode.SHADOW:
                return None
            raise
        record = sequenced.record
        if not isinstance(record, PromptAssembledRecord):
            raise RuntimeError(
                "prompt assembly observer returned a non-assembly record"
            )
        return record

    async def record_cache(
        self,
        *,
        assembly: PromptAssembledRecord,
        usage: NormalizedTokenUsage,
        result: PromptRuntimeResult,
    ) -> PromptCacheRecordStatus:
        """Persist cache metadata without ever invalidating provider output.

        Assembly persistence is the pre-dispatch fail-closed boundary. Once a
        provider response exists, an observation-store failure leaves that
        durable assembly unpaired so replay and evaluation can detect the
        incomplete cache observation without causing a second model call.
        """

        publisher = self.observation_publisher
        if publisher is None:
            return PromptCacheRecordStatus.NOT_CONFIGURED
        try:
            await publisher.record_cache(
                assembly=assembly,
                usage=usage,
                reason_code=_cache_reason_override(result, usage=usage),
            )
        except Exception:
            return PromptCacheRecordStatus.PERSISTENCE_FAILED
        return PromptCacheRecordStatus.RECORDED


class FactoryPromptFragmentProvider:
    """Preserve the graph's current bytes while exposing per-call inputs.

    The factory's typed legacy plan owns the caller-supplied prefix. Deep Agents
    appends its SDK/profile instructions at graph construction; this provider
    attributes that suffix separately when it can prove the prefix. Child
    graphs with a different prompt are conservatively represented as one
    run-scoped, non-cacheable fragment.
    """

    def __init__(
        self,
        *,
        legacy_plan: PromptAssemblyPlan,
        run_scope_fingerprint: str,
    ) -> None:
        self._legacy_plan = legacy_plan
        self._run_scope_fingerprint = run_scope_fingerprint
        existing_run_fingerprints = {
            fragment.scope_fingerprint
            for fragment in legacy_plan.fragments
            if fragment.scope is PromptFragmentScope.RUN
        }
        if existing_run_fingerprints and existing_run_fingerprints != {
            run_scope_fingerprint
        }:
            raise ValueError("runtime prompt scope must match the verified legacy plan")

    def assembly_context(self, call: PromptRuntimeCall) -> PromptAssemblyContext:
        """Rebind route/tool revisions while preserving verified run authority."""

        return PromptAssemblyContext(
            provider=call.provider,
            model_family=call.model_family,
            harness_revision=call.harness_revision,
            capability_bridge_revision=self._legacy_plan.capability_bridge_revision,
            tool_schema_revision=call.tool_schema_revision,
            policy_revision=self._legacy_plan.policy_revision,
            authorization_revision=self._legacy_plan.authorization_revision,
            locked_task_profile=self._legacy_plan.locked_task_profile,
        )

    def fragments(self, call: PromptRuntimeCall) -> Sequence[PromptFragment]:
        system_text = _system_message_text(call.system_message)
        if not system_text:
            raise RuntimeError("effective model request has no system message")
        fragments: list[PromptFragment]
        prefix = self._legacy_plan.rendered_prompt
        if system_text == prefix or system_text.startswith(f"{prefix}\n\n"):
            fragments = list(self._legacy_plan.fragments)
            suffix = system_text[len(prefix) :].removeprefix("\n\n")
            if suffix:
                fragments.append(
                    self._run_fragment(
                        fragment_id="80_framework_harness",
                        revision=call.harness_revision,
                        content=suffix,
                    )
                )
        else:
            fragments = [
                self._run_fragment(
                    fragment_id="00_graph_effective_system",
                    revision=call.harness_revision,
                    content=system_text,
                )
            ]

        progress = _task_policy_progress(call.task_policy_progress)
        if progress is not None:
            fragments.append(
                self._run_fragment(
                    fragment_id="90_task_policy_progress",
                    revision=f"{progress.profile_id}:{progress.profile_revision}",
                    content=render_task_policy_progress(progress),
                )
            )
        approval = _approval_projection(call.state)
        if approval:
            fragments.append(
                self._run_fragment(
                    fragment_id="91_approval_state",
                    revision="approval-projection-v1",
                    content=approval,
                )
            )
        return tuple(fragments)

    def _run_fragment(
        self,
        *,
        fragment_id: str,
        revision: str,
        content: str,
        tier: PromptFragmentTier = PromptFragmentTier.CURRENT_TURN,
    ) -> PromptFragment:
        return PromptFragment(
            fragment_id=fragment_id,
            source_owner="agent_runtime.prompts.runtime_binding",
            source_revision=revision,
            tier=tier,
            source_scope=PromptFragmentScope.RUN,
            scope=PromptFragmentScope.RUN,
            sensitivity=PromptSensitivity.INTERNAL,
            trust=PromptTrustLabel.TRUSTED_RUNTIME,
            scope_fingerprint=self._run_scope_fingerprint,
            content=content,
            cache_eligibility=PromptCacheEligibility.NEVER,
        )


def tool_schema_revision(tools: Sequence[object]) -> str:
    """Digest the exact final model-visible schema surface in model order."""

    return canonical_json_sha256(
        {
            "schema_revision": "model-tool-surface-v1",
            "tools": [_tool_schema_fact(tool) for tool in tools],
        }
    )


def _tool_schema_fact(tool: object) -> Mapping[str, object]:
    if isinstance(tool, Mapping):
        return {
            "kind": "provider_mapping",
            "definition": _json_safe_schema(tool),
        }
    schema: object = {}
    input_schema = getattr(tool, "get_input_schema", None)
    if callable(input_schema):
        model = input_schema()
        model_json_schema = getattr(model, "model_json_schema", None)
        if callable(model_json_schema):
            schema = model_json_schema()
    if not schema:
        args_schema = getattr(tool, "args_schema", None)
        model_json_schema = getattr(args_schema, "model_json_schema", None)
        if callable(model_json_schema):
            schema = model_json_schema()
    return {
        "name": str(getattr(tool, "name", "")),
        "description": str(getattr(tool, "description", "")),
        "schema": _json_safe_schema(schema),
    }


def _json_safe_schema(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe_schema(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [_json_safe_schema(item) for item in value]
    raise RuntimeError("model-visible tool schema is not canonical JSON")


def _as_system_message(value: str | SystemMessage) -> SystemMessage:
    if isinstance(value, SystemMessage):
        return deepcopy(value)
    return SystemMessage(content=value)


def _system_message_text(message: SystemMessage | None) -> str:
    if message is None:
        return ""
    if isinstance(message.content, str):
        return message.content
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, Mapping) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
            else:
                raise RuntimeError("structured system text block is invalid")
        else:
            raise RuntimeError("unsupported structured system message block")
    return "\n\n".join(parts)


def _task_policy_progress(
    progress: object | None,
) -> TaskPolicyProgressProjection | None:
    if progress is None:
        return None
    # This validation is the authority boundary: raw tool results, arguments,
    # user text, and evidence cannot be coerced into the F4 prompt projection.
    return TaskPolicyProgressProjection.model_validate(progress)


def _approval_projection(state: Mapping[str, object]) -> str:
    value = state.get("runtime_prompt_approval")
    if value not in {"pending", "approved", "rejected"}:
        return ""
    return f"Trusted approval state for the current call: {value}."


def _fragment_token_totals(plan: PromptAssemblyPlan) -> PromptFragmentTokenTotals:
    totals = {item.tier: item.estimated_tokens for item in plan.totals_by_tier}
    return PromptFragmentTokenTotals(
        system_policy=totals.get(PromptFragmentTier.SYSTEM_POLICY, 0),
        stable=totals.get(PromptFragmentTier.STABLE, 0),
        contextual=totals.get(PromptFragmentTier.CONTEXTUAL, 0),
        volatile=totals.get(PromptFragmentTier.VOLATILE, 0),
        current_turn=totals.get(PromptFragmentTier.CURRENT_TURN, 0),
    )


def _assembly_outcome(mode: FeatureMode) -> PromptAssemblyOutcome:
    if mode is FeatureMode.ENFORCE:
        return PromptAssemblyOutcome.ENFORCED
    if mode is FeatureMode.SHADOW:
        return PromptAssemblyOutcome.SHADOW
    return PromptAssemblyOutcome.FEATURE_OFF


def _assembly_reason(mode: FeatureMode) -> PromptAssemblyReasonCode:
    if mode is FeatureMode.ENFORCE:
        return PromptAssemblyReasonCode.TYPED_PLAN_ENFORCED
    if mode is FeatureMode.SHADOW:
        return PromptAssemblyReasonCode.SHADOW_PLAN_ASSEMBLED
    return PromptAssemblyReasonCode.PROMPT_ASSEMBLY_DISABLED


def _cache_reason_override(
    result: PromptRuntimeResult,
    *,
    usage: NormalizedTokenUsage,
) -> PromptCacheReasonCode | None:
    if usage.provider_cache_metadata_observed:
        return None
    observation = result.observation
    if observation.cache_owner is ProviderCacheOwner.NONE:
        return PromptCacheReasonCode.DECORATION_DISABLED
    if observation.cache_reason_code in {
        "provider_or_model_unsupported",
        "model_not_qualified_for_explicit_cache_controls",
        "framework_cache_middleware_absent",
    }:
        return PromptCacheReasonCode.ADAPTER_UNSUPPORTED
    return None


__all__ = (
    "FactoryPromptFragmentProvider",
    "PromptAssemblyObserverPort",
    "PromptCacheRecordStatus",
    "PromptFragmentProviderPort",
    "PromptRuntimeBinding",
    "PromptRuntimeCall",
    "PromptRuntimeObservation",
    "PromptRuntimeResult",
    "tool_schema_revision",
)
