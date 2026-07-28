"""Typed, deterministic system-prompt assembly (F2).

Prompt bodies exist only in memory and are excluded at the fragment boundary,
not merely at the outer plan.  Consequently every ordinary Pydantic dump of a
fragment or plan is safe for diagnostics and persistence.  Provider transport
decoration is deliberately outside this module.
"""

from __future__ import annotations

from collections import defaultdict
from enum import IntEnum, StrEnum
import hashlib
import re
from typing import Annotated, Sequence

from pydantic import Field, field_validator

from agent_runtime.context.memory.token_budget import TokenBudgetEvaluator
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256

_DIGEST_PATTERN = r"^[a-f0-9]{64}$"
_SLUG_PATTERN = re.compile(r"[^a-z0-9.+-]+")
_SCOPE_RANK: dict["PromptFragmentScope", int] = {}


class PromptFragmentTier(IntEnum):
    """Precedence-preserving assembly tiers, rendered in this exact order."""

    SYSTEM_POLICY = 10
    STABLE = 20
    CONTEXTUAL = 30
    VOLATILE = 40
    CURRENT_TURN = 50


class PromptFragmentScope(StrEnum):
    """Smallest identity boundary at which fragment bytes remain valid."""

    INSTALLATION = "installation"
    PROFILE = "profile"
    USER = "user"
    PROJECT = "project"
    CONVERSATION = "conversation"
    RUN = "run"


_SCOPE_RANK.update(
    {
        PromptFragmentScope.INSTALLATION: 0,
        PromptFragmentScope.PROFILE: 1,
        PromptFragmentScope.USER: 2,
        PromptFragmentScope.PROJECT: 3,
        PromptFragmentScope.CONVERSATION: 4,
        PromptFragmentScope.RUN: 5,
    }
)


class PromptCacheEligibility(StrEnum):
    """Provider-neutral cache intent; adapters decide transport support."""

    NEVER = "never"
    STABLE_PREFIX = "stable_prefix"


class PromptSensitivity(StrEnum):
    """Closed data classification for prompt material."""

    PUBLIC = "public"
    INTERNAL = "internal"
    PERSONAL = "personal"
    CONFIDENTIAL = "confidential"
    SECRET = "secret"


class PromptTrustLabel(StrEnum):
    """Whether prompt bytes may carry authority."""

    IMMUTABLE_POLICY = "immutable_policy"
    TRUSTED_RUNTIME = "trusted_runtime"
    UNTRUSTED_RETRIEVED = "untrusted_retrieved"
    UNTRUSTED_USER = "untrusted_user"


class PromptAssemblyFailureReason(StrEnum):
    """Stable, content-free failure reasons emitted before provider dispatch."""

    EMPTY_FRAGMENT_SET = "empty_fragment_set"
    DUPLICATE_FRAGMENT_ID = "duplicate_fragment_id"
    SOURCE_SCOPE_BROADENED = "source_scope_broadened"
    SECRET_FRAGMENT_FORBIDDEN = "secret_fragment_forbidden"
    MUTABLE_FRAGMENT_CACHEABLE = "mutable_fragment_cacheable"
    UNTRUSTED_FRAGMENT_CACHEABLE = "untrusted_fragment_cacheable"
    MISSING_SCOPE_FINGERPRINT = "missing_scope_fingerprint"
    INSTALLATION_SCOPE_FINGERPRINT_FORBIDDEN = (
        "installation_scope_fingerprint_forbidden"
    )
    CONFLICTING_SCOPE_FINGERPRINT = "conflicting_scope_fingerprint"
    NON_CONTIGUOUS_STABLE_PREFIX = "non_contiguous_stable_prefix"
    SYSTEM_POLICY_NOT_IMMUTABLE = "system_policy_not_immutable"
    UNTRUSTED_BEFORE_POLICY = "untrusted_before_policy"


