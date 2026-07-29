"""F4 journal conformance over canonical memory/file runtime event stores."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_control_store import EventJournalRunControlStore
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.api.task_policy_store import EventJournalTaskPolicyStore
from agent_runtime.capabilities.task_policy_journal import (
    TaskPolicyAdmissionDisposition,
    TaskPolicyAdmissionRecordedRecord,
    TaskPolicyBudgetRecordedRecord,
    TaskPolicyFeedbackDisposition,
    TaskPolicyFeedbackRecordedRecord,
    TaskPolicyIntentRecordedRecord,
    TaskPolicyJournalConflict,
    TaskPolicyJournalCorruption,
    TaskPolicyJournalScopeConflict,
    TaskPolicyJournalSnapshotConflict,
    TaskPolicyJournalWrite,
    TaskPolicyOutcomeRecordedRecord,
    TaskPolicyOutcomeStatus,
    TaskPolicyPlanBoundRecord,
    TaskPolicyProfileSelectedRecord,
    TaskPolicyProgressRecordedRecord,
)
from agent_runtime.control_plane import (
    BudgetEnvelope,
    FeatureMode,
    FeatureModeSet,
    RunControlSnapshot,
    RunControlSnapshotWrite,
    RunPolicyRevisions,
)
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventPresentationProjector,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
    TaskPolicyJournalPayload,
)

_ORG = "org-f4-journal"
_USER = "user-f4-journal"
_SUBJECT = "a" * 64
_OTHER_SUBJECT = "b" * 64
_SELECTION_DIGEST = "c" * 64
_PLAN_DIGEST = "d" * 64
_REQUEST_FINGERPRINT = "e" * 64
_RESULT_FINGERPRINT = "f" * 64
_EFFECTIVE_BUDGET_DIGEST = "1" * 64
_CREATED_AT = datetime(2026, 7, 28, 4, 30, tzinfo=timezone.utc)


@pytest.fixture(params=("in_memory", "file"))
async def seeded_store(request: pytest.FixtureRequest, tmp_path):
    if request.param == "in_memory":
        store = InMemoryRuntimeApiStore()
    else:
        store = FileRuntimeApiStore(tmp_path / "runtime")
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
            "OPENAI_API_KEY": "sk-test",
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
    conversation_coordinator = ConversationCoordinator(
        persistence=store,
        settings=settings,
        run_coordinator=run_coordinator,
    )
    conversation = await conversation_coordinator.create_conversation(
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
            user_input="private prompt that must not enter the F4 journal",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    return conversation, run


def _snapshot(run, conversation) -> RunControlSnapshot:
    budget = BudgetEnvelope.create(
        budget_envelope_id="budget-f4",
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
        model_route="model-r1",
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
        feature_modes=FeatureModeSet(f4=FeatureMode.ENFORCE),
        budget_envelope_ref=budget.revision_ref,
        assignment_revision="assignment-r1",
        snapshot_id="snapshot-f4",
        created_at=_CREATED_AT,
    )


def _write(run, record) -> TaskPolicyJournalWrite:
    return TaskPolicyJournalWrite(
        org_id=_ORG,
        trace_id=run.trace_id,
        subject_fingerprint=_SUBJECT,
        record=record,
    )


def _records(run, snapshot):
    common = {
        "run_id": run.run_id,
        "snapshot_id": snapshot.snapshot_id,
        "created_at": _CREATED_AT,
    }
    profile = TaskPolicyProfileSelectedRecord.create(
        **common,
        record_id="profile-selected",
        selection_ref=snapshot.task_policy_selection_ref,
        selection_digest=_SELECTION_DIGEST,
        profile_id="unknown.general",
        profile_revision="tool-r1",
        task_family="unknown",
        planning_requirement="required",
        selection_reason="conservative_default",
    )
    plan = TaskPolicyPlanBoundRecord.create(
        **common,
        record_id="plan-bound",
        selection_ref=snapshot.task_policy_selection_ref,
        selection_digest=_SELECTION_DIGEST,
        plan_id="plan-one",
        plan_ref="task-plan://plan-one/sha256/" + _PLAN_DIGEST,
        plan_digest=_PLAN_DIGEST,
        created_by="deterministic",
        status="active",
        step_count=2,
        success_evidence_requirement_count=1,
    )
    intent = TaskPolicyIntentRecordedRecord.create(
        **common,
        record_id="intent-one",
        selection_ref=snapshot.task_policy_selection_ref,
        selection_digest=_SELECTION_DIGEST,
        tool_call_id="call-one",
        operation_id="operation-one",
        capability_id="connector.search",
        request_fingerprint=_REQUEST_FINGERPRINT,
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
        plan_step_id="step-one",
        expected_evidence_kind="record_ref",
    )
    admission = TaskPolicyAdmissionRecordedRecord.create(
        **common,
        record_id="admission-one",
        tool_call_id=intent.tool_call_id,
        operation_id=intent.operation_id,
        intent_record_id=intent.record_id,
        intent_digest=intent.record_digest,
        disposition=TaskPolicyAdmissionDisposition.ADMITTED,
        reason_codes=("within_budget",),
        model_turn_ordinal=1,
        tool_call_ordinal=1,
    )
    outcome = TaskPolicyOutcomeRecordedRecord.create(
        **common,
        record_id="outcome-one",
        tool_call_id=intent.tool_call_id,
        operation_id=intent.operation_id,
        intent_record_id=intent.record_id,
        intent_digest=intent.record_digest,
        request_fingerprint=intent.request_fingerprint,
        status=TaskPolicyOutcomeStatus.SUCCEEDED,
        result_fingerprint=_RESULT_FINGERPRINT,
        new_evidence_count=2,
        observed_source_count=1,
        latency_ms=12,
    )
    budget = TaskPolicyBudgetRecordedRecord.create(
        **common,
        record_id="budget-one",
        budget_envelope_ref=snapshot.budget_envelope_ref,
        effective_budget_digest=_EFFECTIVE_BUDGET_DIGEST,
        model_turns_used=1,
        tool_calls_used=1,
        cost_microusd_used=20,
        active_tool_time_ms_used=12,
        model_turn_limit=8,
        tool_call_limit=16,
    )
    feedback = TaskPolicyFeedbackRecordedRecord.create(
        **common,
        record_id="feedback-one",
        tool_call_id=intent.tool_call_id,
        operation_id=intent.operation_id,
        admission_record_id=admission.record_id,
        outcome_record_id=outcome.record_id,
        disposition=TaskPolicyFeedbackDisposition.CONTINUE,
        reason_codes=("new_evidence",),
        new_evidence_count=2,
        total_evidence_count=2,
        budget_record_id=budget.record_id,
        budget_digest=budget.record_digest,
    )
    progress = TaskPolicyProgressRecordedRecord.create(
        **common,
        record_id="progress-one",
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
        plan_status="active",
        step_count=2,
        completed_step_count=1,
        blocked_step_count=0,
        active_step_id="step-two",
        evidence_count=2,
        checkpoint_ordinal=1,
    )
    return profile, plan, intent, admission, outcome, budget, feedback, progress


async def test_append_replay_and_list_after_sequence_are_adapter_parity(
    seeded_store,
) -> None:
    store, _conversation, run, controls, snapshot = seeded_store
    journal = EventJournalTaskPolicyStore(events=store, snapshots=controls)
    records = _records(run, snapshot)

    appended = [await journal.append(_write(run, record)) for record in records]
    retried_profile = await journal.append(_write(run, records[0]))
    retried = await journal.append(_write(run, records[2]))
    replayed = await journal.list_for_run(
        org_id=_ORG,
        run_id=run.run_id,
        subject_fingerprint=_SUBJECT,
    )

    assert retried_profile == appended[0]
    assert retried == appended[2]
    assert replayed == tuple(appended)
    assert await journal.list_for_run(
        org_id=_ORG,
        run_id=run.run_id,
        subject_fingerprint=_SUBJECT,
        after_sequence=appended[4].sequence_no,
    ) == tuple(appended[5:])
    assert [item.sequence_no for item in replayed] == sorted(
        item.sequence_no for item in replayed
    )

    events = await store.list_events_after(
        org_id=_ORG,
        run_id=run.run_id,
        after_sequence=0,
    )
    journal_events = tuple(
        event
        for event in events
        if event.event_type is RuntimeApiEventType.TOOL_POLICY_JOURNAL
    )
    assert len(journal_events) == len(records)
    assert all(
        event.visibility is RuntimeEventVisibility.INTERNAL for event in journal_events
    )
    assert all(
        event.redaction_state is RuntimeEventRedactionState.REDACTED
        for event in journal_events
    )
    assert all(
        RuntimeEventPresentationProjector.payload_for_event(
            event_type=event.event_type,
            payload=event.payload,
        )
        == event.payload
        for event in journal_events
    )
    serialized = "".join(event.model_dump_json() for event in journal_events)
    for forbidden in (
        "private prompt",
        "raw_arguments",
        "raw_result",
        "credential",
        "/Users/",
        "evidence text",
    ):
        assert forbidden not in serialized


async def test_conflicting_stable_record_id_is_rejected(seeded_store) -> None:
    store, _conversation, run, controls, snapshot = seeded_store
    journal = EventJournalTaskPolicyStore(events=store, snapshots=controls)
    profile, plan, intent, *_ = _records(run, snapshot)
    await journal.append(_write(run, profile))
    await journal.append(_write(run, plan))
    await journal.append(_write(run, intent))
    changed = TaskPolicyIntentRecordedRecord.create(
        **intent.model_dump(
            exclude={"record_digest", "request_fingerprint", "created_at"}
        ),
        request_fingerprint="9" * 64,
        created_at=_CREATED_AT,
    )

    with pytest.raises(TaskPolicyJournalConflict):
        await journal.append(_write(run, changed))


async def test_scope_and_snapshot_binding_fail_closed(seeded_store) -> None:
    store, _conversation, run, controls, snapshot = seeded_store
    journal = EventJournalTaskPolicyStore(events=store, snapshots=controls)
    profile = _records(run, snapshot)[0]
    await journal.append(_write(run, profile))

    with pytest.raises(TaskPolicyJournalScopeConflict):
        await journal.list_for_run(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_OTHER_SUBJECT,
        )

    mismatched = TaskPolicyBudgetRecordedRecord.create(
        record_id="budget-wrong-snapshot",
        run_id=run.run_id,
        snapshot_id="another-snapshot",
        budget_envelope_ref=snapshot.budget_envelope_ref,
        effective_budget_digest=_EFFECTIVE_BUDGET_DIGEST,
        created_at=_CREATED_AT,
    )
    with pytest.raises(TaskPolicyJournalSnapshotConflict):
        await journal.append(_write(run, mismatched))


async def test_replay_detects_wrong_stable_event_identity(seeded_store) -> None:
    store, conversation, run, controls, snapshot = seeded_store
    journal = EventJournalTaskPolicyStore(events=store, snapshots=controls)
    profile, plan, *_ = _records(run, snapshot)
    await journal.append(_write(run, profile))
    await store.append_event(
        RuntimeEventDraft(
            org_id=_ORG,
            event_id="wrong-stable-event-id",
            created_at=plan.created_at,
            run_id=run.run_id,
            conversation_id=conversation.conversation_id,
            trace_id=run.trace_id,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.TOOL_POLICY_JOURNAL,
            activity_kind=RuntimeActivityKind.EVENT,
            visibility=RuntimeEventVisibility.INTERNAL,
            redaction_state=RuntimeEventRedactionState.REDACTED,
            payload=TaskPolicyJournalPayload(record=plan).model_dump(mode="json"),
        )
    )

    with pytest.raises(TaskPolicyJournalCorruption, match="stable identity"):
        await journal.list_for_run(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )


def test_event_projection_rejects_extra_or_malformed_private_fields() -> None:
    payload = {
        "record": {
            "schema_version": 1,
            "record_kind": "profile_selected",
            "record_id": "profile",
            "record_digest": "0" * 64,
            "run_id": "run",
            "snapshot_id": "snapshot",
            "created_at": _CREATED_AT.isoformat(),
            "selection_ref": "task-policy://unknown/r1",
            "selection_digest": _SELECTION_DIGEST,
            "profile_id": "unknown.general",
            "profile_revision": "r1",
            "task_family": "unknown",
            "planning_requirement": "required",
            "selection_reason": "default",
            "raw_arguments": {"secret": "must not survive"},
        }
    }
    assert (
        RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.TOOL_POLICY_JOURNAL,
            payload=payload,
        )
        == {}
    )


async def test_file_store_restart_replays_identical_f4_records(tmp_path) -> None:
    root = tmp_path / "runtime"
    first = FileRuntimeApiStore(root)
    await first.open()
    conversation, run = await _new_run(first)
    first_controls = EventJournalRunControlStore(first)
    snapshot = await first_controls.get_or_create(
        RunControlSnapshotWrite(
            org_id=_ORG,
            trace_id=run.trace_id,
            snapshot=_snapshot(run, conversation),
        )
    )
    first_journal = EventJournalTaskPolicyStore(
        events=first,
        snapshots=first_controls,
    )
    expected = tuple(
        [
            await first_journal.append(_write(run, record))
            for record in _records(run, snapshot)
        ]
    )
    await first.close()

    reopened = FileRuntimeApiStore(root)
    await reopened.open()
    try:
        recovered = await EventJournalTaskPolicyStore(
            events=reopened,
            snapshots=EventJournalRunControlStore(reopened),
        ).list_for_run(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
    finally:
        await reopened.close()

    assert recovered == expected
