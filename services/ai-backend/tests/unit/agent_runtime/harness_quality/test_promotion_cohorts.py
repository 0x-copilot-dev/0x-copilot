"""F3.8 — the declared promotion cohort matrix, and proof it is not decorative.

The matrix exists so Step 15 evaluates a *declared* set of configurations rather
than discovering "meaningful combinations" at promotion time.  That is only worth
anything if the declaration is checkable, so every property the matrix claims is
asserted here rather than left to review:

* it names only families the operational corpus actually carries, and it names
  **all** of them, so Step 15 cannot skip a family by finding no cohort for it;
* a protected family is a family the cohort runs, so "must clear" is never
  vacuous;
* every safety family a cohort runs is protected, so a strong aggregate score
  cannot carry an unauthorized-discovery or overlapping-write failure through;
* no cohort requests an F3 posture above the ceiling its own signed F3 mode
  permits, which is the same narrowing rule the runtime enforces per run; and
* the backout configuration §19 names is itself a cohort, so "direct/server
  fallback remains available" is measured rather than assumed.

Each of the four validators is also exercised with a deliberately invalid cohort,
because a validator that never refuses anything is indistinguishable from no
validator at all.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.discovery.activation import CapabilityActivationMode
from agent_runtime.control_plane.feature_modes import (
    AgentQualityFeature,
    FeatureMode,
    FeatureModeSet,
)
from agent_runtime.harness_quality.operational_corpus import (
    OPERATIONAL_TASK_FAMILIES,
)
from agent_runtime.harness_quality.promotion_cohorts import (
    PROMOTION_COHORT_MATRIX_REVISION,
    SAFETY_FAMILIES,
    PromotionCohort,
    cohort_by_id,
    cohorts_registering_the_capability_bridge,
    covered_task_families,
    promotion_cohort_matrix,
)


def _cohort(**overrides: object) -> PromotionCohort:
    """A minimal valid cohort, so each refusal case varies exactly one thing."""

    fields: dict[str, object] = {
        "cohort_id": "probe_cohort",
        "stage": 0,
        "deployment_profile": "single_user_desktop",
        "feature_modes": FeatureModeSet(),
        "capability_activation": CapabilityActivationMode.DIRECT,
        "task_families": ("connector_selection",),
        "protected_families": ("connector_selection",),
        "rationale": "A synthetic cohort used only to exercise the validators.",
    }
    fields.update(overrides)
    return PromotionCohort.model_validate(fields)


class TestTheMatrixIsDeclaredAndComplete:
    """What Step 15 is handed, asserted rather than described."""

    def test_the_matrix_is_a_fixed_reviewed_set(self) -> None:
        matrix = promotion_cohort_matrix()

        assert PROMOTION_COHORT_MATRIX_REVISION == "promotion-cohort-matrix-v1"
        # "Around ten" is the PRD's own order of magnitude. The bound is loose on
        # purpose — it is a guard against the matrix quietly becoming either a
        # single blanket cohort or a per-family enumeration, not a target.
        assert 8 <= len(matrix) <= 20
        assert len({cohort.cohort_id for cohort in matrix}) == len(matrix)

    def test_the_matrix_is_ordered_by_promotion_stage(self) -> None:
        """Advancement order is data, so Step 15 cannot choose it."""

        stages = [cohort.stage for cohort in promotion_cohort_matrix()]

        assert stages == sorted(stages)
        assert stages[0] == 0, "stage 0 is the paired-comparison control"

    def test_stage_zero_is_the_all_off_control_over_the_whole_corpus(self) -> None:
        control = promotion_cohort_matrix()[0]

        assert control.enabled_features() == ()
        assert control.capability_activation is CapabilityActivationMode.DIRECT
        assert control.task_families == OPERATIONAL_TASK_FAMILIES

    def test_every_corpus_family_is_run_by_at_least_one_cohort(self) -> None:
        """Total coverage, so no family can be skipped by omission."""

        assert covered_task_families() == frozenset(OPERATIONAL_TASK_FAMILIES)

    def test_every_named_family_is_declared_by_the_corpus(self) -> None:
        declared = frozenset(OPERATIONAL_TASK_FAMILIES)
        for cohort in promotion_cohort_matrix():
            assert frozenset(cohort.task_families) <= declared, cohort.cohort_id

    def test_every_cohort_carries_a_reviewed_rationale(self) -> None:
        """The 'reviewed' half of 'a fixed, reviewed set', kept as data."""

        for cohort in promotion_cohort_matrix():
            assert len(cohort.rationale.strip()) >= 40, cohort.cohort_id


class TestProtectionIsMeaningful:
    """A protected family must be run, and a safety family must be protected."""

    def test_every_protected_family_is_one_the_cohort_runs(self) -> None:
        for cohort in promotion_cohort_matrix():
            assert set(cohort.protected_families) <= set(cohort.task_families), (
                cohort.cohort_id
            )

    def test_every_safety_family_a_cohort_runs_is_protected(self) -> None:
        for cohort in promotion_cohort_matrix():
            unprotected = set(cohort.task_families) & SAFETY_FAMILIES - set(
                cohort.protected_families
            )
            assert not unprotected, (cohort.cohort_id, sorted(unprotected))

    def test_the_unauthorized_probe_is_protected_wherever_the_bridge_registers(
        self,
    ) -> None:
        """Step 8's security criterion, bound to every cohort that can breach it.

        A cohort that exposes ``search``/``describe``/``invoke`` and does not
        protect the unauthorized-probe family would let Step 15 promote the
        bridge on aggregate quality alone.
        """

        registering = cohorts_registering_the_capability_bridge()

        assert registering, "at least one cohort must exercise the deferred bridge"
        for cohort in registering:
            assert "capability_discovery_unauthorized_probe" in (
                cohort.protected_families
            ), cohort.cohort_id


class TestF3PosturesCannotWiden:
    """The matrix obeys the same ceiling the runtime enforces per run."""

    def test_no_cohort_requests_a_posture_above_its_own_f3_mode(self) -> None:
        for cohort in promotion_cohort_matrix():
            ceiling = CapabilityActivationMode.ceiling_for(
                cohort.feature_modes.mode_for(
                    AgentQualityFeature.F3_CAPABILITY_DISCOVERY
                )
            )
            assert cohort.capability_activation.rank <= ceiling.rank, cohort.cohort_id

    def test_only_a_deferred_cohort_registers_the_bridge(self) -> None:
        for cohort in promotion_cohort_matrix():
            assert cohort.registers_capability_bridge is (
                cohort.capability_activation is CapabilityActivationMode.DEFERRED
            ), cohort.cohort_id

    def test_the_direct_backout_configuration_is_itself_a_cohort(self) -> None:
        """§19's 'F3: deferred → server/direct' kill switch, as a measured cohort.

        Its F3 mode is ``enforce`` — the same signed mode the deferred cohorts
        carry — with the posture dialled back, which is exactly the state a
        kill switch produces. A backout that is never evaluated is a branch
        nobody has run.
        """

        fallback = cohort_by_id()["f3_direct_fallback"]

        assert (
            fallback.feature_modes.mode_for(AgentQualityFeature.F3_CAPABILITY_DISCOVERY)
            is FeatureMode.ENFORCE
        )
        assert fallback.capability_activation is CapabilityActivationMode.DIRECT
        assert fallback.registers_capability_bridge is False


class TestTheValidatorsActuallyRefuse:
    """Each rule above, shown to bite on a deliberately malformed cohort."""

    def test_an_undeclared_task_family_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="operational corpus"):
            _cohort(
                task_families=("not_a_family",),
                protected_families=("not_a_family",),
            )

    def test_a_protected_family_the_cohort_does_not_run_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="family the cohort runs"):
            _cohort(
                task_families=("connector_selection",),
                protected_families=("bulk_filtering",),
            )

    def test_an_unprotected_safety_family_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="safety family"):
            _cohort(
                task_families=("connector_selection", "conflicting_writes"),
                protected_families=("connector_selection",),
            )

    def test_a_bridge_cohort_that_does_not_run_the_probe_is_refused(self) -> None:
        """The rule that caught a real gap in this matrix while it was written.

        Two cohorts enabled ``deferred`` for a *later* feature's evaluation and
        carried no F3 family at all, so the bridge would have been in the
        model's hands in a configuration Step 15 never probed. Stating it at the
        contract makes that unrepresentable rather than review-dependent.
        """

        with pytest.raises(ValidationError, match="unauthorized-probe"):
            _cohort(
                feature_modes=FeatureModeSet(f3=FeatureMode.ENFORCE),
                capability_activation=CapabilityActivationMode.DEFERRED,
                task_families=("connector_selection",),
                protected_families=("connector_selection",),
            )

    def test_a_posture_above_the_f3_ceiling_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="ceiling"):
            _cohort(
                feature_modes=FeatureModeSet(f3=FeatureMode.SHADOW),
                capability_activation=CapabilityActivationMode.DEFERRED,
            )

    def test_an_unsupported_deployment_profile_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="supported profile"):
            _cohort(deployment_profile="whatever_we_ship_next")

    def test_a_duplicated_family_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            _cohort(task_families=("connector_selection", "connector_selection"))


class TestTheMatrixIsMachineReadable:
    """Step 15 consumes it; it does not reinterpret it."""

    def test_every_cohort_round_trips_through_its_own_contract(self) -> None:
        for cohort in promotion_cohort_matrix():
            assert PromotionCohort.model_validate(cohort.model_dump()) == cohort

    def test_the_matrix_serialises_without_a_second_vocabulary(self) -> None:
        """A JSON dump is a view of the matrix, never a parallel definition."""

        dumped = [
            cohort.model_dump(mode="json") for cohort in promotion_cohort_matrix()
        ]

        assert {row["cohort_id"] for row in dumped} == set(cohort_by_id())
        for row in dumped:
            assert row["capability_activation"] in {
                mode.value for mode in CapabilityActivationMode
            }
            assert set(row["feature_modes"]) == {
                feature.value for feature in AgentQualityFeature
            }
