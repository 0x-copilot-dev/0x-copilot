"""Pure, fail-closed policy resolution for a proposed external effect."""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.effects.contracts import (
    EffectPolicyResolution,
    EffectPolicySnapshot,
    ProposedEffect,
)
from agent_runtime.surfaces_v2.ledger_models import EffectClass, EffectPolicy


@dataclass(frozen=True)
class EffectStagePolicyResolver:
    """Resolve the most restrictive stage policy without I/O or mutable state."""

    def resolve(
        self,
        *,
        proposed_effect: ProposedEffect,
        snapshot: EffectPolicySnapshot,
    ) -> EffectPolicyResolution:
        """Return the immutable policy decision carried by the staged event.

        ``allow_always`` can only yield ``AUTO`` for a descriptor-known,
        non-sensitive external-reversible proposal.  Every explicit policy and every
        risk floor participates in a monotonic most-restrictive comparison.
        """

        candidates: list[tuple[EffectPolicy, str]] = []
        effect_class = proposed_effect.effect_class

        allow_always_eligible = (
            snapshot.allow_always
            and snapshot.descriptor_known
            and effect_class is EffectClass.EXTERNAL_REVERSIBLE
            and not snapshot.sensitive_target
            and not proposed_effect.agent_hold
        )

        if effect_class is EffectClass.EXTERNAL_DESTRUCTIVE:
            candidates.append((EffectPolicy.REQUIRE, "external_destructive"))
        elif effect_class is EffectClass.UNKNOWN:
            candidates.append((EffectPolicy.ASK, "unknown_effect"))
        elif effect_class is EffectClass.EXTERNAL_REVERSIBLE:
            if not allow_always_eligible:
                candidates.append((EffectPolicy.ASK, "external_reversible_default"))
        else:
            candidates.append((EffectPolicy.BLOCK, "not_external"))

        if proposed_effect.agent_hold:
            candidates.append((EffectPolicy.REQUIRE, "agent_hold"))
        if snapshot.sensitive_target:
            candidates.append((EffectPolicy.REQUIRE, "sensitive_target"))
        if not snapshot.descriptor_known:
            candidates.append((EffectPolicy.ASK, "descriptor_unknown"))

        named_policies = (
            (snapshot.deployment_policy, "deployment"),
            (snapshot.organization_policy, "organization"),
            (snapshot.grant_policy, "grant"),
            (snapshot.capability_policy, "capability"),
            (snapshot.user_policy, "user"),
        )
        for configured_policy, label in named_policies:
            if configured_policy is None:
                continue
            # ``AUTO`` is not a generic relaxation knob.  In A4 it only means the
            # explicit allow-always path for a known reversible descriptor; all other
            # attempted automatic settings degrade to an honest ask-first posture.
            if configured_policy is EffectPolicy.AUTO and not allow_always_eligible:
                candidates.append((EffectPolicy.ASK, f"{label}_auto_not_eligible"))
            else:
                candidates.append((configured_policy, label))

        if allow_always_eligible:
            candidates.append((EffectPolicy.AUTO, "allow_always"))

        policy_rank = {
            EffectPolicy.AUTO: 0,
            EffectPolicy.ASK: 1,
            EffectPolicy.REQUIRE: 2,
            EffectPolicy.BLOCK: 3,
        }
        policy, _ = max(candidates, key=lambda item: policy_rank[item[0]])
        reasons = tuple(label for candidate, label in candidates if candidate is policy)
        return EffectPolicyResolution(
            policy=policy,
            auto_approval_allowed=policy is EffectPolicy.AUTO and allow_always_eligible,
            reasons=reasons,
        )


__all__ = ["EffectStagePolicyResolver"]
