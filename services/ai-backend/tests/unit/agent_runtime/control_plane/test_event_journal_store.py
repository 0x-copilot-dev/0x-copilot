"""Run-control persistence conformance over canonical runtime event stores."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_control_store import EventJournalRunControlStore
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.control_plane import (
    AgentQualityFeature,
    BudgetEnvelope,
    FeatureMode,
    FeatureModeSet,
    RunControlDecision,
    RunControlDecisionConflict,
    RunControlDecisionWrite,
    RunControlScopeConflict,
    RunControlSnapshot,
    RunControlSnapshotConflict,
    RunControlSnapshotWrite,
    RunPolicyRevisions,
)
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventPresentationProjector,
    RuntimeEventVisibility,
)

_ORG = "org-control"
_USER = "user-control"
_SUBJECT = "a" * 64
_OTHER_SUBJECT = "b" * 64


@pytest.fixture(params=("in_memory", "file"))
async def seeded_store(request: pytest.FixtureRequest, tmp_path):
    if request.param == "in_memory":
        store = InMemoryRuntimeApiStore()
    else:
        store = FileRuntimeApiStore(tmp_path / "runtime")
    await store.open()
    conversation, run = await _new_run(store)
    try:
        yield store, conversation, run
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
            user_input="Bind a control snapshot.",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    return conversation, run


def _policy_revisions(*, prompt: str = "prompt-r1") -> RunPolicyRevisions:
    return RunPolicyRevisions(
        prompt=prompt,
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


def _snapshot(run, conversation, *, prompt: str = "prompt-r1", suffix: str = "one"):
    budget = BudgetEnvelope.create(
        budget_envelope_id="budget-one",
        revision="budget-r1",
        max_model_turns=8,
        max_tool_calls=24,
    )
    return RunControlSnapshot.create(
        run_id=run.run_id,
        conversation_id=conversation.conversation_id,
        subject_fingerprint=_SUBJECT,
        deployment_profile="single_user_desktop",
        harness_variant_ref="harness://stable/r1",
        task_policy_selection_ref="task-policy://unknown.general/r1",
        policy_revisions=_policy_revisions(prompt=prompt),
        feature_modes=FeatureModeSet(f4=FeatureMode.ENFORCE),
        budget_envelope_ref=budget.revision_ref,
        assignment_revision="assignment-r1",
        snapshot_id=f"snapshot-{suffix}",
        created_at=datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)
        + timedelta(minutes=len(suffix)),
    )


def _write(snapshot, run) -> RunControlSnapshotWrite:
    return RunControlSnapshotWrite(
        org_id=_ORG,
        trace_id=run.trace_id,
        snapshot=snapshot,
    )


async def test_get_or_create_converges_same_digest_and_appends_once(
    seeded_store,
) -> None:
    store, conversation, run = seeded_store
    controls = EventJournalRunControlStore(store)
    first_candidate = _snapshot(run, conversation, suffix="first")
    second_candidate = _snapshot(run, conversation, suffix="second")

    first, second = await asyncio.gather(
        controls.get_or_create(_write(first_candidate, run)),
        controls.get_or_create(_write(second_candidate, run)),
    )
    events = await store.list_events_after(
        org_id=_ORG,
        run_id=run.run_id,
        after_sequence=0,
    )
    bound = [
        event
        for event in events
        if event.event_type is RuntimeApiEventType.QUALITY_CONTROL_BOUND
    ]

    assert first == second
    assert first.snapshot_digest == first_candidate.snapshot_digest
    assert len(bound) == 1
    assert bound[0].visibility is RuntimeEventVisibility.INTERNAL
    assert "org-control" not in bound[0].model_dump_json()
    assert "user-control" not in bound[0].model_dump_json()
    assert (
        RuntimeEventPresentationProjector.payload_for_event(
            event_type=bound[0].event_type,
            payload=bound[0].payload,
        )
        == bound[0].payload
    )
    assert "snapshot" not in bound[0].payload
    assert not any(isinstance(value, dict) for value in bound[0].payload.values())
    assert (
        RuntimeEventPresentationProjector.payload_for_event(
            event_type=bound[0].event_type,
            payload={**bound[0].payload, "snapshot": {"raw": "record"}},
        )
        == {}
    )


async def test_different_digest_for_same_run_conflicts(seeded_store) -> None:
    store, conversation, run = seeded_store
    controls = EventJournalRunControlStore(store)
    await controls.get_or_create(_write(_snapshot(run, conversation), run))

    with pytest.raises(RunControlSnapshotConflict):
        await controls.get_or_create(
            _write(
                _snapshot(
                    run,
                    conversation,
                    prompt="prompt-r2",
                    suffix="changed",
                ),
                run,
            )
        )


async def test_cross_subject_read_fails_without_disclosing_fingerprint(
    seeded_store,
) -> None:
    store, conversation, run = seeded_store
    controls = EventJournalRunControlStore(store)
    await controls.get_or_create(_write(_snapshot(run, conversation), run))

    with pytest.raises(RunControlScopeConflict) as exc_info:
        await controls.get(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_OTHER_SUBJECT,
        )
    assert _SUBJECT not in str(exc_info.value)
    assert _OTHER_SUBJECT not in str(exc_info.value)


async def test_decisions_are_idempotent_ordered_and_snapshot_bound(
    seeded_store,
) -> None:
    store, conversation, run = seeded_store
    controls = EventJournalRunControlStore(store)
    snapshot = await controls.get_or_create(_write(_snapshot(run, conversation), run))
    first = RunControlDecision.create(
        decision_id="decision-first",
        run_id=run.run_id,
        snapshot_id=snapshot.snapshot_id,
        phase="before_model",
        feature=AgentQualityFeature.F2_PROMPT_ASSEMBLY,
        policy_revision="prompt-r1",
        input_digest="c" * 64,
        outcome_code="assembled",
        created_at=datetime(2026, 7, 27, 8, 10, tzinfo=timezone.utc),
    )
    write = RunControlDecisionWrite(
        org_id=_ORG,
        trace_id=run.trace_id,
        subject_fingerprint=_SUBJECT,
        decision=first,
    )
    appended = await controls.append(write)
    retried = await controls.append(write)
    second = RunControlDecision.create(
        decision_id="decision-second",
        run_id=run.run_id,
        snapshot_id=snapshot.snapshot_id,
        phase="tool",
        feature=AgentQualityFeature.F4_TOOL_USE_CONTROLLER,
        policy_revision="tool-r1",
        input_digest="d" * 64,
        outcome_code="admitted",
        parent_decision_refs=(first.decision_id,),
        created_at=datetime(2026, 7, 27, 8, 11, tzinfo=timezone.utc),
    )
    second_appended = await controls.append(
        write.model_copy(update={"decision": second})
    )
    events = await store.list_events_after(
        org_id=_ORG,
        run_id=run.run_id,
        after_sequence=0,
    )
    decision_events = tuple(
        event
        for event in events
        if event.event_type is RuntimeApiEventType.QUALITY_DECISION
    )

    assert retried == appended
    assert second_appended.sequence_no > appended.sequence_no
    assert await controls.list_for_run(
        org_id=_ORG,
        run_id=run.run_id,
        subject_fingerprint=_SUBJECT,
        after_sequence=appended.sequence_no,
    ) == (second_appended,)
    assert all(
        RuntimeEventPresentationProjector.payload_for_event(
            event_type=event.event_type,
            payload=event.payload,
        )
        == event.payload
        for event in decision_events
    )
    assert all("decision" not in event.payload for event in decision_events)
    assert all(
        not any(isinstance(value, dict) for value in event.payload.values())
        for event in decision_events
    )
    assert (
        RuntimeEventPresentationProjector.payload_for_event(
            event_type=decision_events[0].event_type,
            payload={**decision_events[0].payload, "decision": {"raw": "record"}},
        )
        == {}
    )

    changed = RunControlDecision.create(
        **{
            **first.model_dump(
                exclude={
                    "schema_version",
                    "created_at",
                    "decision_digest",
                    "outcome_code",
                }
            ),
            "outcome_code": "different",
        }
    )
    with pytest.raises(RunControlDecisionConflict):
        await controls.append(write.model_copy(update={"decision": changed}))


async def test_same_decision_id_is_scoped_to_run_in_global_event_identity(
    seeded_store,
) -> None:
    store, first_conversation, first_run = seeded_store
    second_conversation, second_run = await _new_run(store)
    controls = EventJournalRunControlStore(store)
    first_snapshot = await controls.get_or_create(
        _write(_snapshot(first_run, first_conversation), first_run)
    )
    second_snapshot = await controls.get_or_create(
        _write(
            _snapshot(second_run, second_conversation, suffix="second-run"),
            second_run,
        )
    )

    async def append_same_id(run, snapshot, input_digit: str):
        return await controls.append(
            RunControlDecisionWrite(
                org_id=_ORG,
                trace_id=run.trace_id,
                subject_fingerprint=_SUBJECT,
                decision=RunControlDecision.create(
                    decision_id="shared-decision-id",
                    run_id=run.run_id,
                    snapshot_id=snapshot.snapshot_id,
                    phase="before_model",
                    feature=AgentQualityFeature.F2_PROMPT_ASSEMBLY,
                    policy_revision="prompt-r1",
                    input_digest=input_digit * 64,
                    outcome_code="assembled",
                    created_at=datetime(
                        2026,
                        7,
                        27,
                        8,
                        20,
                        tzinfo=timezone.utc,
                    ),
                ),
            )
        )

    await append_same_id(first_run, first_snapshot, "e")
    await append_same_id(second_run, second_snapshot, "f")
    first_events = await store.list_events_after(
        org_id=_ORG,
        run_id=first_run.run_id,
        after_sequence=0,
    )
    second_events = await store.list_events_after(
        org_id=_ORG,
        run_id=second_run.run_id,
        after_sequence=0,
    )
    first_event = next(
        event
        for event in first_events
        if event.event_type is RuntimeApiEventType.QUALITY_DECISION
    )
    second_event = next(
        event
        for event in second_events
        if event.event_type is RuntimeApiEventType.QUALITY_DECISION
    )

    assert first_event.payload["decision_id"] == second_event.payload["decision_id"]
    assert first_event.event_id != second_event.event_id


async def test_file_store_restart_rehydrates_same_snapshot(tmp_path) -> None:
    root = tmp_path / "runtime"
    first_store = FileRuntimeApiStore(root)
    await first_store.open()
    conversation, run = await _new_run(first_store)
    snapshot = await EventJournalRunControlStore(first_store).get_or_create(
        _write(_snapshot(run, conversation), run)
    )
    await first_store.close()

    reopened = FileRuntimeApiStore(root)
    await reopened.open()
    try:
        recovered = await EventJournalRunControlStore(reopened).get(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
    finally:
        await reopened.close()

    assert recovered == snapshot
