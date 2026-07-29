"""The declared promotion cohort matrix Step 15 evaluates.

PRD §11 Step 8 work item 12 makes this a *Step 8* deliverable on purpose: Step 15
runs "F1 paired comparisons for every enforcement feature and for each
configuration in the cohort matrix declared in Step 8", and the sentence after it
says the matrix "is an input to this step, not a judgement made during it".  A
matrix discovered at promotion time would let whoever runs the promotion pick the
combinations that pass.

**Why this is Python and not JSON or prose.**  Every value a cohort names already
has a closed vocabulary somewhere in this service — task families in
:data:`~agent_runtime.harness_quality.operational_corpus.OPERATIONAL_TASK_FAMILIES`,
modes in :class:`~agent_runtime.control_plane.feature_modes.FeatureModeSet`, F3
postures in
:class:`~agent_runtime.capabilities.discovery.activation.CapabilityActivationMode`,
deployment profiles in ``copilot_service_contracts``.  A checked-in JSON file
would need a second validator and could drift from all four; a contract module
gets the cross-checks for free and CI is the reviewer.  Nothing here is
free-form: :func:`promotion_cohort_matrix` is validated at import, so a cohort
naming a family the corpus does not carry cannot be committed.  Step 15 (or any
external consumer) serialises it with ``model_dump`` when it wants a file.

**What a cohort is.**  One reviewed *configuration*: which features are enabled
and at what mode, on which deployment profile, with the exact task families it
must clear and the subset of those that are **protected** — families whose
failure blocks the cohort outright, whatever the aggregate score says.  A
protected family must also be a family the cohort actually runs, because
protecting a family you never execute is a checkbox rather than a control.

**Why these eleven, in this order.**  §19's rollout ladder is
``off → dark → shadow → synthetic → dogfood → curated read-only → broader
enforce → effects/high-sensitivity``.  The matrix is that ladder made specific:
:attr:`PromotionCohort.stage` is the fixed advancement order, stage 0 is the
paired-comparison control every later cohort is measured against, each
enforcement feature gets its own cohort so a regression is attributable to one
feature rather than to a bundle, F3 gets three (shadow, curated read-only,
effects) because Step 8 is the step this matrix was declared for, and the last
cohort is the integrated one where every earlier feature is on at once.  Modes
accumulate down the ladder rather than resetting: a cohort that enabled F3 with
F8 off would be measuring a configuration no deployment will ever run.

Coverage is total by construction — a test asserts the union of every cohort's
task families equals ``OPERATIONAL_TASK_FAMILIES`` — so Step 15 cannot skip a
family by finding no cohort that mentions it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from copilot_service_contracts.deployment_profile import (
    ALLOWED_PROFILES,
    PROFILE_SAAS_MULTI_TENANT,
    PROFILE_SINGLE_TENANT_MANAGED,
    PROFILE_SINGLE_USER_DESKTOP,
)
from pydantic import Field, field_validator, model_validator

from agent_runtime.capabilities.discovery.activation import CapabilityActivationMode
from agent_runtime.control_plane.feature_modes import (
    AgentQualityFeature,
    FeatureMode,
    FeatureModeSet,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.harness_quality.operational_corpus import (
    OPERATIONAL_TASK_FAMILIES,
)


#: Bumped whenever a cohort is added, removed, or has its families or modes
#: changed.  Step 15 records the revision it evaluated, so a promotion decision
#: names the exact matrix it was made under.
PROMOTION_COHORT_MATRIX_REVISION = "promotion-cohort-matrix-v1"


#: The F3 security family.  Any cohort whose posture puts the bridge in the
#: model's hands must run *and* protect it — see
#: :meth:`PromotionCohort._coherent`.
UNAUTHORIZED_PROBE_FAMILY = "capability_discovery_unauthorized_probe"

_F3_FAMILIES = (
    "capability_discovery_selection_recall",
    UNAUTHORIZED_PROBE_FAMILY,
    "capability_discovery_end_to_end",
)
_F4_FAMILIES = tuple(
    family for family in OPERATIONAL_TASK_FAMILIES if family.startswith("task_policy_")
)
#: The F6 families. ``parallel_write_after_planned_reads`` and
#: ``parallel_approval_gated_unplannable`` are the safety half — a write that
#: joined an overlap, or an approval-gated call that was planned into one, are
#: outcomes no latency win offsets — so every cohort that runs them protects
#: them via :data:`SAFETY_FAMILIES`.
_F6_FAMILIES = (
    "parallel_independent_reads_overlap",
    "parallel_unknown_capability_serialized",
    "parallel_write_after_planned_reads",
    "parallel_approval_gated_unplannable",
    "parallel_sibling_failure_isolated",
    "parallel_cancel_restart_no_invention",
)
_EVIDENCE_FAMILIES = (
    "evidence_supported",
    "evidence_conflicting",
    "evidence_stale",
    "evidence_revoked",
)
_PROVIDER_FAMILIES = (
    "provider_pre_content_failure",
    "provider_ambiguous_failure",
)
#: Families that are a *safety* answer rather than a quality one.  Every cohort
#: that runs one protects it: an unauthorized capability surfacing, a revoked
#: evidence read succeeding, or two conflicting writes overlapping is a promotion
#: blocker at any score.
SAFETY_FAMILIES = frozenset(
    {
        UNAUTHORIZED_PROBE_FAMILY,
        "conflicting_writes",
        "evidence_revoked",
        "evidence_stale",
        "mcp_auth",
        "parallel_write_after_planned_reads",
        "parallel_approval_gated_unplannable",
    }
)


class PromotionCohort(RuntimeContract):
    """One reviewed feature-mode configuration Step 15 must evaluate.

    ``feature_modes`` is the run-snapshot mode set a run in this cohort is
    assigned, so a cohort is expressed in exactly the vocabulary the control
    plane already freezes per run — there is no second notion of "enabled".

    ``capability_activation`` is F3's requested posture.  It is stated
    separately because the F3 mode is only a *ceiling*: ``enforce`` permits
    ``deferred`` but an operator may still request ``direct`` or ``server``, and
    "direct/server fallback remains available" is a configuration Step 15 has to
    be able to name.  The validator refuses a posture above the ceiling the
    cohort's own F3 mode allows, so a widening cohort is unrepresentable rather
    than merely unreviewed.
    """

    cohort_id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    stage: int = Field(ge=0, le=99)
    deployment_profile: str = Field(min_length=1, max_length=80)
    feature_modes: FeatureModeSet
    capability_activation: CapabilityActivationMode
    task_families: tuple[str, ...] = Field(min_length=1, max_length=64)
    protected_families: tuple[str, ...] = Field(min_length=1, max_length=64)
    rationale: str = Field(min_length=16, max_length=600)

    class Messages:
        """Safe public messages for cohort validation."""

        UNKNOWN_PROFILE = "deployment_profile is not a supported profile"
        UNKNOWN_FAMILY = "task family is not declared by the operational corpus"
        DUPLICATE_FAMILY = "task families must be unique"
        UNRUN_PROTECTED = "a protected family must be a family the cohort runs"
        ABOVE_CEILING = "capability_activation exceeds the cohort's F3 mode ceiling"
        UNPROTECTED_SAFETY = "a safety family the cohort runs must be protected"
        UNPROBED_BRIDGE = (
            "a cohort registering the capability bridge must run and protect "
            "the unauthorized-probe family"
        )

    @field_validator("deployment_profile")
    @classmethod
    def _supported_profile(cls, value: str) -> str:
        if value not in ALLOWED_PROFILES:
            raise ValueError(cls.Messages.UNKNOWN_PROFILE)
        return value

    @field_validator("task_families", "protected_families")
    @classmethod
    def _declared_families(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError(cls.Messages.DUPLICATE_FAMILY)
        for family in value:
            if family not in OPERATIONAL_TASK_FAMILIES:
                raise ValueError(cls.Messages.UNKNOWN_FAMILY)
        return value

    @model_validator(mode="after")
    def _coherent(self) -> "PromotionCohort":
        run = set(self.task_families)
        if not set(self.protected_families) <= run:
            raise ValueError(self.Messages.UNRUN_PROTECTED)
        ceiling = CapabilityActivationMode.ceiling_for(
            self.feature_modes.mode_for(AgentQualityFeature.F3_CAPABILITY_DISCOVERY)
        )
        if self.capability_activation.rank > ceiling.rank:
            raise ValueError(self.Messages.ABOVE_CEILING)
        # A cohort that runs a safety family and does not protect it would let a
        # strong aggregate score carry an unauthorized-discovery or
        # overlapping-write failure through promotion.
        if run & SAFETY_FAMILIES - set(self.protected_families):
            raise ValueError(self.Messages.UNPROTECTED_SAFETY)
        # Stated here rather than remembered per cohort: the moment a posture
        # puts search/describe/invoke in the model's hands, "an unauthorized
        # name cannot be searched, described, guessed, or invoked" is a claim
        # that cohort is making. A cohort enabling the bridge for some *other*
        # feature's evaluation makes the claim just as much as an F3 cohort
        # does, and this is what stops it from being the one that skips it.
        if self.capability_activation.registers_bridge and (
            UNAUTHORIZED_PROBE_FAMILY not in run
        ):
            raise ValueError(self.Messages.UNPROBED_BRIDGE)
        return self

    @property
    def registers_capability_bridge(self) -> bool:
        """Return whether a run in this cohort exposes the F3 bridge tools."""

        return self.capability_activation.registers_bridge

    def enabled_features(self) -> tuple[AgentQualityFeature, ...]:
        """Return the features this cohort moves off ``off``, in declared order."""

        return tuple(
            feature
            for feature in AgentQualityFeature
            if self.feature_modes.mode_for(feature) is not FeatureMode.OFF
        )


def _modes(**enabled: FeatureMode) -> FeatureModeSet:
    """Build a mode set from the few features a cohort turns on."""

    return FeatureModeSet(**enabled)


_ENFORCE = FeatureMode.ENFORCE
_SHADOW = FeatureMode.SHADOW


_MATRIX: tuple[PromotionCohort, ...] = (
    PromotionCohort(
        cohort_id="control_all_off",
        stage=0,
        deployment_profile=PROFILE_SINGLE_USER_DESKTOP,
        feature_modes=_modes(),
        capability_activation=CapabilityActivationMode.DIRECT,
        task_families=OPERATIONAL_TASK_FAMILIES,
        protected_families=tuple(sorted(SAFETY_FAMILIES)),
        rationale=(
            "The paired-comparison control. Every later cohort is measured "
            "against this one, so it runs the whole corpus with every feature "
            "off and fixes the baseline the promotion thresholds are read "
            "relative to."
        ),
    ),
    PromotionCohort(
        cohort_id="f8_descriptor_revisions_enforce",
        stage=1,
        deployment_profile=PROFILE_SINGLE_USER_DESKTOP,
        feature_modes=_modes(f8=_ENFORCE),
        capability_activation=CapabilityActivationMode.DIRECT,
        task_families=("connector_selection", "mcp_auth", "duplicate_error_loop"),
        protected_families=("mcp_auth",),
        rationale=(
            "F8 is first because F3 and F5 both bind references to its "
            "descriptor revisions. Enabling it alone proves revision-aware "
            "connector discovery does not change selection or auth behaviour "
            "before anything depends on it."
        ),
    ),
    PromotionCohort(
        cohort_id="f4_task_policy_shadow",
        stage=2,
        deployment_profile=PROFILE_SINGLE_USER_DESKTOP,
        feature_modes=_modes(f4=_SHADOW, f8=_ENFORCE),
        capability_activation=CapabilityActivationMode.DIRECT,
        task_families=_F4_FAMILIES,
        protected_families=("task_policy_exact_duplicate_blocked",),
        rationale=(
            "F4 decides without enforcing. The shadow cohort exists to compare "
            "the decision it would have made against what the run actually did, "
            "which is the only way to price enforcement before paying for it."
        ),
    ),
    PromotionCohort(
        cohort_id="f4_task_policy_enforce",
        stage=3,
        deployment_profile=PROFILE_SINGLE_USER_DESKTOP,
        feature_modes=_modes(f4=_ENFORCE, f8=_ENFORCE),
        capability_activation=CapabilityActivationMode.DIRECT,
        task_families=(*_F4_FAMILIES, "duplicate_error_loop", "bulk_filtering"),
        protected_families=(
            "task_policy_exact_duplicate_blocked",
            "task_policy_cost_budget_exhaustion",
            "task_policy_deadline_exhaustion",
        ),
        rationale=(
            "The first cohort where a policy decision can stop a call. Budget "
            "and deadline exhaustion are protected because a run that ignores "
            "them is unbounded, which is worse than a run that answers badly."
        ),
    ),
    PromotionCohort(
        cohort_id="f2_prompt_assembly_enforce",
        stage=4,
        deployment_profile=PROFILE_SINGLE_USER_DESKTOP,
        feature_modes=_modes(f2=_ENFORCE, f4=_ENFORCE, f8=_ENFORCE),
        capability_activation=CapabilityActivationMode.DIRECT,
        task_families=("prompt_cache_prefix_reuse", "long_context_recall"),
        protected_families=("prompt_cache_prefix_reuse",),
        rationale=(
            "Step 8 depends on Step 5, so F2 is promoted before F3. The cache "
            "family is protected because F3.9's deferred prompt source is only "
            "worth its flat token cost if it actually joins the stable prefix."
        ),
    ),
    PromotionCohort(
        cohort_id="f3_discovery_shadow",
        stage=5,
        deployment_profile=PROFILE_SINGLE_USER_DESKTOP,
        feature_modes=_modes(f2=_ENFORCE, f3=_SHADOW, f4=_ENFORCE, f8=_ENFORCE),
        capability_activation=CapabilityActivationMode.SHADOW,
        task_families=(*_F3_FAMILIES, "connector_selection", "mcp_auth"),
        protected_families=("capability_discovery_unauthorized_probe", "mcp_auth"),
        rationale=(
            "F3 ranks and records without registering a bridge tool, so the "
            "model surface is still the pre-F3 one. This is where selection "
            "recall is measured before any answer depends on it."
        ),
    ),
    PromotionCohort(
        cohort_id="f3_discovery_deferred_read_only",
        stage=6,
        deployment_profile=PROFILE_SINGLE_USER_DESKTOP,
        feature_modes=_modes(f2=_ENFORCE, f3=_ENFORCE, f4=_ENFORCE, f8=_ENFORCE),
        capability_activation=CapabilityActivationMode.DEFERRED,
        task_families=(
            *_F3_FAMILIES,
            "connector_selection",
            "mcp_auth",
            "web_evidence",
            "library_evidence",
        ),
        protected_families=("capability_discovery_unauthorized_probe", "mcp_auth"),
        rationale=(
            "The curated read-only cohort from the §19 ladder, and the first "
            "one where the bridge is in the model's hands. Read-only because an "
            "unauthorized name reaching the model is recoverable and an "
            "unauthorized effect is not."
        ),
    ),
    PromotionCohort(
        cohort_id="f3_discovery_deferred_effects",
        stage=7,
        deployment_profile=PROFILE_SINGLE_USER_DESKTOP,
        feature_modes=_modes(f2=_ENFORCE, f3=_ENFORCE, f4=_ENFORCE, f8=_ENFORCE),
        capability_activation=CapabilityActivationMode.DEFERRED,
        task_families=(
            *_F3_FAMILIES,
            "conflicting_writes",
            "multi_file_workspace_edits",
        ),
        protected_families=(
            "capability_discovery_unauthorized_probe",
            "conflicting_writes",
        ),
        rationale=(
            "The high-sensitivity half of F3, deliberately a separate stage: an "
            "invoke that reaches a write is where a stale reference or a "
            "mis-bound idempotency key stops being a quality question."
        ),
    ),
    PromotionCohort(
        cohort_id="f3_direct_fallback",
        stage=7,
        deployment_profile=PROFILE_SINGLE_USER_DESKTOP,
        feature_modes=_modes(f2=_ENFORCE, f3=_ENFORCE, f4=_ENFORCE, f8=_ENFORCE),
        capability_activation=CapabilityActivationMode.DIRECT,
        task_families=(*_F3_FAMILIES, "connector_selection", "mcp_auth"),
        protected_families=("capability_discovery_unauthorized_probe", "mcp_auth"),
        rationale=(
            "The backout configuration named in §19 ('F3: deferred → "
            "server/direct'), evaluated rather than assumed. Same signed F3 "
            "mode as the two cohorts above with the posture dialled back, so a "
            "kill switch is a measured cohort and not an untested branch."
        ),
    ),
    PromotionCohort(
        cohort_id="f5_context_and_evidence_enforce",
        stage=8,
        deployment_profile=PROFILE_SINGLE_TENANT_MANAGED,
        feature_modes=_modes(
            f2=_ENFORCE,
            f3=_ENFORCE,
            f4=_ENFORCE,
            f5=_ENFORCE,
            f8=_ENFORCE,
        ),
        capability_activation=CapabilityActivationMode.DEFERRED,
        task_families=(
            *_EVIDENCE_FAMILIES,
            "long_context_recall",
            "bulk_filtering",
            "web_evidence",
            "library_evidence",
            UNAUTHORIZED_PROBE_FAMILY,
        ),
        protected_families=(
            "evidence_revoked",
            "evidence_stale",
            UNAUTHORIZED_PROBE_FAMILY,
        ),
        rationale=(
            "F5 reauthorizes every evidence read, so it is promoted on a "
            "multi-user profile where revocation is a live event rather than a "
            "synthetic one. Revoked and stale evidence are protected because "
            "both must fail deterministically, not merely score lower."
        ),
    ),
    PromotionCohort(
        cohort_id="f6_safe_parallel_reads",
        stage=9,
        deployment_profile=PROFILE_SINGLE_USER_DESKTOP,
        feature_modes=_modes(
            f2=_ENFORCE,
            f3=_ENFORCE,
            f4=_ENFORCE,
            f5=_ENFORCE,
            f6=_ENFORCE,
            f8=_ENFORCE,
        ),
        capability_activation=CapabilityActivationMode.DEFERRED,
        task_families=(
            *_F6_FAMILIES,
            "safe_parallel_reads",
            "conflicting_writes",
            "dataflow",
            UNAUTHORIZED_PROBE_FAMILY,
        ),
        protected_families=(
            "conflicting_writes",
            "parallel_write_after_planned_reads",
            "parallel_approval_gated_unplannable",
            UNAUTHORIZED_PROBE_FAMILY,
        ),
        rationale=(
            "F6 widens admission, so its cohort pairs the family it is meant to "
            "speed up with the family it must never touch. Conflicting writes "
            "overlapping is the one F6 outcome no latency win can offset."
        ),
    ),
    PromotionCohort(
        cohort_id="integrated_enforce_desktop",
        stage=10,
        deployment_profile=PROFILE_SINGLE_USER_DESKTOP,
        feature_modes=_modes(
            f1=_ENFORCE,
            f2=_ENFORCE,
            f3=_ENFORCE,
            f4=_ENFORCE,
            f5=_ENFORCE,
            f6=_ENFORCE,
            f8=_ENFORCE,
            f9=_ENFORCE,
            f10=_ENFORCE,
            f11=_ENFORCE,
            f12=_ENFORCE,
        ),
        capability_activation=CapabilityActivationMode.DEFERRED,
        task_families=OPERATIONAL_TASK_FAMILIES,
        protected_families=tuple(sorted(SAFETY_FAMILIES)),
        rationale=(
            "The configuration the desktop actually ships. Every feature whose "
            "step has landed is on at once, over the whole corpus, because a "
            "matrix of one-feature cohorts cannot catch an interaction. F7 "
            "stays off: §11 Step 11 gates it behind a re-justification "
            "measured with F3, F5, and F6 already enabled."
        ),
    ),
    PromotionCohort(
        cohort_id="integrated_enforce_multi_tenant",
        stage=11,
        deployment_profile=PROFILE_SAAS_MULTI_TENANT,
        feature_modes=_modes(
            f1=_ENFORCE,
            f2=_ENFORCE,
            f3=_ENFORCE,
            f4=_ENFORCE,
            f5=_ENFORCE,
            f6=_ENFORCE,
            f8=_ENFORCE,
            f9=_ENFORCE,
            f10=_ENFORCE,
            f11=_ENFORCE,
            f12=_ENFORCE,
        ),
        capability_activation=CapabilityActivationMode.DEFERRED,
        task_families=OPERATIONAL_TASK_FAMILIES,
        protected_families=tuple(sorted(SAFETY_FAMILIES)),
        rationale=(
            "The same integrated configuration on the profile where a catalog "
            "is projected per tenant and a reference minted for one subject "
            "must be meaningless to another. Last, because cross-tenant "
            "isolation is the most expensive failure in the matrix."
        ),
    ),
)


def promotion_cohort_matrix() -> tuple[PromotionCohort, ...]:
    """Return the reviewed cohort matrix Step 15 evaluates, in stage order."""

    return _MATRIX


def cohort_by_id() -> Mapping[str, PromotionCohort]:
    """Return the matrix keyed by cohort id."""

    return {cohort.cohort_id: cohort for cohort in _MATRIX}


def covered_task_families(
    cohorts: Iterable[PromotionCohort] | None = None,
) -> frozenset[str]:
    """Return every task family at least one cohort runs."""

    return frozenset(
        family
        for cohort in (_MATRIX if cohorts is None else cohorts)
        for family in cohort.task_families
    )


def cohorts_registering_the_capability_bridge() -> tuple[PromotionCohort, ...]:
    """Return the cohorts whose runs expose the F3 bridge tools.

    Step 8's own exit criteria are only meaningful for these; the rest are the
    fallback configurations that must keep behaving exactly as they did before
    F3 existed.
    """

    return tuple(cohort for cohort in _MATRIX if cohort.registers_capability_bridge)


__all__ = (
    "PROMOTION_COHORT_MATRIX_REVISION",
    "SAFETY_FAMILIES",
    "PromotionCohort",
    "cohort_by_id",
    "cohorts_registering_the_capability_bridge",
    "covered_task_families",
    "promotion_cohort_matrix",
)
