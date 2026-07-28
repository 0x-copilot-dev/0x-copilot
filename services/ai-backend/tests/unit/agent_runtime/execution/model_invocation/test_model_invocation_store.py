"""F10.3 conformance over canonical in-memory and desktop file stores."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.model_invocation_store import EventJournalModelInvocationStore
from agent_runtime.api.run_control_store import EventJournalRunControlStore
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.control_plane import (
    BudgetEnvelope,
    FeatureMode,
    FeatureModeSet,
    RunControlBinding,
    RunControlSnapshot,
    RunControlSnapshotWrite,
    RunPolicyRevisions,
)
from agent_runtime.execution.call_identity import RuntimeModelCallIdentity
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.model_invocation import (
    ModelAttemptAdmissionRecord,
    ModelAttemptDecision,
    ModelAttemptDecisionKind,
    ModelAttemptDecisionReason,
    ModelAttemptFailedRecord,
    ModelAttemptLifecycleState,
    ModelAttemptStateRecord,
    ModelAttemptUsageRecord,
    ModelCredentialMode,
    ModelDispatchState,
    ModelFailureClass,
    ModelFallbackPolicy,
    ModelInvocationCompletedRecord,
    ModelInvocationConflict,
    ModelInvocationCorruption,
    ModelInvocationBudget,
    ModelInvocationFailedRecord,
    ModelInvocationFailureReason,
    ModelInvocationPlannedRecord,
    ModelInvocationRecoveryRecord,
    ModelInvocationScopeConflict,
    ModelInvocationSnapshotConflict,
    ModelInvocationWrite,
    ModelRecoveryKind,
    ModelRecoveryOutcome,
    ModelRouteEntry,
    ModelRouteExclusion,
    ModelRouteExclusionReason,
    ModelRoutePlan,
    ModelStreamState,
    route_records,
)
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.observability.attribution import Purpose
from agent_runtime.observability.token_usage import NormalizedTokenUsage
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    ModelInvocationJournalPayload,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventPresentationProjector,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)

_ORG = "org-f10-invocation"
_USER = "user-f10-invocation"
_SUBJECT = "a" * 64
_OTHER_SUBJECT = "b" * 64
_CREATED_AT = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(params=("in_memory", "file"))
async def seeded_store(request: pytest.FixtureRequest, tmp_path):
    store = (
        InMemoryRuntimeApiStore()
        if request.param == "in_memory"
        else FileRuntimeApiStore(tmp_path / "runtime")
    )
    await store.open()
    conversation, run = await _new_run(store)
    controls = EventJournalRunControlStore(store)
    snapshot = await controls.get_or_create(
        RunControlSnapshotWrite(
            org_id=_ORG,
            trace_id=run.trace_id,
            snapshot=_snapshot(run, conversation),
        )
    )
    try:
        yield store, conversation, run, controls, snapshot
    finally:
        await store.close()


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test-must-not-enter-the-journal",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


async def _new_run(store):
    settings = _settings()
    run_coordinator = RunCoordinator(
        persistence=store,
        queue=store,
        event_producer=RuntimeEventProducer(
            persistence=store,
            event_store=store,
            on_event_appended=None,
        ),
        settings=settings,
        model_resolver=ModelConfigResolver(settings),
    )
    conversations = ConversationCoordinator(
        persistence=store,
        settings=settings,
        run_coordinator=run_coordinator,
    )
    conversation = await conversations.create_conversation(
        CreateConversationRequest(
            org_id=_ORG,
            user_id=_USER,
            assistant_id="assistant",
        )
    )
    run = await run_coordinator.create_run(
        CreateRunRequest(
            conversation_id=conversation.conversation_id,
            org_id=_ORG,
            user_id=_USER,
            user_input="private prompt body must never enter invocation events",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    return conversation, run


def _snapshot(run, conversation, *, snapshot_id: str = "snapshot-f10"):
    budget = BudgetEnvelope.create(
        budget_envelope_id="budget-f10",
        revision="budget-r1",
        max_model_turns=8,
        max_tool_calls=16,
    )
    revisions = RunPolicyRevisions(
        prompt="prompt-r1",
        capability="capability-r1",
        context="context-r1",
        tool_controller="tool-r1",
        concurrency="concurrency-r1",
        dataflow="dataflow-r1",
        mcp_freshness="mcp-r1",
        delegation="delegation-r1",
        model_route="model-route-r1",
        workspace_edit="workspace-r1",
        answer_verification="answer-r1",
    )
    return RunControlSnapshot.create(
        run_id=run.run_id,
        conversation_id=conversation.conversation_id,
        subject_fingerprint=_SUBJECT,
        deployment_profile="single_user_desktop",
        harness_variant_ref="harness://stable/r1",
        task_policy_selection_ref="task-policy://unknown.general/r1",
        policy_revisions=revisions,
        feature_modes=FeatureModeSet(f10=FeatureMode.ENFORCE),
        budget_envelope_ref=budget.revision_ref,
        assignment_revision="assignment-r1",
        snapshot_id=snapshot_id,
        created_at=_CREATED_AT,
    )


def _binding(snapshot: RunControlSnapshot) -> RunControlBinding:
    return RunControlBinding(
        snapshot=snapshot,
        effective_modes=snapshot.feature_modes,
        decisions=(),
    )


def _identity(snapshot: RunControlSnapshot) -> RuntimeModelCallIdentity:
    return RuntimeModelCallIdentity(
        run_id=snapshot.run_id,
        snapshot_id=snapshot.snapshot_id,
        execution_scope="supervisor",
        model_turn=1,
        model_call_id="model-call:f10-stable-call",
    )


def _route_plan(*, include_exclusion: bool = True) -> ModelRoutePlan:
    route = ModelRouteEntry(
        deployment_id="deployment-primary",
        descriptor_revision="descriptor-r1",
        endpoint_ref=f"endpoint_{'1' * 32}",
        provider="openai",
        model_name="gpt-5.4-mini",
        region="us-east",
        credential_mode=ModelCredentialMode.BYOK,
        price_revision="price-r1",
        max_input_tokens=200_000,
        max_output_tokens=16_000,
    )
    exclusions = (
        (
            ModelRouteExclusion(
                deployment_id="deployment-wrong-region",
                reasons=(ModelRouteExclusionReason.REGION_MISMATCH,),
            ),
        )
        if include_exclusion
        else ()
    )
    return ModelRoutePlan.create(
        routes=(route,),
        exclusions=exclusions,
        fallback_policy=ModelFallbackPolicy.NONE,
        budget=ModelInvocationBudget(
            max_attempts=2,
            max_same_deployment_attempts=2,
            max_cost_microusd=10_000,
            max_input_tokens=10_000,
            max_output_tokens=2_000,
        ),
    )


def _planned(
    snapshot: RunControlSnapshot,
    *,
    request_digest: str = "1" * 64,
    route_plan: ModelRoutePlan | None = None,
) -> ModelInvocationPlannedRecord:
    return ModelInvocationPlannedRecord.create(
        binding=_binding(snapshot),
        identity=_identity(snapshot),
        purpose=Purpose.MAIN,
        request_digest=request_digest,
        requirements_digest="2" * 64,
        requirements_revision="requirements-r1",
        descriptor_set_revision="descriptor-set-r1",
        route_plan=route_plan or _route_plan(),
        created_at=_CREATED_AT,
    )


def _write(run, record) -> ModelInvocationWrite:
    return ModelInvocationWrite(
        org_id=_ORG,
        subject_fingerprint=_SUBJECT,
        trace_id=run.trace_id,
        record=record,
    )


def _first_admission(
    invocation: ModelInvocationPlannedRecord,
) -> ModelAttemptAdmissionRecord:
    return ModelAttemptAdmissionRecord.create(
        invocation=invocation,
        decision=ModelAttemptDecision(
            kind=ModelAttemptDecisionKind.ADMIT,
            reason=ModelAttemptDecisionReason.FIRST_ATTEMPT,
            deployment_id="deployment-primary",
            ordinal=1,
        ),
        admission_ordinal=1,
        prior_attempt_count=0,
        created_at=_CREATED_AT,
    )


async def _append_plan(journal, run, invocation, plan):
    rows = [await journal.append(_write(run, invocation))]
    for record in route_records(invocation, plan, created_at=_CREATED_AT):
        rows.append(await journal.append(_write(run, record)))
    return rows


async def test_success_journal_is_idempotent_replayable_and_body_free(seeded_store):
    store, _conversation, run, controls, snapshot = seeded_store
    journal = EventJournalModelInvocationStore(events=store, snapshots=controls)
    plan = _route_plan()
    invocation = _planned(snapshot, route_plan=plan)
    rows = await _append_plan(journal, run, invocation, plan)
    assert await journal.append(_write(run, invocation)) == rows[0]

    admission = _first_admission(invocation)
    rows.append(await journal.append(_write(run, admission)))
    for state, dispatch, stream in (
        (
            ModelAttemptLifecycleState.DISPATCHING,
            ModelDispatchState.BEFORE_DISPATCH,
            ModelStreamState.NOT_STARTED,
        ),
        (
            ModelAttemptLifecycleState.ACCEPTED,
            ModelDispatchState.ACCEPTED,
            ModelStreamState.NOT_STARTED,
        ),
        (
            ModelAttemptLifecycleState.STREAM_STARTED,
            ModelDispatchState.ACCEPTED,
            ModelStreamState.STARTED_NO_VISIBLE_OUTPUT,
        ),
    ):
        rows.append(
            await journal.append(
                _write(
                    run,
                    ModelAttemptStateRecord.create(
                        invocation=invocation,
                        admission=admission,
                        state=state,
                        dispatch_state=dispatch,
                        stream_state=stream,
                        created_at=_CREATED_AT,
                    ),
                )
            )
        )
    usage = ModelAttemptUsageRecord.create(
        invocation=invocation,
        admission=admission,
        usage=NormalizedTokenUsage(input_tokens=1000, output_tokens=200),
        provider_reported=True,
        usage_record_id="usage-row-1",
        cost_microusd=700,
        duration_ms=1200,
        created_at=_CREATED_AT,
    )
    rows.append(await journal.append(_write(run, usage)))
    completed_state = ModelAttemptStateRecord.create(
        invocation=invocation,
        admission=admission,
        state=ModelAttemptLifecycleState.COMPLETED,
        dispatch_state=ModelDispatchState.ACCEPTED,
        stream_state=ModelStreamState.VISIBLE_OUTPUT,
        visible_text_emitted=True,
        elapsed_ms=1200,
        created_at=_CREATED_AT,
    )
    rows.append(await journal.append(_write(run, completed_state)))
    terminal = ModelInvocationCompletedRecord.create(
        invocation=invocation,
        terminal_attempt_id=admission.attempt_id or "",
        attempt_count=1,
        total_input_tokens=1000,
        total_output_tokens=200,
        total_cost_microusd=700,
        total_duration_ms=1200,
        created_at=_CREATED_AT,
    )
    rows.append(await journal.append(_write(run, terminal)))

    replayed = await journal.list_for_invocation(
        org_id=_ORG,
        run_id=run.run_id,
        subject_fingerprint=_SUBJECT,
        invocation_id=invocation.invocation_id,
    )
    assert replayed == tuple(rows)
    serialized = "".join(item.record.model_dump_json() for item in replayed)
    for forbidden in (
        "private prompt body",
        "sk-test",
        "https://",
        "api_key",
        "authorization",
        "exception_message",
        "response_body",
    ):
        assert forbidden not in serialized.lower()


async def test_retry_lineage_requires_failure_recovery_and_new_attempt(seeded_store):
    store, _conversation, run, controls, snapshot = seeded_store
    journal = EventJournalModelInvocationStore(events=store, snapshots=controls)
    plan = _route_plan(include_exclusion=False)
    invocation = _planned(snapshot, route_plan=plan)
    await _append_plan(journal, run, invocation, plan)
    first = _first_admission(invocation)
    await journal.append(_write(run, first))
    failed = ModelAttemptFailedRecord.create(
        invocation=invocation,
        admission=first,
        failure_class=ModelFailureClass.PRE_DISPATCH_TRANSIENT,
        dispatch_state=ModelDispatchState.NOT_ACCEPTED,
        stream_state=ModelStreamState.NOT_STARTED,
        provider_failure_observed=True,
        created_at=_CREATED_AT,
    )
    await journal.append(_write(run, failed))
    await journal.append(
        _write(
            run,
            ModelAttemptUsageRecord.create(
                invocation=invocation,
                admission=first,
                usage=NormalizedTokenUsage(),
                provider_reported=False,
                created_at=_CREATED_AT,
            ),
        )
    )
    recovery = ModelInvocationRecoveryRecord.create(
        invocation=invocation,
        source_attempt_id=first.attempt_id or "",
        recovery_ordinal=1,
        kind=ModelRecoveryKind.SAME_DEPLOYMENT_RETRY,
        outcome=ModelRecoveryOutcome.ADMITTED,
        decision_reason=ModelAttemptDecisionReason.SAFE_SAME_DEPLOYMENT_RETRY,
        target_attempt_ordinal=2,
        created_at=_CREATED_AT,
    )
    await journal.append(_write(run, recovery))
    second = ModelAttemptAdmissionRecord.create(
        invocation=invocation,
        decision=ModelAttemptDecision(
            kind=ModelAttemptDecisionKind.ADMIT,
            reason=ModelAttemptDecisionReason.SAFE_SAME_DEPLOYMENT_RETRY,
            deployment_id="deployment-primary",
            ordinal=2,
        ),
        admission_ordinal=2,
        prior_attempt_count=1,
        created_at=_CREATED_AT,
    )
    await journal.append(_write(run, second))

    with pytest.raises(ValidationError, match="visible/effect"):
        ModelInvocationRecoveryRecord.create(
            invocation=invocation,
            source_attempt_id=second.attempt_id or "",
            recovery_ordinal=2,
            kind=ModelRecoveryKind.SAME_DEPLOYMENT_RETRY,
            outcome=ModelRecoveryOutcome.ADMITTED,
            decision_reason=ModelAttemptDecisionReason.SAFE_SAME_DEPLOYMENT_RETRY,
            target_attempt_ordinal=3,
            visible_text_emitted=True,
            created_at=_CREATED_AT,
        )


async def test_conflict_scope_snapshot_and_incomplete_route_fail_closed(seeded_store):
    store, conversation, run, controls, snapshot = seeded_store
    journal = EventJournalModelInvocationStore(events=store, snapshots=controls)
    plan = _route_plan()
    invocation = _planned(snapshot, route_plan=plan)
    await journal.append(_write(run, invocation))

    changed = _planned(snapshot, request_digest="9" * 64, route_plan=plan)
    with pytest.raises(ModelInvocationConflict):
        await journal.append(_write(run, changed))
    with pytest.raises(ModelInvocationScopeConflict):
        await journal.list_for_run(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_OTHER_SUBJECT,
        )

    wrong_snapshot = _snapshot(run, conversation, snapshot_id="snapshot-other")
    wrong = _planned(wrong_snapshot, route_plan=plan)
    with pytest.raises(ModelInvocationSnapshotConflict):
        await journal.append(_write(run, wrong))

    with pytest.raises(ModelInvocationConflict):
        await journal.append(_write(run, _first_admission(invocation)))


async def test_corrupt_secret_field_is_rejected_on_replay(seeded_store):
    store, conversation, run, controls, snapshot = seeded_store
    invocation = _planned(snapshot)
    payload = ModelInvocationJournalPayload(record=invocation).model_dump(mode="json")
    payload["record"]["api_key"] = "sk-leaked"
    await store.append_event(
        RuntimeEventDraft(
            org_id=_ORG,
            event_id="wrong-f10-id",
            created_at=invocation.created_at,
            run_id=run.run_id,
            conversation_id=conversation.conversation_id,
            trace_id=run.trace_id,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.MODEL_INVOCATION_PLANNED,
            activity_kind=RuntimeActivityKind.EVENT,
            visibility=RuntimeEventVisibility.INTERNAL,
            redaction_state=RuntimeEventRedactionState.REDACTED,
            payload=payload,
        )
    )

    with pytest.raises(ModelInvocationCorruption, match="malformed"):
        await EventJournalModelInvocationStore(
            events=store,
            snapshots=controls,
        ).list_for_run(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )


def test_runtime_projector_rejects_secret_or_body_fields():
    payload = {
        "record": {
            **_planned_payload(),
            "prompt": "secret prompt",
        }
    }
    assert (
        RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.MODEL_INVOCATION_PLANNED,
            payload=payload,
        )
        == {}
    )


def _planned_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_kind": "invocation_planned",
        "record_id": "record",
        "run_id": "run",
        "snapshot_id": "snapshot",
        "snapshot_digest": "1" * 64,
        "model_call_id": "call",
        "invocation_id": "invocation",
        "created_at": _CREATED_AT.isoformat(),
        "record_digest": "2" * 64,
        "execution_scope": "supervisor",
        "model_turn": 1,
        "purpose": "main",
        "request_digest": "3" * 64,
        "requirements_digest": "4" * 64,
        "requirements_revision": "r1",
        "descriptor_set_revision": "r1",
        "route_plan_id": "route",
        "route_digest": f"sha256:{'5' * 64}",
        "route_policy_revision": "r1",
        "fallback_policy": "none",
        "max_attempts": 1,
        "max_same_deployment_attempts": 1,
        "max_cost_microusd": None,
        "max_input_tokens": None,
        "max_output_tokens": None,
        "deadline_at": None,
        "eligible_route_count": 0,
        "exclusion_count": 0,
        "status": "planned",
    }


async def test_file_store_restart_replays_identical_invocation_records(tmp_path):
    root = tmp_path / "runtime"
    first = FileRuntimeApiStore(root)
    await first.open()
    conversation, run = await _new_run(first)
    controls = EventJournalRunControlStore(first)
    snapshot = await controls.get_or_create(
        RunControlSnapshotWrite(
            org_id=_ORG,
            trace_id=run.trace_id,
            snapshot=_snapshot(run, conversation),
        )
    )
    journal = EventJournalModelInvocationStore(events=first, snapshots=controls)
    plan = _route_plan()
    invocation = _planned(snapshot, route_plan=plan)
    expected = await _append_plan(journal, run, invocation, plan)
    await first.close()

    reopened = FileRuntimeApiStore(root)
    await reopened.open()
    try:
        recovered = await EventJournalModelInvocationStore(
            events=reopened,
            snapshots=EventJournalRunControlStore(reopened),
        ).list_for_run(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
    finally:
        await reopened.close()

    assert recovered == tuple(expected)


async def test_zero_attempt_terminal_failure_requires_denied_admission(seeded_store):
    store, _conversation, run, controls, snapshot = seeded_store
    journal = EventJournalModelInvocationStore(events=store, snapshots=controls)
    plan = ModelRoutePlan.create(
        routes=(),
        exclusions=(),
        fallback_policy=ModelFallbackPolicy.NONE,
        budget=ModelInvocationBudget(
            max_attempts=1,
            max_same_deployment_attempts=1,
        ),
    )
    invocation = _planned(snapshot, route_plan=plan)
    await _append_plan(journal, run, invocation, plan)
    denial = ModelAttemptAdmissionRecord.create(
        invocation=invocation,
        decision=ModelAttemptDecision(
            kind=ModelAttemptDecisionKind.DENY,
            reason=ModelAttemptDecisionReason.NO_ELIGIBLE_ROUTE,
        ),
        admission_ordinal=1,
        prior_attempt_count=0,
        created_at=_CREATED_AT,
    )
    await journal.append(_write(run, denial))
    terminal = ModelInvocationFailedRecord.create(
        invocation=invocation,
        attempt_count=0,
        reason=ModelInvocationFailureReason.NO_ELIGIBLE_ROUTE,
        created_at=_CREATED_AT,
    )
    persisted = await journal.append(_write(run, terminal))
    assert persisted.record == terminal