class PromptAssemblyValidationError(ValueError):
    """Fail-closed assembly error carrying only a reviewed reason code."""

    def __init__(self, reason: PromptAssemblyFailureReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def normalize_prompt_route(value: str, *, field_name: str) -> str:
    """Return a provider-neutral normalized provider/model-family identifier."""

    normalized = _SLUG_PATTERN.sub("-", value.strip().lower()).strip("-")
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized


def _sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class LockedTaskProfile(RuntimeContract):
    """Immutable F4 task-family binding allowed to affect a reusable prefix."""

    task_family: Annotated[str, Field(min_length=1, max_length=80)]
    profile_revision: Annotated[str, Field(min_length=1, max_length=160)]
    lock_revision: Annotated[str, Field(min_length=1, max_length=160)]


class PromptAssemblyContext(RuntimeContract):
    """All semantic and authority revisions authenticated by a prompt plan."""

    provider: Annotated[str, Field(min_length=1, max_length=120)]
    model_family: Annotated[str, Field(min_length=1, max_length=200)]
    harness_revision: Annotated[str, Field(min_length=1, max_length=160)]
    capability_bridge_revision: Annotated[str, Field(min_length=1, max_length=160)]
    tool_schema_revision: Annotated[str, Field(min_length=1, max_length=160)]
    policy_revision: Annotated[str, Field(min_length=1, max_length=160)]
    authorization_revision: Annotated[str, Field(min_length=1, max_length=160)]
    locked_task_profile: LockedTaskProfile | None = None

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        return normalize_prompt_route(value, field_name="provider")

    @field_validator("model_family")
    @classmethod
    def _normalize_model_family(cls, value: str) -> str:
        return normalize_prompt_route(value, field_name="model_family")


class PromptFragment(RuntimeContract):
    """One attributable prompt contribution.

    ``content`` is excluded and hidden at this innermost boundary.  The
    ``content_digest`` is computed exactly once while validating the fragment;
    callers may supply it only when it matches the actual normalized bytes.
    """

    fragment_id: Annotated[str, Field(min_length=1, max_length=120)]
    source_owner: Annotated[str, Field(min_length=1, max_length=120)]
    source_revision: Annotated[str, Field(min_length=1, max_length=160)]
    tier: PromptFragmentTier
    source_scope: PromptFragmentScope
    scope: PromptFragmentScope
    sensitivity: PromptSensitivity
    trust: PromptTrustLabel
    content: Annotated[
        str,
        Field(min_length=1, max_length=200_000, exclude=True, repr=False),
    ]
    cache_eligibility: PromptCacheEligibility
    scope_fingerprint: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    content_digest: str = Field(default="", pattern=_DIGEST_PATTERN)
    byte_count: int = Field(default=0, ge=1)
    estimated_tokens: int = Field(default=0, ge=1)

    @field_validator("content")
    @classmethod
    def _reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value

    def model_post_init(self, __context: object) -> None:
        """Seal body-derived metadata once without re-hashing on diagnostics."""

        del __context
        body = self.content.encode("utf-8")
        digest = _sha256_hex(body)
        if self.content_digest and self.content_digest != digest:
            raise ValueError("content_digest does not match content")
        expected_bytes = len(body)
        if self.byte_count and self.byte_count != expected_bytes:
            raise ValueError("byte_count does not match content")
        expected_tokens = TokenBudgetEvaluator.estimate_tokens(self.content)
        if self.estimated_tokens and self.estimated_tokens != expected_tokens:
            raise ValueError("estimated_tokens does not match content")
        object.__setattr__(self, "content_digest", digest)
        object.__setattr__(self, "byte_count", expected_bytes)
        object.__setattr__(self, "estimated_tokens", expected_tokens)

    @property
    def revision(self) -> str:
        """Temporary read-only alias for pre-F2 consumers."""

        return self.source_revision

    def diagnostic(self) -> "PromptFragmentDiagnostic":
        return PromptFragmentDiagnostic(
            fragment_id=self.fragment_id,
            source_owner=self.source_owner,
            source_revision=self.source_revision,
            tier=self.tier,
            source_scope=self.source_scope,
            scope=self.scope,
            sensitivity=self.sensitivity,
            trust=self.trust,
            cache_eligibility=self.cache_eligibility,
            scope_fingerprint=self.scope_fingerprint,
            content_digest=self.content_digest,
            byte_count=self.byte_count,
            estimated_tokens=self.estimated_tokens,
        )


class PromptFragmentDiagnostic(RuntimeContract):
    """Safe fragment metadata suitable for F1 and local diagnostics."""

    fragment_id: str
    source_owner: str
    source_revision: str
    tier: PromptFragmentTier
    source_scope: PromptFragmentScope
    scope: PromptFragmentScope
    sensitivity: PromptSensitivity
    trust: PromptTrustLabel
    cache_eligibility: PromptCacheEligibility
    scope_fingerprint: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    content_digest: str = Field(pattern=_DIGEST_PATTERN)
    byte_count: int = Field(ge=1)
    estimated_tokens: int = Field(ge=1)


class PromptTierTotals(RuntimeContract):
    """Deterministic body-free size totals for one precedence tier."""

    tier: PromptFragmentTier
    fragment_count: int = Field(ge=1)
    byte_count: int = Field(ge=1)
    estimated_tokens: int = Field(ge=1)


class PromptAssemblyPlan(RuntimeContract):
    """Immutable rendered plan whose ordinary serialization is body-free."""

    plan_id: Annotated[str, Field(min_length=1, max_length=96)]
    plan_revision: Annotated[str, Field(min_length=1, max_length=120)]
    plan_digest: str = Field(pattern=_DIGEST_PATTERN)
    provider: str
    model_family: str
    harness_revision: str
    capability_bridge_revision: str
    tool_schema_revision: str
    policy_revision: str
    authorization_revision: str
    locked_task_profile: LockedTaskProfile | None = None
    fragments: tuple[PromptFragment, ...]
    rendered_prompt: str = Field(min_length=1, exclude=True, repr=False)
    complete_system_digest: str = Field(pattern=_DIGEST_PATTERN)
    stable_prefix_digest: str | None = Field(default=None, pattern=_DIGEST_PATTERN)
    stable_prefix_fragment_count: int = Field(ge=0)
    total_bytes: int = Field(ge=1)
    estimated_tokens: int = Field(ge=1)
    totals_by_tier: tuple[PromptTierTotals, ...]

    @property
    def rendered_digest(self) -> str:
        """Temporary alias for the former complete-prompt digest name."""

        return self.complete_system_digest

    def diagnostic(self) -> dict[str, object]:
        """Return the complete, explicitly content-free diagnostic."""

        return self.model_dump(mode="json")


class PromptAssembler:
    """Assemble typed fragments with one validation and hashing pass."""

    def __init__(
        self,
        *,
        context: PromptAssemblyContext,
        plan_revision: str = "f2-assembly-v2",
    ) -> None:
        if not plan_revision.strip():
            raise ValueError("plan_revision must be non-empty")
        self._context = context
        self._plan_revision = plan_revision.strip()

    def assemble(self, fragments: Sequence[PromptFragment]) -> PromptAssemblyPlan:
        """Return a deterministic plan in ``O(body bytes + n log n)`` time.

        Sorting only body-free keys is ``O(n log n)``.  Each prompt body is
        hashed once by ``PromptFragment`` and joined once here; all plan
        digests operate exclusively on the cached content digests.
        """

        if not fragments:
            raise PromptAssemblyValidationError(
                PromptAssemblyFailureReason.EMPTY_FRAGMENT_SET
            )
        ordered = tuple(
            sorted(
                fragments,
                key=lambda fragment: (
                    int(fragment.tier),
                    fragment.fragment_id,
                    fragment.source_revision,
                ),
            )
        )
        self._validate(ordered)
        rendered = "\n\n".join(fragment.content for fragment in ordered)
        complete_system_digest = _sha256_hex(rendered.encode("utf-8"))
        stable_count = self._stable_prefix_count(ordered)
        authority = self._authority_digest_input()
        stable_prefix_digest = (
            canonical_json_sha256(
                {
                    "authority": authority,
                    "fragments": [
                        self._fragment_digest_input(fragment)
                        for fragment in ordered[:stable_count]
                    ],
                }
            )
            if stable_count
            else None
        )
        totals = self._tier_totals(ordered)
        diagnostic_fragments = [
            self._fragment_digest_input(fragment) for fragment in ordered
        ]
        plan_digest = canonical_json_sha256(
            {
                "plan_revision": self._plan_revision,
                "authority": authority,
                "fragments": diagnostic_fragments,
                "complete_system_digest": complete_system_digest,
                "stable_prefix_digest": stable_prefix_digest,
                "stable_prefix_fragment_count": stable_count,
            }
        )
        return PromptAssemblyPlan(
            plan_id=f"prompt-plan:{plan_digest[:48]}",
            plan_revision=self._plan_revision,
            plan_digest=plan_digest,
            provider=self._context.provider,
            model_family=self._context.model_family,
            harness_revision=self._context.harness_revision,
            capability_bridge_revision=self._context.capability_bridge_revision,
            tool_schema_revision=self._context.tool_schema_revision,
            policy_revision=self._context.policy_revision,
            authorization_revision=self._context.authorization_revision,
            locked_task_profile=self._context.locked_task_profile,
            fragments=ordered,
            rendered_prompt=rendered,
            complete_system_digest=complete_system_digest,
            stable_prefix_digest=stable_prefix_digest,
            stable_prefix_fragment_count=stable_count,
            total_bytes=len(rendered.encode("utf-8")),
            estimated_tokens=TokenBudgetEvaluator.estimate_tokens(rendered),
            totals_by_tier=totals,
        )

    @staticmethod
    def _validate(ordered: tuple[PromptFragment, ...]) -> None:
        seen_ids: set[str] = set()
        scope_fingerprints: dict[PromptFragmentScope, str] = {}
        first_mutable_seen = False
        policy_seen = False
        for fragment in ordered:
            if fragment.fragment_id in seen_ids:
                raise PromptAssemblyValidationError(
                    PromptAssemblyFailureReason.DUPLICATE_FRAGMENT_ID
                )
            seen_ids.add(fragment.fragment_id)
            if _SCOPE_RANK[fragment.scope] < _SCOPE_RANK[fragment.source_scope]:
                raise PromptAssemblyValidationError(
                    PromptAssemblyFailureReason.SOURCE_SCOPE_BROADENED
                )
            if fragment.sensitivity is PromptSensitivity.SECRET:
                raise PromptAssemblyValidationError(
                    PromptAssemblyFailureReason.SECRET_FRAGMENT_FORBIDDEN
                )
            if fragment.scope is PromptFragmentScope.INSTALLATION:
                if fragment.scope_fingerprint is not None:
                    raise PromptAssemblyValidationError(
                        PromptAssemblyFailureReason.INSTALLATION_SCOPE_FINGERPRINT_FORBIDDEN
                    )
            elif fragment.scope_fingerprint is None:
                raise PromptAssemblyValidationError(
                    PromptAssemblyFailureReason.MISSING_SCOPE_FINGERPRINT
                )
            else:
                existing = scope_fingerprints.setdefault(
                    fragment.scope, fragment.scope_fingerprint
                )
                if existing != fragment.scope_fingerprint:
                    raise PromptAssemblyValidationError(
                        PromptAssemblyFailureReason.CONFLICTING_SCOPE_FINGERPRINT
                    )
            if fragment.tier is PromptFragmentTier.SYSTEM_POLICY:
                if fragment.trust is not PromptTrustLabel.IMMUTABLE_POLICY:
                    raise PromptAssemblyValidationError(
                        PromptAssemblyFailureReason.SYSTEM_POLICY_NOT_IMMUTABLE
                    )
                policy_seen = True
            elif (
                fragment.trust
                in {
                    PromptTrustLabel.UNTRUSTED_RETRIEVED,
                    PromptTrustLabel.UNTRUSTED_USER,
                }
                and not policy_seen
            ):
                raise PromptAssemblyValidationError(
                    PromptAssemblyFailureReason.UNTRUSTED_BEFORE_POLICY
                )
            cacheable = (
                fragment.cache_eligibility is PromptCacheEligibility.STABLE_PREFIX
            )
            if cacheable:
                if fragment.tier not in {
                    PromptFragmentTier.SYSTEM_POLICY,
                    PromptFragmentTier.STABLE,
                }:
                    raise PromptAssemblyValidationError(
                        PromptAssemblyFailureReason.MUTABLE_FRAGMENT_CACHEABLE
                    )
                if fragment.trust not in {
                    PromptTrustLabel.IMMUTABLE_POLICY,
                    PromptTrustLabel.TRUSTED_RUNTIME,
                }:
                    raise PromptAssemblyValidationError(
                        PromptAssemblyFailureReason.UNTRUSTED_FRAGMENT_CACHEABLE
                    )
                if first_mutable_seen:
                    raise PromptAssemblyValidationError(
                        PromptAssemblyFailureReason.NON_CONTIGUOUS_STABLE_PREFIX
                    )
            else:
                first_mutable_seen = True

    @staticmethod
    def _stable_prefix_count(ordered: tuple[PromptFragment, ...]) -> int:
        count = 0
        for fragment in ordered:
            if fragment.cache_eligibility is not PromptCacheEligibility.STABLE_PREFIX:
                break
            count += 1
        return count

    def _authority_digest_input(self) -> dict[str, object]:
        context = self._context
        return {
            "provider": context.provider,
            "model_family": context.model_family,
            "harness_revision": context.harness_revision,
            "capability_bridge_revision": context.capability_bridge_revision,
            "tool_schema_revision": context.tool_schema_revision,
            "policy_revision": context.policy_revision,
            "authorization_revision": context.authorization_revision,
            "locked_task_profile": (
                context.locked_task_profile.model_dump(mode="json")
                if context.locked_task_profile is not None
                else None
            ),
        }

    @staticmethod
    def _fragment_digest_input(fragment: PromptFragment) -> dict[str, object]:
        return fragment.diagnostic().model_dump(mode="json")

    @staticmethod
    def _tier_totals(
        ordered: tuple[PromptFragment, ...],
    ) -> tuple[PromptTierTotals, ...]:
        by_tier: dict[PromptFragmentTier, list[int]] = defaultdict(lambda: [0, 0, 0])
        for fragment in ordered:
            values = by_tier[fragment.tier]
            values[0] += 1
            values[1] += fragment.byte_count
            values[2] += fragment.estimated_tokens
        return tuple(
            PromptTierTotals(
                tier=tier,
                fragment_count=values[0],
                byte_count=values[1],
                estimated_tokens=values[2],
            )
            for tier, values in sorted(by_tier.items(), key=lambda item: int(item[0]))
        )


__all__ = [
    "LockedTaskProfile",
    "PromptAssembler",
    "PromptAssemblyContext",
    "PromptAssemblyFailureReason",
    "PromptAssemblyPlan",
    "PromptAssemblyValidationError",
    "PromptCacheEligibility",
    "PromptFragment",
    "PromptFragmentDiagnostic",
    "PromptFragmentScope",
    "PromptFragmentTier",
    "PromptSensitivity",
    "PromptTierTotals",
    "PromptTrustLabel",
    "normalize_prompt_route",
]
