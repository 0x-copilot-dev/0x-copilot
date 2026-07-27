from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.harness_quality import (
    DeterministicEvaluationRunner,
    EvaluationCase,
    EvaluationStatus,
    FixtureMiss,
    FixtureResponse,
    FixtureToolExecutor,
    HarnessVariant,
    InMemoryEvaluationRepository,
    PromotionGate,
    PromotionStatus,
    ProjectionPolicy,
    RuntimeTrajectoryProjector,
    ScorerResult,
    TrajectoryProjector,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from runtime_api.schemas import (
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventEnvelope,
)


def _case() -> EvaluationCase:
    return EvaluationCase(
        case_id="case_connector_selection",
        suite_id="suite_f1",
        revision="r1",
        task_family="connector_selection",
        input_ref="artifact_input_1",
        fixture_catalog_ref="fixture_catalog_r1",
        scorer_set_id="scorers_r1",
    )


def _variant() -> HarnessVariant:
    return HarnessVariant(
        variant_id="control",
        revision="r1",
        prompt_plan_revision="prompt_r1",
        capability_policy_revision="capability_r1",
        context_policy_revision="context_r1",
        model_route_revision="model_r1",
    )


def _event(sequence_no: int, payload: dict[str, object]) -> RuntimeEventEnvelope:
    return RuntimeEventEnvelope(
        run_id="run_1",
        conversation_id="conversation_1",
        source=StreamEventSource.TOOL,
        event_type=RuntimeApiEventType.TOOL_CALL,
        trace_id="trace_1",
        sequence_no=sequence_no,
        activity_kind=RuntimeActivityKind.TOOL,
        payload=payload,
    )


def test_projector_retains_only_observable_identifiers_and_digest() -> None:
    payload = {
        "tool_name": "drive.search",
        "arguments": {"query": "private raw customer query"},
        "access_token": "must-not-project",
    }
    manifest = TrajectoryProjector(redaction_policy_revision="redaction_r1").project(
        run_id="run_1",
        variant_id="control",
        events=(_event(1, payload),),
        harness_revisions={"prompt": "prompt_r1"},
    )

    assert manifest.ordered_steps[0].capability_id == "drive.search"
    assert manifest.ordered_steps[0].payload_digest == canonical_json_sha256(payload)
    assert "private raw customer query" not in manifest.model_dump_json()
    assert "must-not-project" not in manifest.model_dump_json()


@pytest.mark.asyncio
async def test_fixture_executor_is_closed_and_never_falls_back_to_live_transport() -> (
    None
):
    arguments = {"query": "quarterly plan"}
    request_digest = FixtureToolExecutor.request_digest(
        capability_id="drive.search", arguments=arguments
    )
    fixtures = FixtureToolExecutor(
        (
            FixtureResponse(
                capability_id="drive.search",
                request_digest=request_digest,
                response_ref="artifact_fixture_1",
                response_digest=canonical_json_sha256({"fixture": 1}),
            ),
        )
    )

    assert (
        await fixtures.execute(capability_id="drive.search", arguments=arguments)
    ).response_ref == "artifact_fixture_1"
    with pytest.raises(FixtureMiss):
        await fixtures.execute(
            capability_id="drive.search", arguments={"query": "miss"}
        )


class _PassingScorer:
    def score(self, *, case: EvaluationCase, trajectory: object) -> ScorerResult:
        del case, trajectory
        return ScorerResult(
            scorer_id="required_capability",
            score=1,
            passed=True,
            hard_gate=True,
            reason_code="capability_present",
        )


class _FailingScorer:
    def score(self, *, case: EvaluationCase, trajectory: object) -> ScorerResult:
        del case, trajectory
        return ScorerResult(
            scorer_id="effect_safety",
            score=0,
            passed=False,
            hard_gate=True,
            reason_code="unauthorized_effect",
        )


@pytest.mark.asyncio
async def test_hard_gate_failure_blocks_promotion() -> None:
    projector = TrajectoryProjector(redaction_policy_revision="redaction_r1")

    async def executor(*, case, variant, fixtures):
        del case, fixtures
        return projector.project(
            run_id=None,
            variant_id=variant.variant_id,
            events=(),
        )

    repository = InMemoryEvaluationRepository()
    result = await DeterministicEvaluationRunner(
        repository=repository,
        executor=executor,
        scorers=(_PassingScorer(), _FailingScorer()),
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    ).run(case=_case(), variant=_variant(), fixtures=FixtureToolExecutor(()))

    assert result.status is EvaluationStatus.FAILED
    assert result.hard_gate_failures == ("unauthorized_effect",)
    decision = PromotionGate().decide(
        decision_id="decision_1",
        candidate_variant_id="candidate",
        control_variant_id="control",
        suite_revisions=("r1",),
        thresholds_revision="thresholds_r1",
        report_ref="artifact_report_1",
        actor="user_1",
        rationale="Synthetic safety suite.",
        candidate_results=(result,),
    )
    assert decision.status is PromotionStatus.REJECTED


@pytest.mark.asyncio
async def test_fixture_miss_is_inconclusive_not_a_candidate_pass() -> None:
    async def executor(*, case, variant, fixtures):
        del case, variant
        await fixtures.execute(
            capability_id="drive.search", arguments={"query": "missing"}
        )
        raise AssertionError("fixture miss must have raised")

    result = await DeterministicEvaluationRunner(
        repository=InMemoryEvaluationRepository(),
        executor=executor,
        scorers=(),
    ).run(case=_case(), variant=_variant(), fixtures=FixtureToolExecutor(()))

    assert result.status is EvaluationStatus.INCONCLUSIVE
    assert result.hard_gate_failures == ("fixture_miss",)


class _EventStore:
    def __init__(self, events: tuple[RuntimeEventEnvelope, ...]) -> None:
        self.events = events
        self.calls = 0

    async def list_events_after(self, **_kwargs) -> tuple[RuntimeEventEnvelope, ...]:
        self.calls += 1
        return self.events


@pytest.mark.asyncio
async def test_runtime_projection_is_opt_in_and_reads_existing_event_store() -> None:
    store = _EventStore((_event(1, {"tool_name": "drive.search"}),))
    projector = RuntimeTrajectoryProjector(
        event_store=store,  # type: ignore[arg-type]
        projector=TrajectoryProjector(redaction_policy_revision="redaction_r1"),
    )
    disabled = await projector.project_run(
        org_id="org_1",
        run_id="run_1",
        variant_id="control",
        policy=ProjectionPolicy(revision="policy_r1"),
        is_development_run=False,
    )
    assert disabled is None
    assert store.calls == 0

    projected = await projector.project_run(
        org_id="org_1",
        run_id="run_1",
        variant_id="control",
        policy=ProjectionPolicy(
            revision="policy_r2", enabled=True, user_consented=True
        ),
        is_development_run=False,
    )
    assert projected is not None
    assert projected.run_id == "run_1"
    assert store.calls == 1
