"""Immutable Step 1 run-control contract tests."""

from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_runtime.control_plane import (
    AgentQualityFeature,
    BudgetEnvelope,
    FeatureMode,
    FeatureModeSet,
    RunControlDecision,
    RunControlSnapshot,
    RunPolicyRevisions,
)

_FINGERPRINT = "a" * 64
_INPUT_DIGEST = "b" * 64


def _revision_values() -> dict[str, str]:
    return {
        "prompt": "prompt-r1",
        "capability": "capability-r1",
        "context": "context-r1",
        "tool_controller": "tool-r1",
        "concurrency": "concurrency-r1",
        "dataflow": "dataflow-r1",
        "mcp_freshness": "mcp-r1",
        "delegation": "delegation-r1",
        "model_route": "model-r1",
        "workspace_edit": "workspace-r1",
        "answer_verification": "answer-r1",
    }


def _snapshot(
    *,
    policy_revisions: RunPolicyRevisions | None = None,
) -> RunControlSnapshot:
    budget = BudgetEnvelope.create(
        budget_envelope_id="budget-default",
        revision="budget-r1",
        max_model_turns=8,
        max_tool_calls=24,
        max_cost_microusd=250_000,
    )
    return RunControlSnapshot.create(
        run_id="run-control",
        conversation_id="conversation-control",
        subject_fingerprint=_FINGERPRINT,
        deployment_profile="single_user_desktop",
        harness_variant_ref="harness://stable/r1",
        task_policy_selection_ref="task-policy://unknown.general/r1",
        policy_revisions=policy_revisions
        or RunPolicyRevisions.model_validate(_revision_values()),
        feature_modes=FeatureModeSet(f2=FeatureMode.SHADOW, f4=FeatureMode.ENFORCE),
        budget_envelope_ref=budget.revision_ref,
        assignment_revision="assignment-r1",
        snapshot_id="snapshot-control",
        created_at=datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc),
    )


def test_snapshot_digest_is_stable_under_randomized_config_ordering() -> None:
    expected = _snapshot().snapshot_digest
    items = list(_revision_values().items())
    randomizer = random.Random(8417)

    for _ in range(30):
        randomizer.shuffle(items)
        revisions = RunPolicyRevisions.model_validate(dict(items))
        assert _snapshot(policy_revisions=revisions).snapshot_digest == expected


def test_snapshot_digest_excludes_record_identity_but_binds_policy() -> None:
    first = _snapshot()
    equivalent = RunControlSnapshot.create(
        **{
            **first.model_dump(
                exclude={
                    "schema_version",
                    "snapshot_id",
                    "created_at",
                    "snapshot_digest",
                    "policy_revisions",
                    "feature_modes",
                },
            ),
            "policy_revisions": first.policy_revisions,
            "feature_modes": first.feature_modes,
            "snapshot_id": "snapshot-other-writer",
            "created_at": datetime(2026, 7, 27, 8, 1, tzinfo=timezone.utc),
        }
    )
    changed_revisions = first.policy_revisions.model_copy(
        update={"prompt": "prompt-r2"}
    )
    changed = RunControlSnapshot.create(
        **{
            **first.model_dump(
                exclude={
                    "schema_version",
                    "snapshot_id",
                    "created_at",
                    "snapshot_digest",
                    "policy_revisions",
                    "feature_modes",
                },
            ),
            "policy_revisions": changed_revisions,
            "feature_modes": first.feature_modes,
            "snapshot_id": "snapshot-changed",
        }
    )

    assert equivalent.snapshot_digest == first.snapshot_digest
    assert changed.snapshot_digest != first.snapshot_digest


def test_snapshot_rejects_tampered_digest_and_naive_timestamp() -> None:
    snapshot = _snapshot()
    with pytest.raises(ValidationError, match="digest does not match"):
        RunControlSnapshot.model_validate(
            {**snapshot.model_dump(), "assignment_revision": "assignment-r2"}
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        RunControlSnapshot.create(
            **{
                **snapshot.model_dump(
                    exclude={
                        "schema_version",
                        "snapshot_id",
                        "created_at",
                        "snapshot_digest",
                        "policy_revisions",
                        "feature_modes",
                    }
                ),
                "policy_revisions": snapshot.policy_revisions,
                "feature_modes": snapshot.feature_modes,
                "created_at": datetime(2026, 7, 27, 8, 0),
            }
        )


def test_budget_envelope_is_digest_bound_and_reference_only() -> None:
    envelope = BudgetEnvelope.create(
        budget_envelope_id="budget-one",
        revision="budget-r3",
        max_input_tokens=100_000,
        max_output_tokens=8_000,
        deadline_at=datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc),
    )

    assert envelope.revision_ref.endswith(envelope.envelope_digest)
    with pytest.raises(ValidationError, match="budget_envelope_ref"):
        RunControlSnapshot.create(
            **{
                **_snapshot().model_dump(
                    exclude={
                        "schema_version",
                        "snapshot_id",
                        "created_at",
                        "snapshot_digest",
                        "budget_envelope_ref",
                        "policy_revisions",
                        "feature_modes",
                    }
                ),
                "policy_revisions": _snapshot().policy_revisions,
                "feature_modes": _snapshot().feature_modes,
                "budget_envelope_ref": "cas://fabricated-budget-body",
            }
        )
    with pytest.raises(ValidationError, match="digest does not match"):
        BudgetEnvelope.model_validate(
            {**envelope.model_dump(), "max_output_tokens": 16_000}
        )


def test_decision_digest_binds_snapshot_and_lineage() -> None:
    decision = RunControlDecision.create(
        decision_id="decision-one",
        run_id="run-control",
        snapshot_id="snapshot-control",
        phase="before_model",
        feature=AgentQualityFeature.F2_PROMPT_ASSEMBLY,
        policy_revision="prompt-r1",
        input_digest=_INPUT_DIGEST,
        outcome_code="assembled",
        record_ref="prompt-plan://plan-one",
        parent_decision_refs=("decision-parent",),
        created_at=datetime(2026, 7, 27, 8, 5, tzinfo=timezone.utc),
    )

    assert (
        decision.decision_digest
        == RunControlDecision.model_validate(decision.model_dump()).decision_digest
    )
    with pytest.raises(ValidationError, match="digest does not match"):
        RunControlDecision.model_validate(
            {**decision.model_dump(), "outcome_code": "different"}
        )
    with pytest.raises(ValidationError, match="must be unique"):
        RunControlDecision.create(
            **{
                **decision.model_dump(
                    exclude={
                        "schema_version",
                        "created_at",
                        "decision_digest",
                        "parent_decision_refs",
                    }
                ),
                "parent_decision_refs": ("decision-parent", "decision-parent"),
            }
        )
