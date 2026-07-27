"""Lifecycle parity for run-control records carried by canonical run events."""

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
    RunControlDecisionWrite,
    RunControlSnapshot,
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
)

_ORG = "org-control-lifecycle"
_USER = "user-control-lifecycle"
_SUBJECT = "a" * 64
_CONTROL_EVENT_TYPES = {
    RuntimeApiEventType.QUALITY_CONTROL_BOUND,
    RuntimeApiEventType.QUALITY_DECISION,
}


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
            user_input="Exercise run-control lifecycle parity.",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    return conversation_coordinator, conversation, run


def _policy_revisions() -> RunPolicyRevisions:
    return RunPolicyRevisions(
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


def _snapshot(run, conversation, *, suffix: str = "one") -> RunControlSnapshot:
    budget = BudgetEnvelope.create(
        budget_envelope_id="budget-control-lifecycle",
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
        policy_revisions=_policy_revisions(),
        feature_modes=FeatureModeSet(f4=FeatureMode.ENFORCE),
        budget_envelope_ref=budget.revision_ref,
        assignment_revision="assignment-r1",
        snapshot_id=f"snapshot-{suffix}",
        created_at=datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)
        + timedelta(minutes=len(suffix)),
    )


def _snapshot_write(snapshot: RunControlSnapshot, run) -> RunControlSnapshotWrite:
    return RunControlSnapshotWrite(
        org_id=_ORG,
        trace_id=run.trace_id,
        snapshot=snapshot,
    )


def _decision(snapshot: RunControlSnapshot, run) -> RunControlDecision:
    return RunControlDecision.create(
        decision_id="decision-lifecycle",
        run_id=run.run_id,
        snapshot_id=snapshot.snapshot_id,
        phase="before_model",
        feature=AgentQualityFeature.F2_PROMPT_ASSEMBLY,
        policy_revision="prompt-r1",
        input_digest="b" * 64,
        outcome_code="assembled",
        created_at=datetime(2026, 7, 27, 8, 10, tzinfo=timezone.utc),
    )


async def _seed_controls(store, conversation, run):
    controls = EventJournalRunControlStore(store)
    snapshot = await controls.get_or_create(
        _snapshot_write(_snapshot(run, conversation), run)
    )
    decision = await controls.append(
        RunControlDecisionWrite(
            org_id=_ORG,
            trace_id=run.trace_id,
            subject_fingerprint=_SUBJECT,
            decision=_decision(snapshot, run),
        )
    )
    return controls, snapshot, decision


@pytest.fixture(params=("in_memory", "file"))
async def adapter_case(request: pytest.FixtureRequest, tmp_path):
    if request.param == "in_memory":
        store = InMemoryRuntimeApiStore()
    else:
        store = FileRuntimeApiStore(tmp_path / "runtime")
    await store.open()
    coordinator, conversation, run = await _new_run(store)
    try:
        yield request.param, store, coordinator, conversation, run
    finally:
        await store.close()


async def test_same_run_concurrent_get_or_create_converges(adapter_case) -> None:
    _kind, store, _coordinator, conversation, run = adapter_case
    controls = EventJournalRunControlStore(store)
    first_candidate = _snapshot(run, conversation, suffix="first")
    second_candidate = _snapshot(run, conversation, suffix="second")

    first, second = await asyncio.gather(
        controls.get_or_create(_snapshot_write(first_candidate, run)),
        controls.get_or_create(_snapshot_write(second_candidate, run)),
    )
    events = await store.list_events_after(
        org_id=_ORG,
        run_id=run.run_id,
        after_sequence=0,
    )

    assert first == second
    assert first.snapshot_digest == first_candidate.snapshot_digest
    assert (
        sum(
            event.event_type is RuntimeApiEventType.QUALITY_CONTROL_BOUND
            for event in events
        )
        == 1
    )


