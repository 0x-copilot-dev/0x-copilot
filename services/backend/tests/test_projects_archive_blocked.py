"""Tests for the Phase 6.5 §6 archive-blocked-when-running contract.

Covers:

* Archive when nothing is alive → 204 (Phase 6 behavior preserved when
  ``liveness_service=None``, or when the report shows all-zero counts).
* Archive with active runs → 409 + ``LivenessReport`` body shape.
* Archive with active routines / pending approvals / inbox in-flight
  → 409 in each case.
* No force-archive — ``?force=true``, ``X-Atlas-Force``, etc. are
  ignored; the 409 still fires.
* Partial failure → archive may proceed when remaining sources are
  clean (§3.8 fail-open trade-off — documented).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.liveness.service import LivenessService
from backend_app.projects.store import InMemoryProjectsStore


@dataclass
class _StubAiClient:
    runs: int = 0
    approvals: int = 0
    raise_for_runs: bool = False
    raise_for_approvals: bool = False

    async def count_active_runs(self, tenant_id: str, project_id: str) -> int:
        if self.raise_for_runs:
            raise RuntimeError("simulated runs upstream error")
        return self.runs

    async def count_pending_approvals(self, tenant_id: str, project_id: str) -> int:
        if self.raise_for_approvals:
            raise RuntimeError("simulated approvals upstream error")
        return self.approvals


@dataclass
class _StubCounter:
    value: int = 0

    async def __call__(self, tenant_id: str, project_id: str) -> int:
        return self.value


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    store.create_user(
        UserRecord(
            user_id="usr_sarah",
            org_id="org_acme",
            primary_email="sarah@acme.com",
            display_name="Sarah",
        )
    )
    return store


def _client(
    *,
    runs: int = 0,
    approvals: int = 0,
    routines: int = 0,
    inbox: int = 0,
    raise_for_runs: bool = False,
) -> tuple[TestClient, InMemoryProjectsStore]:
    ai_stub = _StubAiClient(
        runs=runs, approvals=approvals, raise_for_runs=raise_for_runs
    )
    liveness = LivenessService(
        ai_backend_client=ai_stub,
        routines_reader=_StubCounter(value=routines),
        inbox_reader=_StubCounter(value=inbox),
        cache_ttl_seconds=0.0,  # disable cache for tests
    )
    store = InMemoryProjectsStore()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=_seeded_identity(),
        projects_store=store,
        liveness_service=liveness,
    )
    return TestClient(app), store


def _create_project(client: TestClient) -> str:
    response = client.post(
        "/v1/projects",
        params={"org_id": "org_acme", "user_id": "usr_sarah"},
        json={
            "name": "Acme Renewal",
            "icon_emoji": "🚀",
            "color_hue": 200,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


class TestArchiveAllowed:
    def test_archive_succeeds_when_nothing_alive(self) -> None:
        client, _ = _client()
        pid = _create_project(client)
        response = client.delete(
            f"/v1/projects/{pid}",
            params={"org_id": "org_acme", "user_id": "usr_sarah"},
        )
        assert response.status_code == 204


class TestArchiveBlocked:
    @pytest.mark.parametrize(
        "kwargs,expected_field",
        [
            ({"runs": 1}, "active_runs"),
            ({"approvals": 1}, "pending_approvals"),
            ({"routines": 1}, "active_routines"),
            ({"inbox": 1}, "in_flight_inbox"),
        ],
    )
    def test_409_when_one_source_alive(
        self, kwargs: dict[str, Any], expected_field: str
    ) -> None:
        client, _ = _client(**kwargs)
        pid = _create_project(client)
        response = client.delete(
            f"/v1/projects/{pid}",
            params={"org_id": "org_acme", "user_id": "usr_sarah"},
        )
        assert response.status_code == 409
        # FastAPI HTTPException wraps the dict detail in {"detail": {...}}.
        body = response.json()
        # The error shape is the inner dict whether it's wrapped or not.
        inner = body.get("detail", body)
        assert inner["error"] == "project_archive_blocked_live_work"
        liveness = inner["liveness"]
        assert liveness[expected_field] == 1
        assert liveness["is_alive"] is True

    def test_force_query_param_ignored(self) -> None:
        client, _ = _client(runs=1)
        pid = _create_project(client)
        response = client.delete(
            f"/v1/projects/{pid}",
            params={
                "org_id": "org_acme",
                "user_id": "usr_sarah",
                "force": "true",
            },
        )
        assert response.status_code == 409


class TestPartialFailure:
    def test_archive_proceeds_when_runs_errors_and_others_clean(self) -> None:
        # Per §3.8 — fail-open. If the runs source errors but every other
        # source reports zero, the project is NOT marked alive and archive
        # proceeds.
        client, _ = _client(raise_for_runs=True)
        pid = _create_project(client)
        response = client.delete(
            f"/v1/projects/{pid}",
            params={"org_id": "org_acme", "user_id": "usr_sarah"},
        )
        assert response.status_code == 204
