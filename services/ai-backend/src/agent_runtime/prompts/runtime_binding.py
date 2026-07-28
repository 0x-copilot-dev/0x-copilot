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
from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import SystemMessage
from pydantic import Field

from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.prompts.assembly import (
    PromptAssembler,
    PromptAssemblyPlan,
    PromptCacheEligibility,
    PromptFragment,
    PromptFragmentScope,
    PromptFragmentTier,
)
from agent_runtime.prompts.provider_cache import (
    ProviderCacheAdapterRegistry,
    ProviderCacheOwner,
    ProviderPromptDecoration,
)
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
            plan = PromptAssembler().assemble(self.fragment_provider.fragments(call))
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
                    tier=PromptFragmentTier.SYSTEM_POLICY,
                )
            ]

        progress = _render_task_policy_progress(call.task_policy_progress)
        if progress:
            fragments.append(
                self._run_fragment(
                    fragment_id="90_task_policy_progress",
                    revision="f4-progress-projection-v1",
                    content=progress,
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
            revision=revision,
            tier=tier,
            scope=PromptFragmentScope.RUN,
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


def _render_task_policy_progress(progress: object | None) -> str:
    if progress is None:
        return ""
    # Consume only the reviewed typed projection fields. Raw tool results,
    # arguments, user text, and evidence never enter this adapter.
    profile_id = str(getattr(progress, "profile_id", "")).strip()
    profile_revision = str(getattr(progress, "profile_revision", "")).strip()
    task_family = str(getattr(progress, "task_family", "")).strip()
    if not profile_id or not profile_revision or not task_family:
        raise RuntimeError("task-policy progress projection is incomplete")
    facts = {
        "profile_id": profile_id,
        "profile_revision": profile_revision,
        "task_family": task_family,
        "model_turns_used": int(getattr(progress, "model_turns_used", 0)),
        "model_turn_limit": getattr(progress, "model_turn_limit", None),
        "tool_calls_used": int(getattr(progress, "tool_calls_used", 0)),
        "tool_call_limit": getattr(progress, "tool_call_limit", None),
        "completed_steps": int(getattr(progress, "completed_steps", 0)),
        "total_steps": int(getattr(progress, "total_steps", 0)),
    }
    lines = [
        "Trusted runtime progress (current call):",
        *(f"- {key}: {value}" for key, value in facts.items()),
    ]
    return "\n".join(lines)


def _approval_projection(state: Mapping[str, object]) -> str:
    value = state.get("runtime_prompt_approval")
    if value not in {"pending", "approved", "rejected"}:
        return ""
    return f"Trusted approval state for the current call: {value}."


__all__ = (
    "FactoryPromptFragmentProvider",
    "PromptAssemblyObserverPort",
    "PromptFragmentProviderPort",
    "PromptRuntimeBinding",
    "PromptRuntimeCall",
    "PromptRuntimeObservation",
    "PromptRuntimeResult",
    "tool_schema_revision",
)
