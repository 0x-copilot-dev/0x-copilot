"""Tests for the Projects destination SSE stream (PRD-H FR-H.2).

Coverage:

- Bus invariants: monotonic ``sequence_no`` per tenant channel;
  replay-after-cursor is exclusive; **tenant isolation** (a subscriber
  for ``org_a`` never sees ``org_b`` events).
- :class:`ProjectSseAdapter` framing (``event:``/``id:``/``data:`` shape).
- :class:`LastEventIdResolver` (header wins over query; invalid strings
  fall back to query; non-negative invariant).
- ``register_projects_sse_route`` exposes ``GET /v1/projects/stream``.
- **Mutation → publish**: each project mutation (create / update /
  archive / delete / member add/remove / star / unstar) emits the
  matching envelope on the caller's tenant channel, and a cross-tenant
  subscriber sees none of them.

Backend doesn't ship pytest-asyncio so async coroutines run via
``asyncio.run`` (matching ``tests/test_inbox_sse.py``).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.projects.sse import (
    Constants,
    InMemoryProjectActivityBus,
    LastEventIdResolver,
    ProjectSseAdapter,
    ProjectStreamEnvelope,
    register_projects_sse_route,
)
from backend_app.projects.store import InMemoryProjectsStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_payload(project_id: str = "prj_1") -> dict:
    return {"id": project_id, "name": "Acme renewal", "status": "active"}


async def _drain(it: AsyncIterator[bytes], limit: int) -> list[bytes]:
    out: list[bytes] = []
    async for frame in it:
        out.append(frame)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Bus
# ---------------------------------------------------------------------------


class TestProjectActivityBus:
    def test_publish_assigns_monotonic_sequence_no(self) -> None:
        bus = InMemoryProjectActivityBus()
        e1 = bus.publish(
            tenant_id="org_a",
            event_type="project_created",
            project_id="prj_1",
            payload=_project_payload("prj_1"),
        )
        e2 = bus.publish(
            tenant_id="org_a",
            event_type="project_updated",
            project_id="prj_1",
            payload=_project_payload("prj_1"),
        )
        assert e1.sequence_no == 1
        assert e2.sequence_no == 2
        assert bus.latest_sequence_no(tenant_id="org_a") == 2

    def test_list_after_is_exclusive(self) -> None:
        bus = InMemoryProjectActivityBus()
        for _ in range(3):
            bus.publish(
                tenant_id="org_a",
                event_type="project_updated",
                project_id="prj_1",
                payload=_project_payload(),
            )
        after_first = bus.list_after(tenant_id="org_a", after_sequence=1)
        assert [e.sequence_no for e in after_first] == [2, 3]

    def test_tenant_isolation_cross_org(self) -> None:
        """A subscriber for org_a never sees org_b events (FR-H.1)."""

        bus = InMemoryProjectActivityBus()
        bus.publish(
            tenant_id="org_a",
            event_type="project_created",
            project_id="prj_a",
            payload=_project_payload("prj_a"),
        )
        bus.publish(
            tenant_id="org_b",
            event_type="project_created",
            project_id="prj_b",
            payload=_project_payload("prj_b"),
        )
        a_events = bus.list_after(tenant_id="org_a", after_sequence=0)
        b_events = bus.list_after(tenant_id="org_b", after_sequence=0)
        assert [e.project_id for e in a_events] == ["prj_a"]
        assert [e.project_id for e in b_events] == ["prj_b"]
        # Each tenant channel numbers independently from 1.
        assert a_events[0].sequence_no == 1
        assert b_events[0].sequence_no == 1

    def test_unknown_channel_is_empty(self) -> None:
        bus = InMemoryProjectActivityBus()
        assert bus.list_after(tenant_id="org_missing", after_sequence=0) == ()
        assert bus.latest_sequence_no(tenant_id="org_missing") == 0

    def test_singleton_reset(self) -> None:
        InMemoryProjectActivityBus.reset_default_for_tests()
        first = InMemoryProjectActivityBus.get_default()
        assert InMemoryProjectActivityBus.get_default() is first
        InMemoryProjectActivityBus.reset_default_for_tests()
        assert InMemoryProjectActivityBus.get_default() is not first


# ---------------------------------------------------------------------------
# Adapter framing
# ---------------------------------------------------------------------------


class TestProjectSseAdapter:
    def test_format_event_frames_typed_fields(self) -> None:
        envelope = ProjectStreamEnvelope(
            sequence_no=7,
            event_type="project_updated",
            project_id="prj_1",
            payload=_project_payload(),
            emitted_at="2026-07-21T19:04:33+00:00",  # type: ignore[arg-type]
        )
        frame = ProjectSseAdapter.format_event(envelope).decode("utf-8")
        assert f"event: {Constants.Sse.EVENT_NAME}\n" in frame
        assert "id: 7\n" in frame
        assert frame.endswith("\n\n")
        # The ``data:`` line is a parseable ProjectStreamEnvelope.
        data_line = next(
            line for line in frame.splitlines() if line.startswith("data: ")
        )
        parsed = json.loads(data_line[len("data: ") :])
        assert parsed["sequence_no"] == 7
        assert parsed["event_type"] == "project_updated"
        assert parsed["project_id"] == "prj_1"

    def test_stream_replays_then_returns_when_not_following(self) -> None:
        bus = InMemoryProjectActivityBus()
        bus.publish(
            tenant_id="org_a",
            event_type="project_created",
            project_id="prj_1",
            payload=_project_payload(),
        )
        bus.publish(
            tenant_id="org_a",
            event_type="project_updated",
            project_id="prj_1",
            payload=_project_payload(),
        )

        async def exercise() -> list[bytes]:
            return await _drain(
                ProjectSseAdapter.stream(
                    bus=bus, tenant_id="org_a", after_sequence=0, follow=False
                ),
                limit=10,
            )

        frames = asyncio.run(exercise())
        assert len(frames) == 2
        assert b"id: 1\n" in frames[0]
        assert b"id: 2\n" in frames[1]

    def test_stream_honours_after_sequence(self) -> None:
        bus = InMemoryProjectActivityBus()
        for _ in range(3):
            bus.publish(
                tenant_id="org_a",
                event_type="project_updated",
                project_id="prj_1",
                payload=_project_payload(),
            )

        async def exercise() -> list[bytes]:
            return await _drain(
                ProjectSseAdapter.stream(
                    bus=bus, tenant_id="org_a", after_sequence=2, follow=False
                ),
                limit=10,
            )

        frames = asyncio.run(exercise())
        assert len(frames) == 1
        assert b"id: 3\n" in frames[0]

    def test_stream_cross_tenant_sees_nothing(self) -> None:
        bus = InMemoryProjectActivityBus()
        bus.publish(
            tenant_id="org_a",
            event_type="project_created",
            project_id="prj_a",
            payload=_project_payload("prj_a"),
        )

        async def exercise() -> list[bytes]:
            return await _drain(
                ProjectSseAdapter.stream(
                    bus=bus, tenant_id="org_b", after_sequence=0, follow=False
                ),
                limit=10,
            )

        assert asyncio.run(exercise()) == []


# ---------------------------------------------------------------------------
# LastEventIdResolver
# ---------------------------------------------------------------------------


class TestLastEventIdResolver:
    def test_header_wins_over_query(self) -> None:
        assert (
            LastEventIdResolver.resolve(header_value="5", query_after_sequence=2) == 5
        )

    def test_invalid_header_falls_back_to_query(self) -> None:
        assert (
            LastEventIdResolver.resolve(header_value="nope", query_after_sequence=3)
            == 3
        )

    def test_negative_header_falls_back(self) -> None:
        assert (
            LastEventIdResolver.resolve(header_value="-4", query_after_sequence=1) == 1
        )

    def test_no_header_uses_query(self) -> None:
        assert (
            LastEventIdResolver.resolve(header_value=None, query_after_sequence=9) == 9
        )


# ---------------------------------------------------------------------------
# Route smoke — the stream route is registered on a bare app.
# ---------------------------------------------------------------------------


class TestStreamRouteRegistration:
    def test_route_is_registered(self) -> None:
        app = FastAPI()
        register_projects_sse_route(app, bus=InMemoryProjectActivityBus())
        paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
        assert "/v1/projects/stream" in paths


# ---------------------------------------------------------------------------
# Mutation → publish (integration via create_app + injected bus)
# ---------------------------------------------------------------------------


def _seeded_identity() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    store.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    for user_id, display in (("usr_sarah", "Sarah Chen"), ("usr_bob", "Bob")):
        store.create_user(
            UserRecord(
                user_id=user_id,
                org_id="org_acme",
                primary_email=f"{user_id}@acme.com",
                display_name=display,
            )
        )
    store.create_organization(
        OrganizationRecord(org_id="org_zeta", display_name="Zeta", slug="zeta")
    )
    store.create_user(
        UserRecord(
            user_id="usr_alice",
            org_id="org_zeta",
            primary_email="alice@zeta.com",
            display_name="Alice",
        )
    )
    return store


def _client() -> tuple[TestClient, InMemoryProjectActivityBus]:
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=_seeded_identity(),
        projects_store=InMemoryProjectsStore(),
    )
    bus = app.state.projects_activity_bus
    return TestClient(app), bus


def _q(user: str = "usr_sarah", org: str = "org_acme") -> dict[str, str]:
    return {"org_id": org, "user_id": user}


def _create_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "Acme renewal",
        "description": "Q3 renewal work",
        "icon_emoji": "🚀",
        "color_hue": 210,
    }
    base.update(overrides)
    return base


class TestMutationPublishesEvent:
    def test_create_publishes_project_created(self) -> None:
        client, bus = _client()
        resp = client.post("/v1/projects", params=_q(), json=_create_payload())
        assert resp.status_code == 201, resp.text
        project_id = resp.json()["id"]
        events = list(bus.list_after(tenant_id="org_acme", after_sequence=0))
        assert len(events) == 1
        assert events[0].event_type == "project_created"
        assert events[0].project_id == project_id
        assert events[0].payload["name"] == "Acme renewal"

    def test_update_publishes_project_updated(self) -> None:
        client, bus = _client()
        project_id = client.post(
            "/v1/projects", params=_q(), json=_create_payload()
        ).json()["id"]
        base = bus.latest_sequence_no(tenant_id="org_acme")
        resp = client.patch(
            f"/v1/projects/{project_id}", params=_q(), json={"name": "Renamed"}
        )
        assert resp.status_code == 200, resp.text
        events = list(bus.list_after(tenant_id="org_acme", after_sequence=base))
        assert [e.event_type for e in events] == ["project_updated"]

    def test_archive_via_status_publishes_project_archived(self) -> None:
        client, bus = _client()
        project_id = client.post(
            "/v1/projects", params=_q(), json=_create_payload()
        ).json()["id"]
        base = bus.latest_sequence_no(tenant_id="org_acme")
        resp = client.patch(
            f"/v1/projects/{project_id}", params=_q(), json={"status": "archived"}
        )
        assert resp.status_code == 200, resp.text
        events = list(bus.list_after(tenant_id="org_acme", after_sequence=base))
        assert [e.event_type for e in events] == ["project_archived"]

    def test_delete_publishes_project_deleted(self) -> None:
        client, bus = _client()
        project_id = client.post(
            "/v1/projects", params=_q(), json=_create_payload()
        ).json()["id"]
        base = bus.latest_sequence_no(tenant_id="org_acme")
        resp = client.delete(f"/v1/projects/{project_id}", params=_q())
        assert resp.status_code == 204, resp.text
        events = list(bus.list_after(tenant_id="org_acme", after_sequence=base))
        assert [e.event_type for e in events] == ["project_deleted"]
        assert events[0].project_id == project_id

    def test_add_member_publishes_project_member_added(self) -> None:
        client, bus = _client()
        project_id = client.post(
            "/v1/projects", params=_q(), json=_create_payload()
        ).json()["id"]
        base = bus.latest_sequence_no(tenant_id="org_acme")
        resp = client.post(
            f"/v1/projects/{project_id}/members",
            params=_q(),
            json={"user_id": "usr_bob", "role": "editor"},
        )
        assert resp.status_code == 201, resp.text
        events = list(bus.list_after(tenant_id="org_acme", after_sequence=base))
        assert [e.event_type for e in events] == ["project_member_added"]
        assert events[0].payload["user_id"] == "usr_bob"

    def test_star_and_unstar_publish(self) -> None:
        client, bus = _client()
        project_id = client.post(
            "/v1/projects", params=_q(), json=_create_payload()
        ).json()["id"]
        base = bus.latest_sequence_no(tenant_id="org_acme")
        assert (
            client.post(f"/v1/projects/{project_id}/star", params=_q()).status_code
            == 204
        )
        assert (
            client.post(f"/v1/projects/{project_id}/unstar", params=_q()).status_code
            == 204
        )
        events = list(bus.list_after(tenant_id="org_acme", after_sequence=base))
        assert [e.event_type for e in events] == [
            "project_starred",
            "project_unstarred",
        ]

    def test_mutation_does_not_leak_to_other_tenant(self) -> None:
        """A mutation in org_acme publishes nothing on org_zeta's channel."""

        client, bus = _client()
        client.post("/v1/projects", params=_q(), json=_create_payload())
        assert bus.list_after(tenant_id="org_zeta", after_sequence=0) == ()
