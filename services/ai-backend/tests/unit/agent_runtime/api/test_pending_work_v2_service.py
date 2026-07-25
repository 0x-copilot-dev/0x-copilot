"""E1 D6 query-service tests for canonical Pending Work V2."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from copilot_service_contracts.work_ledger import load_ledger_golden_journeys

from agent_runtime.api.pending_work_v2_service import (
    PendingWorkV2InvalidCursor,
    PendingWorkV2QueryService,
    PendingWorkV2Values,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, ConversationRecord, RunRecord

_ORG = "org_pending_v2"
_USER = "user_pending_v2"
_OTHER_USER = "user_pending_v2_other"
_OTHER_ORG = "org_pending_v2_other"
_BASE_TIME = datetime(2026, 7, 25, 9, tzinfo=timezone.utc)


def _journey_events(journey_id: str, *, run_id: str) -> list[SimpleNamespace]:
    fixture = load_ledger_golden_journeys()
    journeys = fixture["journeys"]
    assert isinstance(journeys, list)
    journey = next(row for row in journeys if row["id"] == journey_id)
    assert isinstance(journey, dict)
    raw_events = journey["events"]
    assert isinstance(raw_events, list)
    return [
        SimpleNamespace(
            run_id=run_id,
            sequence_no=row["sequence_no"],
            event_type=row["event_type"],
            payload=dict(row["payload"]),
        )
        for row in raw_events
    ]


def _run(
    *,
    run_id: str,
    conversation_id: str,
    org_id: str,
    user_id: str,
    created_at: datetime,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        conversation_id=conversation_id,
        org_id=org_id,
        user_id=user_id,
        user_message_id=f"msg_{run_id}",
        trace_id=f"trace_{run_id}",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.WAITING_FOR_APPROVAL,
        created_at=created_at,
        runtime_context=AgentRuntimeContext(
            user_id=user_id,
            org_id=org_id,
            roles=["employee"],
            run_id=run_id,
            trace_id=f"trace_{run_id}",
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        ),
    )


def _seed(
    store: InMemoryRuntimeApiStore,
    *,
    run_id: str,
    journey_id: str,
    offset_minutes: int,
    org_id: str = _ORG,
    user_id: str = _USER,
) -> None:
    conversation_id = f"conv_{run_id}"
    store.conversations[conversation_id] = ConversationRecord(
        conversation_id=conversation_id,
        org_id=org_id,
        user_id=user_id,
        assistant_id="assistant_pending_v2",
        title="not returned by pending-work-v2",
    )
    store.runs[run_id] = _run(
        run_id=run_id,
        conversation_id=conversation_id,
        org_id=org_id,
        user_id=user_id,
        created_at=_BASE_TIME + timedelta(minutes=offset_minutes),
    )
    store.events_by_run[run_id] = _journey_events(journey_id, run_id=run_id)


def _service(store: InMemoryRuntimeApiStore) -> PendingWorkV2QueryService:
    return PendingWorkV2QueryService(persistence=store, event_store=store)


def test_two_org_two_user_isolation_and_safe_response_shape() -> None:
    store = InMemoryRuntimeApiStore()
    _seed(
        store,
        run_id="run_pending_owner",
        journey_id="csv_artifact_edited_then_staged",
        offset_minutes=4,
    )
    _seed(
        store,
        run_id="run_pending_other_user",
        journey_id="destructive_effect_held",
        offset_minutes=3,
        user_id=_OTHER_USER,
    )
    _seed(
        store,
        run_id="run_pending_other_org",
        journey_id="destructive_effect_held",
        offset_minutes=2,
        org_id=_OTHER_ORG,
    )

    response = asyncio.run(_service(store).list_pending(org_id=_ORG, user_id=_USER))

    assert {item.run_id for item in response.items} == {"run_pending_owner"}
    assert response.warnings == ()
    wire = response.model_dump(mode="json")
    assert set(wire) == {"v", "items", "warnings", "next_cursor", "has_more"}
    rendered = repr(wire)
    for forbidden in (
        "conversation_id",
        "conversation_title",
        "target",
        "reason",
        "proposal_ref",
        "workspace",
        "/Users/",
    ):
        assert forbidden not in rendered


def test_multi_run_order_dedupes_subjects_and_omits_terminal_runs() -> None:
    store = InMemoryRuntimeApiStore()
    _seed(
        store,
        run_id="run_pending_old",
        journey_id="csv_artifact_edited_then_staged",
        offset_minutes=1,
    )
    _seed(
        store,
        run_id="run_pending_new",
        journey_id="destructive_effect_held",
        offset_minutes=3,
    )
    _seed(
        store,
        run_id="run_pending_terminal",
        journey_id="workspace_commit_success",
        offset_minutes=2,
    )

    # A later duplicate stage row is validly shaped but cannot manufacture a
    # second card for the same run/subject identity.
    duplicate = _journey_events(
        "csv_artifact_edited_then_staged", run_id="run_pending_old"
    )
    first_stage = next(
        event for event in duplicate if event.event_type == "effect.staged"
    )
    store.events_by_run["run_pending_old"].append(
        SimpleNamespace(
            run_id="run_pending_old",
            sequence_no=max(event.sequence_no for event in duplicate) + 1,
            event_type=first_stage.event_type,
            payload=dict(first_stage.payload),
        )
    )

    response = asyncio.run(_service(store).list_pending(org_id=_ORG, user_id=_USER))

    assert [item.run_id for item in response.items] == [
        "run_pending_new",
        "run_pending_old",
    ]
    assert "run_pending_terminal" not in {item.run_id for item in response.items}
    keys = {
        (item.run_id, item.subject_kind, item.subject_id) for item in response.items
    }
    assert len(keys) == len(response.items)


def test_corrupt_or_mismatched_event_run_is_explicitly_omitted_not_partially_folded() -> (
    None
):
    store = InMemoryRuntimeApiStore()
    _seed(
        store,
        run_id="run_pending_good",
        journey_id="csv_artifact_edited_then_staged",
        offset_minutes=2,
    )
    _seed(
        store,
        run_id="run_pending_bad",
        journey_id="destructive_effect_held",
        offset_minutes=1,
    )
    bad = store.events_by_run["run_pending_bad"][0]
    store.events_by_run["run_pending_bad"][0] = SimpleNamespace(
        run_id="run_someone_else",
        sequence_no=bad.sequence_no,
        event_type=bad.event_type,
        payload=bad.payload,
    )

    response = asyncio.run(_service(store).list_pending(org_id=_ORG, user_id=_USER))

    assert {item.run_id for item in response.items} == {"run_pending_good"}
    assert response.warnings[0].model_dump() == {
        "run_id": "run_pending_bad",
        "status": "omitted",
    }
    assert "reason" not in response.warnings[0].model_dump()


def test_run_keyset_is_bounded_deterministic_and_rejects_malformed_cursor() -> None:
    store = InMemoryRuntimeApiStore()
    for index, run_id in enumerate(
        ("run_pending_one", "run_pending_two", "run_pending_three"), start=1
    ):
        _seed(
            store,
            run_id=run_id,
            journey_id="destructive_effect_held",
            offset_minutes=index,
        )
    service = _service(store)

    first = asyncio.run(service.list_pending(org_id=_ORG, user_id=_USER, limit=1))
    assert first.has_more is True
    assert first.next_cursor is not None
    assert [item.run_id for item in first.items] == ["run_pending_three"]

    second = asyncio.run(
        service.list_pending(
            org_id=_ORG,
            user_id=_USER,
            limit=1,
            cursor=first.next_cursor,
        )
    )
    assert [item.run_id for item in second.items] == ["run_pending_two"]

    with pytest.raises(PendingWorkV2InvalidCursor):
        asyncio.run(
            service.list_pending(
                org_id=_ORG,
                user_id=_USER,
                cursor="not-a-pending-work-v2-cursor",
            )
        )

    calls: list[int] = []
    original = store.list_runs_for_org

    async def _record_limit(**kwargs):  # noqa: ANN003
        calls.append(kwargs["limit"])
        return await original(**kwargs)

    store.list_runs_for_org = _record_limit  # type: ignore[method-assign]
    asyncio.run(service.list_pending(org_id=_ORG, user_id=_USER, limit=999_999))
    assert calls == [PendingWorkV2Values.MAX_RUN_LIMIT + 1]
