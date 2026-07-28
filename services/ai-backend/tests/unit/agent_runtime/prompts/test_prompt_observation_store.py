"""F2 observation conformance over canonical memory/file event stores."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.prompt_observation_store import (
    EventJournalPromptObservationStore,
)
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
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.observability.token_usage import NormalizedTokenUsage
from agent_runtime.prompts.observation import (
    PromptAssembledRecord,
    PromptAssemblyObservationInput,
    PromptAssemblyOutcome,
    PromptAssemblyReasonCode,
    PromptCacheObservationInput,
    PromptCacheObservedRecord,
    PromptCacheOutcome,
    PromptCacheOwner,
    PromptFragmentTokenTotals,
    PromptObservationConflict,
    PromptObservationCorruption,
    PromptObservationScopeConflict,
    PromptObservationSnapshotConflict,
    PromptObservationWrite,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    PromptAssembledPayload,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventPresentationProjector,
    RuntimeEventRedactionState,
    RuntimeEventVisibility,
)

_ORG = "org-f2-observation"
_USER = "user-f2-observation"
_SUBJECT = "a" * 64
_OTHER_SUBJECT = "b" * 64
_CREATED_AT = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)


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
            user_input="private prompt body that must never enter F2 telemetry",
            model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        )
    )
    return conversation, run


def _snapshot(run, conversation, *, snapshot_id: str = "snapshot-f2"):
    budget = BudgetEnvelope.create(
        budget_envelope_id="budget-f2",
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
        feature_modes=FeatureModeSet(f2=FeatureMode.ENFORCE),
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


def _assembly(snapshot: RunControlSnapshot, *, plan_digest: str = "1" * 64):
    return PromptAssembledRecord.create(
        binding=_binding(snapshot),
        observation=PromptAssemblyObservationInput(
            model_call_id="model-call-1",
            plan_id="plan-1",
            plan_revision="plan-r1",
            plan_digest=plan_digest,
            provider="openai",
            model_family="gpt-5",
            complete_system_digest="2" * 64,
            stable_prefix_digest="3" * 64,
            fragment_count=4,
            stable_prefix_fragment_count=2,
            system_bytes=4096,
            estimated_input_tokens=1024,
            fragment_tokens=PromptFragmentTokenTotals(
                system_policy=200,
                stable=400,
                contextual=300,
                volatile=100,
                current_turn=20,
            ),
            cache_owner=PromptCacheOwner.PRODUCT,
            outcome=PromptAssemblyOutcome.ENFORCED,
            reason_code=PromptAssemblyReasonCode.TYPED_PLAN_ENFORCED,
        ),
        created_at=_CREATED_AT,
    )


def _write(run, record) -> PromptObservationWrite:
    return PromptObservationWrite(
        org_id=_ORG,
        subject_fingerprint=_SUBJECT,
        trace_id=run.trace_id,
        record=record,
    )


async def test_append_is_idempotent_and_serialization_is_body_free(seeded_store):
    store, _conversation, run, controls, snapshot = seeded_store
    journal = EventJournalPromptObservationStore(events=store, snapshots=controls)
    assembly = _assembly(snapshot)
    first = await journal.append(_write(run, assembly))
    retried = await journal.append(_write(run, assembly))
    usage = NormalizedTokenUsage(
        input_tokens=1000,
        output_tokens=100,
        cached_input_tokens=800,
        provider_cache_metadata_observed=True,
    )
    cache_input = PromptCacheObservationInput.from_usage(
        assembly=assembly,
        usage=usage,
    )
    cache = await journal.append(
        _write(
            run,
            PromptCacheObservedRecord.create(
                binding=_binding(snapshot),
                observation=cache_input,
                created_at=_CREATED_AT,
            ),
        )
    )
    replayed = await journal.list_for_run(
        org_id=_ORG,
        run_id=run.run_id,
        subject_fingerprint=_SUBJECT,
    )

    assert first == retried
    assert replayed == (first, cache)
    assert cache.record.outcome is PromptCacheOutcome.READ
    serialized = "".join(item.record.model_dump_json() for item in replayed)
    for forbidden in (
        "private prompt body",
        "rendered_prompt",
        "content",
        "messages",
        "access_token",
        "response_body",
    ):
        assert forbidden not in serialized


async def test_conflicts_scope_and_snapshot_drift_fail_closed(seeded_store):
    store, conversation, run, controls, snapshot = seeded_store
    journal = EventJournalPromptObservationStore(events=store, snapshots=controls)
    assembly = _assembly(snapshot)
    await journal.append(_write(run, assembly))

    changed = _assembly(snapshot, plan_digest="9" * 64)
    with pytest.raises(PromptObservationConflict):
        await journal.append(_write(run, changed))
    with pytest.raises(PromptObservationScopeConflict):
        await journal.list_for_run(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_OTHER_SUBJECT,
        )

    other_snapshot = _snapshot(run, conversation, snapshot_id="snapshot-other")
    wrong = _assembly(other_snapshot)
    with pytest.raises(PromptObservationSnapshotConflict):
        await journal.append(_write(run, wrong))


async def test_cache_requires_prior_matching_assembly(seeded_store):
    store, _conversation, run, controls, snapshot = seeded_store
    journal = EventJournalPromptObservationStore(events=store, snapshots=controls)
    assembly = _assembly(snapshot)
    cache_input = PromptCacheObservationInput.from_usage(
        assembly=assembly,
        usage=NormalizedTokenUsage(
            input_tokens=100,
            cached_input_tokens=50,
            provider_cache_metadata_observed=True,
        ),
    )
    cache = PromptCacheObservedRecord.create(
        binding=_binding(snapshot),
        observation=cache_input,
        created_at=_CREATED_AT,
    )
    with pytest.raises(PromptObservationConflict):
        await journal.append(_write(run, cache))


async def test_corrupt_body_or_event_identity_is_rejected_on_replay(seeded_store):
    store, conversation, run, controls, snapshot = seeded_store
    assembly = _assembly(snapshot)
    payload = PromptAssembledPayload(record=assembly).model_dump(mode="json")
    payload["record"]["rendered_prompt"] = "secret body"
    await store.append_event(
        RuntimeEventDraft(
            org_id=_ORG,
            event_id="wrong-f2-id",
            created_at=assembly.created_at,
            run_id=run.run_id,
            conversation_id=conversation.conversation_id,
            trace_id=run.trace_id,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.PROMPT_ASSEMBLED,
            activity_kind=RuntimeActivityKind.EVENT,
            visibility=RuntimeEventVisibility.INTERNAL,
            redaction_state=RuntimeEventRedactionState.REDACTED,
            payload=payload,
        )
    )

    with pytest.raises(PromptObservationCorruption, match="malformed"):
        await EventJournalPromptObservationStore(
            events=store,
            snapshots=controls,
        ).list_for_run(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )


def test_cache_usage_reconciliation_never_infers_a_hit_or_miss():
    class _Assembly:
        model_call_id = "call"
        record_id = "assembly"
        record_digest = "1" * 64
        plan_id = "plan"
        plan_digest = "2" * 64
        provider = "openai"
        model_family = "gpt-5"
        cache_owner = PromptCacheOwner.PRODUCT

    unsupported = PromptCacheObservationInput.from_usage(
        assembly=_Assembly(),  # type: ignore[arg-type]
        usage=NormalizedTokenUsage(input_tokens=1000),
    )
    miss = PromptCacheObservationInput.from_usage(
        assembly=_Assembly(),  # type: ignore[arg-type]
        usage=NormalizedTokenUsage(
            input_tokens=1000,
            provider_cache_metadata_observed=True,
        ),
    )

    assert unsupported.outcome is PromptCacheOutcome.UNSUPPORTED
    assert unsupported.provider_reported is False
    assert miss.outcome is PromptCacheOutcome.MISS
    assert miss.provider_reported is True
    with pytest.raises(ValidationError, match="cache token subsets exceed"):
        PromptCacheObservationInput(
            **unsupported.model_dump(
                exclude={
                    "outcome",
                    "provider_reported",
                    "cached_input_tokens",
                }
            ),
            outcome=PromptCacheOutcome.READ,
            provider_reported=True,
            cached_input_tokens=1001,
        )


def test_api_projection_rejects_extra_body_field():
    payload = {
        "record": {
            **_assembly_payload(),
            "rendered_prompt": "secret body",
        }
    }
    assert (
        RuntimeEventPresentationProjector.payload_for_event(
            event_type=RuntimeApiEventType.PROMPT_ASSEMBLED,
            payload=payload,
        )
        == {}
    )


def _assembly_payload() -> dict[str, object]:
    # The projector rejection test only needs a structurally recognizable row.
    # An extra field must still make strict validation fail.
    return {
        "schema_version": 1,
        "record_kind": "assembled",
        "record_id": "record",
        "run_id": "run",
        "snapshot_id": "snapshot",
        "snapshot_digest": "1" * 64,
        "model_call_id": "call",
        "created_at": _CREATED_AT.isoformat(),
        "record_digest": "2" * 64,
        "plan_id": "plan",
        "plan_revision": "r1",
        "plan_digest": "3" * 64,
        "provider": "openai",
        "model_family": "gpt-5",
        "complete_system_digest": "4" * 64,
        "stable_prefix_digest": None,
        "fragment_count": 1,
        "stable_prefix_fragment_count": 0,
        "system_bytes": 100,
        "estimated_input_tokens": 25,
        "fragment_tokens": {},
        "cache_owner": "none",
        "outcome": "feature_off",
        "reason_code": "prompt_assembly_disabled",
    }


async def test_file_store_restart_replays_identical_f2_records(tmp_path):
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
    journal = EventJournalPromptObservationStore(events=first, snapshots=controls)
    expected = await journal.append(_write(run, _assembly(snapshot)))
    await first.close()

    reopened = FileRuntimeApiStore(root)
    await reopened.open()
    try:
        recovered = await EventJournalPromptObservationStore(
            events=reopened,
            snapshots=EventJournalRunControlStore(reopened),
        ).list_for_run(
            org_id=_ORG,
            run_id=run.run_id,
            subject_fingerprint=_SUBJECT,
        )
    finally:
        await reopened.close()

    assert recovered == (expected,)
