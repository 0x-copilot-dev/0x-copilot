"""Typed, deterministic system-prompt assembly (F2).

Prompt bodies stay in memory and travel only to the selected provider. Durable
diagnostics expose fragment IDs, revisions, tiers, and digests—not prompt text.
The assembly plan is provider-neutral; provider cache decoration is a separate
adapter so unsupported providers retain byte-identical behaviour.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Annotated, Sequence

from pydantic import Field, field_validator, model_validator

from agent_runtime.context.memory.token_budget import TokenBudgetEvaluator
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


class PromptFragmentTier(IntEnum):
    """Precedence-preserving assembly tiers, rendered in this exact order."""

    SYSTEM_POLICY = 10
    STABLE = 20
    CONTEXTUAL = 30
    VOLATILE = 40
    CURRENT_TURN = 50


class PromptFragmentScope(StrEnum):
    INSTALLATION = "installation"
    PROFILE = "profile"
    CONVERSATION = "conversation"
    RUN = "run"


class PromptCacheEligibility(StrEnum):
    NEVER = "never"
    STABLE_PREFIX = "stable_prefix"


class PromptFragment(RuntimeContract):
    """One attributable prompt contribution.

    ``scope_fingerprint`` is already a one-way digest supplied by the caller.
    It never contains a user, profile, authorization, or workspace identifier.
    """

    fragment_id: Annotated[str, Field(min_length=1, max_length=120)]
    revision: Annotated[str, Field(min_length=1, max_length=120)]
    tier: PromptFragmentTier
    scope: PromptFragmentScope
    content: Annotated[str, Field(min_length=1, max_length=200_000)]
    cache_eligibility: PromptCacheEligibility = PromptCacheEligibility.NEVER
    scope_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("content")
    @classmethod
    def _strip_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("content must not be blank")
        return normalized

    @model_validator(mode="after")
    def _validate_cache_scope(self) -> "PromptFragment":
        if self.cache_eligibility is PromptCacheEligibility.STABLE_PREFIX:
            if self.tier not in {
                PromptFragmentTier.SYSTEM_POLICY,
                PromptFragmentTier.STABLE,
            }:
                raise ValueError("only policy/stable fragments may be cacheable")
            if self.scope is not PromptFragmentScope.INSTALLATION:
                raise ValueError("cacheable fragments must be installation-scoped")
            if self.scope_fingerprint is not None:
                raise ValueError(
                    "cacheable installation fragment needs no scope fingerprint"
                )
        elif (
            self.scope is not PromptFragmentScope.INSTALLATION
            and not self.scope_fingerprint
        ):
            raise ValueError("non-installation fragments require a scope fingerprint")
        return self

    @property
    def content_digest(self) -> str:
        return canonical_json_sha256({"content": self.content})

    @property
    def estimated_tokens(self) -> int:
        return TokenBudgetEvaluator.estimate_tokens(self.content)

    def diagnostic(self) -> "PromptFragmentDiagnostic":
        return PromptFragmentDiagnostic(
            fragment_id=self.fragment_id,
            revision=self.revision,
            tier=self.tier,
            scope=self.scope,
            cache_eligibility=self.cache_eligibility,
            content_digest=self.content_digest,
            estimated_tokens=self.estimated_tokens,
        )


class PromptFragmentDiagnostic(RuntimeContract):
    """Safe fragment metadata suitable for F1 projection and local diagnostics."""

    fragment_id: str
    revision: str
    tier: PromptFragmentTier
    scope: PromptFragmentScope
    cache_eligibility: PromptCacheEligibility
    content_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    estimated_tokens: int = Field(ge=0)


class PromptAssemblyPlan(RuntimeContract):
    """Immutable rendered plan; prompt content is intentionally not serializable."""

    plan_revision: str = Field(min_length=1, max_length=120)
    fragments: tuple[PromptFragment, ...]
    rendered_prompt: str = Field(min_length=1, exclude=True, repr=False)
    rendered_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    stable_prefix_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    estimated_tokens: int = Field(ge=0)

    def diagnostic(self) -> dict[str, object]:
        """Return an explicitly content-free representation for telemetry."""

        return {
            "plan_revision": self.plan_revision,
            "rendered_digest": self.rendered_digest,
            "stable_prefix_digest": self.stable_prefix_digest,
            "estimated_tokens": self.estimated_tokens,
            "fragments": [
                fragment.diagnostic().model_dump(mode="json")
                for fragment in self.fragments
            ],
        }


class PromptAssembler:
    """Assemble typed fragments with deterministic precedence and validation."""

    def __init__(self, *, plan_revision: str = "f2-assembly-v1") -> None:
        if not plan_revision.strip():
            raise ValueError("plan_revision must be non-empty")
        self._plan_revision = plan_revision

    def assemble(self, fragments: Sequence[PromptFragment]) -> PromptAssemblyPlan:
        if not fragments:
            raise ValueError("at least one prompt fragment is required")
        ordered = tuple(
            sorted(
                fragments,
                key=lambda fragment: (
                    int(fragment.tier),
                    fragment.fragment_id,
                    fragment.revision,
                ),
            )
        )
        duplicate_ids = [
            fragment.fragment_id
            for index, fragment in enumerate(ordered[1:], start=1)
            if fragment.fragment_id == ordered[index - 1].fragment_id
        ]
        if duplicate_ids:
            raise ValueError("prompt fragment ids must be unique per plan")
        rendered = "\n\n".join(fragment.content for fragment in ordered)
        stable_prefix = tuple(
            fragment
            for fragment in ordered
            if fragment.cache_eligibility is PromptCacheEligibility.STABLE_PREFIX
        )
        return PromptAssemblyPlan(
            plan_revision=self._plan_revision,
            fragments=ordered,
            rendered_prompt=rendered,
            rendered_digest=canonical_json_sha256({"prompt": rendered}),
            stable_prefix_digest=(
                canonical_json_sha256(
                    {
                        "prompt": "\n\n".join(
                            fragment.content for fragment in stable_prefix
                        )
                    }
                )
                if stable_prefix
                else None
            ),
            estimated_tokens=TokenBudgetEvaluator.estimate_tokens(rendered),
        )


__all__ = [
    "PromptAssembler",
    "PromptAssemblyPlan",
    "PromptCacheEligibility",
    "PromptFragment",
    "PromptFragmentDiagnostic",
    "PromptFragmentScope",
    "PromptFragmentTier",
]