async def test_missing_conversation_row_preserves_legacy_run_replay(
    adapter_case,
) -> None:
    _kind, store, _coordinator, conversation, run = adapter_case
    controls = EventJournalRunControlStore(store)
    snapshot = await controls.get_or_create(
        _snapshot_write(_snapshot(run, conversation), run)
    )

    # Runtime-worker unit fixtures and legacy imports can carry a valid run
    # event stream without a materialized conversation row. Absence is not a
    # deletion tombstone and must preserve the historical replay behavior.
    store.conversations.pop(conversation.conversation_id)

    assert (
        await controls.get(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
        == snapshot
    )


async def test_soft_delete_hides_controls_while_lifecycle_retains_for_restore(
    adapter_case,
) -> None:
    _kind, store, coordinator, conversation, run = adapter_case
    controls, snapshot, decision = await _seed_controls(store, conversation, run)

    await coordinator.delete_conversation(
        org_id=_ORG,
        user_id=_USER,
        conversation_id=conversation.conversation_id,
    )

    assert (
        await controls.get(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
        is None
    )
    assert (
        await controls.list_for_run(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
        == ()
    )
    assert (
        await store.list_events_after(
            org_id=_ORG,
            run_id=run.run_id,
            after_sequence=0,
        )
        == ()
    )

    lifecycle = await store.list_lifecycle_reference_events_window(
        org_id=_ORG,
        run_id=run.run_id,
        after_sequence=0,
        limit=100,
    )
    retained_controls = tuple(
        event for event in lifecycle.events if event.event_type in _CONTROL_EVENT_TYPES
    )
    assert len(retained_controls) == 2

    await coordinator.restore_conversation(
        org_id=_ORG,
        user_id=_USER,
        conversation_id=conversation.conversation_id,
    )
    assert (
        await controls.get(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
        == snapshot
    )
    assert await controls.list_for_run(
        org_id=_ORG,
        run_id=run.run_id,
        subject_fingerprint=_SUBJECT,
    ) == (decision,)


async def test_user_history_delete_obeys_each_adapters_physical_policy(
    adapter_case,
) -> None:
    kind, store, _coordinator, conversation, run = adapter_case
    controls, snapshot, _decision_record = await _seed_controls(
        store, conversation, run
    )
    budget_digest = snapshot.budget_envelope_ref.rsplit("/", 1)[-1]
    if kind == "file":
        conversation_dir = store.layout.conversation_dir(
            _ORG, conversation.conversation_id
        )
        assert conversation_dir.exists()
        # Budget revisions are small immutable control records, not CAS bodies.
        assert not store.object_store.exists(budget_digest)

    await store.delete_user_history(
        org_id=_ORG,
        user_id=_USER,
        reason="run-control lifecycle parity",
    )

    assert (
        await controls.get(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
        is None
    )
    assert (
        await controls.list_for_run(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
        == ()
    )
    lifecycle = await store.list_lifecycle_reference_events_window(
        org_id=_ORG,
        run_id=run.run_id,
        after_sequence=0,
        limit=100,
    )
    retained_controls = tuple(
        event for event in lifecycle.events if event.event_type in _CONTROL_EVENT_TYPES
    )
    if kind == "in_memory":
        assert len(retained_controls) == 2
    else:
        assert retained_controls == ()
        assert not conversation_dir.exists()
        assert not store.object_store.exists(budget_digest)


async def test_file_rebuilds_snapshot_and_decisions_from_canonical_records(
    tmp_path,
) -> None:
    root = tmp_path / "runtime"
    first_store = FileRuntimeApiStore(root)
    await first_store.open()
    _coordinator, conversation, run = await _new_run(first_store)
    _controls, snapshot, decision = await _seed_controls(first_store, conversation, run)
    events_path = first_store.layout.events_path(_ORG, conversation.conversation_id)
    index_path = first_store.layout.index_db_path
    assert events_path.exists()
    await first_store.close()

    index_path.unlink()
    reopened = FileRuntimeApiStore(root)
    await reopened.open()
    try:
        controls = EventJournalRunControlStore(reopened)
        recovered_snapshot = await controls.get(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
        recovered_decisions = await controls.list_for_run(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
    finally:
        await reopened.close()

    assert events_path.exists()
    assert recovered_snapshot == snapshot
    assert recovered_decisions == (decision,)
