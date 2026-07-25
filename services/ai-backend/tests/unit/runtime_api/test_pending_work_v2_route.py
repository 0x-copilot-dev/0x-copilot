"""Route-level contract tests for E1 D6 canonical pending work."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from copilot_service_contracts.work_ledger import load_ledger_golden_journeys

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import AgentRunStatus, ConversationRecord, RunRecord

_PATH = "/v1/agent/pending-work-v2"
_ORG = "org_pending_v2_route"
_USER = "user_pending_v2_route"
_OTHER_USER = "user_pending_v2_route_other"
_OTHER_ORG = "org_pending_v2_route_other"
_BASE = datetime(2026, 7, 25, 10, tzinfo=timezone.utc)


def _settings(*, enforced: bool, surfaces_enabled: bool = True) -> RuntimeSettings:
    environ = {
        "OPENAI_API_KEY": "sk-test",
        "RUNTIME_DEFAULT_PROVIDER": "openai",
        "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        # Surfaces are default-on after E3.  The D6 route must nevertheless
        # remain absent until canonical workspace effects are enforced.
        "SURFACES_V2": "true" if surfaces_enabled else "false",
    }
    if enforced:
        environ.update(
            {
                "OPERATION_GATEWAY_MODE": "enforce",
                "WORKSPACE_EFFECT_MODE": "enforce",
            }
        )
    return RuntimeSettings.load(environ=environ)


def _headers(*, org_id: str = _ORG, user_id: str = _USER) -> dict[str, str]:
    return {"x-enterprise-org-id": org_id, "x-enterprise-user-id": user_id}


def _events(journey_id: str, *, run_id: str) -> list[SimpleNamespace]:
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


def _seed(
    store: InMemoryRuntimeApiStore,
    *,
    run_id: str,
    journey_id: str,
    offset: int,
    org_id: str = _ORG,
    user_id: str = _USER,
) -> None:
    conversation_id = f"conv_{run_id}"
    store.conversations[conversation_id] = ConversationRecord(
        conversation_id=conversation_id,
        org_id=org_id,
        user_id=user_id,
        assistant_id="assistant_pending_v2_route",
        title="not a pending-work-v2 field",
    )
    store.runs[run_id] = RunRecord(
        run_id=run_id,
        conversation_id=conversation_id,
        org_id=org_id,
        user_id=user_id,
        user_message_id=f"msg_{run_id}",
        trace_id=f"trace_{run_id}",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.WAITING_FOR_APPROVAL,
        created_at=_BASE + timedelta(minutes=offset),
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
    store.events_by_run[run_id] = _events(journey_id, run_id=run_id)


def _client(
    *, enforced: bool, surfaces_enabled: bool = True
) -> tuple[TestClient, InMemoryRuntimeApiStore]:
    store = InMemoryRuntimeApiStore()
    app = RuntimeApiAppFactory.create_app(
        ports=RuntimeAdapterFactory.from_store(store),
        settings=_settings(enforced=enforced, surfaces_enabled=surfaces_enabled),
    )
    return TestClient(app), store


def test_route_absent_until_workspace_effect_cohort_is_enforced() -> None:
    client, _store = _client(enforced=False)

    response = client.get(_PATH, headers=_headers())

    assert response.status_code == 404


def test_route_is_absent_when_the_surface_kill_switch_is_off() -> None:
    client, _store = _client(enforced=True, surfaces_enabled=False)

    response = client.get(_PATH, headers=_headers())

    assert response.status_code == 404


def test_route_aggregates_only_identity_owned_runs_and_returns_safe_contract() -> None:
    client, store = _client(enforced=True)
    _seed(
        store,
        run_id="run_pending_route_owner",
        journey_id="csv_artifact_edited_then_staged",
        offset=3,
    )
    _seed(
        store,
        run_id="run_pending_route_other_user",
        journey_id="destructive_effect_held",
        offset=2,
        user_id=_OTHER_USER,
    )
    _seed(
        store,
        run_id="run_pending_route_other_org",
        journey_id="destructive_effect_held",
        offset=1,
        org_id=_OTHER_ORG,
    )

    response = client.get(
        f"{_PATH}?org_id=attacker&user_id=attacker",
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["v"] == 2
    assert {item["run_id"] for item in body["items"]} == {"run_pending_route_owner"}
    assert body["warnings"] == []
    assert set(body) == {"v", "items", "warnings", "next_cursor", "has_more"}
    assert set(body["items"][0]) == {
        "run_id",
        "subject_kind",
        "subject_id",
        "status",
        "opened_sequence_no",
        "latest_sequence_no",
    }


def test_terminal_subjects_are_omitted_and_malformed_events_fail_closed() -> None:
    client, store = _client(enforced=True)
    _seed(
        store,
        run_id="run_pending_route_terminal",
        journey_id="workspace_commit_success",
        offset=2,
    )
    _seed(
        store,
        run_id="run_pending_route_corrupt",
        journey_id="destructive_effect_held",
        offset=1,
    )
    corrupt = store.events_by_run["run_pending_route_corrupt"][0]
    store.events_by_run["run_pending_route_corrupt"][0] = SimpleNamespace(
        run_id=corrupt.run_id,
        sequence_no=corrupt.sequence_no,
        event_type="effect.staged",
        payload={"v": 1, "stage_id": "not-a-complete-canonical-stage"},
    )

    response = client.get(_PATH, headers=_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["warnings"] == [
        {"run_id": "run_pending_route_corrupt", "status": "omitted"}
    ]


def test_route_rejects_malformed_cursor_without_echoing_it() -> None:
    client, _store = _client(enforced=True)

    response = client.get(
        f"{_PATH}?cursor=file:///Users/alice/private", headers=_headers()
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid pending-work cursor."}


def test_route_rejects_a_page_larger_than_the_explicit_cap() -> None:
    client, _store = _client(enforced=True)

    response = client.get(f"{_PATH}?limit=51", headers=_headers())

    # Runtime API deliberately normalizes request-validation failures to 400.
    assert response.status_code == 400


def test_route_requires_verified_identity() -> None:
    client, _store = _client(enforced=True)

    response = client.get(_PATH)

    assert response.status_code == 401


def test_route_requires_runtime_use_scope_when_rbac_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RBAC_MODE", "enforce")
    client, _store = _client(enforced=True)

    response = client.get(
        _PATH,
        headers={
            **_headers(),
            "x-enterprise-permission-scopes": "search:read",
        },
    )

    assert response.status_code == 403
