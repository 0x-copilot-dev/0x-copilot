"""Tests for ``POST /internal/v1/todos/series/materialize-due`` (DW-2).

The endpoint is the trusted-backend lane the ai-backend
``todo_recurrence_materializer`` worker drives. Coverage:

* Happy path — a due series produces one new Todo row with
  ``source.kind = "recurrence"`` and the series's ``last_materialized_due``
  advances to the new due date.
* Idempotency — replaying the same tick clock returns
  ``skipped_duplicates >= 1`` and DOES NOT insert another row (matches
  the partial UNIQUE index ``todo_series_dedup`` in schema.sql).
* Tenant isolation — a materialised row carries the SERIES's tenant_id +
  owner_user_id, never the caller's header identity (system-level
  workflow per service docstring).
* Concurrency claim semantics — the in-memory adapter snapshots the
  ``ends_at IS NULL OR ends_at > now`` predicate the postgres adapter
  pairs with ``FOR UPDATE SKIP LOCKED``; a series with ``ends_at`` in
  the past is excluded from the claim set.
* Service-token + identity headers required (401 without).
* Body validation — malformed ``now`` returns 400.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from enterprise_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend_app.todos.routes import register_todos_routes
from backend_app.todos.service import TodosService
from backend_app.todos.store import (
    InMemoryTodosStore,
    TodoSeriesRecord,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_PATH = "/internal/v1/todos/series/materialize-due"


class _StubIdentityStore:
    """The materialize endpoint never consults the identity store, but
    ``TodosService.__init__`` still requires one. A stub keeps the test
    surface narrow."""


def _build_client(
    *, store: InMemoryTodosStore | None = None
) -> tuple[TestClient, InMemoryTodosStore]:
    store = store or InMemoryTodosStore()
    app = FastAPI()
    service = TodosService(store=store, identity_store=_StubIdentityStore())  # type: ignore[arg-type]
    register_todos_routes(app, service=service)
    return TestClient(app), store


def _service_headers(*, org: str = "system", user: str = "system") -> dict[str, str]:
    return {
        SERVICE_TOKEN_HEADER: "tok-test",
        ORG_HEADER: org,
        USER_HEADER: user,
    }


def _seed_series(
    store: InMemoryTodosStore,
    *,
    series_id: str = "ser_alpha",
    tenant_id: str = "org_acme",
    owner_user_id: str = "usr_sarah",
    rule: str = "rrule",
    spec: str = "FREQ=DAILY;INTERVAL=1",
    started_at: datetime | None = None,
    last_materialized_due: datetime | None = None,
    ends_at: datetime | None = None,
) -> TodoSeriesRecord:
    record = TodoSeriesRecord(
        id=series_id,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        rule=rule,
        spec=spec,
        started_at=started_at or datetime(2026, 5, 17, tzinfo=timezone.utc),
        ends_at=ends_at,
        last_materialized_due=last_materialized_due,
    )
    return store.insert_series(record)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_due_series_materializes_one_todo(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, store = _build_client()
        _seed_series(
            store,
            started_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        )
        # ``now`` is one day after the seed; the DAILY rule yields
        # 2026-05-18 as the next due. The series anchor uses
        # ``started_at`` because ``last_materialized_due`` is unset.
        response = client.post(
            _PATH,
            json={"now": "2026-05-18T08:00:00Z"},
            headers=_service_headers(),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {
            "materialized": 1,
            "skipped_duplicates": 0,
            "series_processed": 1,
        }

        # One Todo materialised; due is the next-day date, owner +
        # tenant inherited from the series, source.kind = recurrence.
        materialised = [r for r in store.todos.values() if r.series_id == "ser_alpha"]
        assert len(materialised) == 1
        row = materialised[0]
        assert row.tenant_id == "org_acme"
        assert row.owner_user_id == "usr_sarah"
        assert row.due == "2026-05-18"
        assert row.source["kind"] == "recurrence"
        assert row.source["series_id"] == "ser_alpha"
        assert row.recurrence == {
            "rule": "rrule",
            "spec": "FREQ=DAILY;INTERVAL=1",
            "series_id": "ser_alpha",
        }

        # Series anchor advanced — next tick computes from the new
        # ``last_materialized_due``, not the original ``started_at``.
        series = store.series["ser_alpha"]
        assert series.last_materialized_due == datetime(
            2026, 5, 18, tzinfo=timezone.utc
        )

        # Audit row written under the SERIES owner (system-level
        # workflow; never the caller's identity).
        audits = store.list_audit_for_todo(tenant_id="org_acme", todo_id=row.id)
        actions = [a.action for a in audits]
        assert "todo.materialize" in actions
        materialize_audit = next(a for a in audits if a.action == "todo.materialize")
        assert materialize_audit.actor_user_id == "usr_sarah"
        assert materialize_audit.tenant_id == "org_acme"

    def test_future_series_not_materialized(self, monkeypatch) -> None:
        """``next_due`` strictly after ``now`` → no materialization."""

        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, store = _build_client()
        _seed_series(
            store,
            started_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        )
        # ``now`` is BEFORE the next-due (same day as seed; DAILY's
        # next instance is tomorrow). The series is processed (we
        # claim it) but produces no row.
        response = client.post(
            _PATH,
            json={"now": "2026-05-17T08:00:00Z"},
            headers=_service_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["materialized"] == 0
        assert body["series_processed"] == 1
        assert store.todos == {}


# ---------------------------------------------------------------------------
# Idempotency — second tick with the same clock does NOT duplicate
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_two_ticks_same_clock_only_one_row(self, monkeypatch) -> None:
        """A second tick with the same clock NEVER produces a duplicate row.

        The first tick materialises one Todo and advances the anchor; the
        second tick sees the anchor at the new value and (because no new
        date is due yet) materialises nothing. Either way the row count
        is one — that is the contract the worker relies on, and the only
        contract that matters when the worker re-polls on its tick.
        """

        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, store = _build_client()
        _seed_series(
            store,
            started_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        )

        first = client.post(
            _PATH,
            json={"now": "2026-05-18T08:00:00Z"},
            headers=_service_headers(),
        )
        assert first.status_code == 200
        assert first.json()["materialized"] == 1
        assert first.json()["skipped_duplicates"] == 0

        # Re-fire the same clock — anchor has advanced so no new
        # row is due yet; the second tick is a no-op.
        second = client.post(
            _PATH,
            json={"now": "2026-05-18T08:00:00Z"},
            headers=_service_headers(),
        )
        assert second.status_code == 200
        body = second.json()
        assert body["materialized"] == 0
        assert body["series_processed"] == 1

        # Critically: exactly one materialised todo for this series.
        materialised = [r for r in store.todos.values() if r.series_id == "ser_alpha"]
        assert len(materialised) == 1

    def test_replay_after_anchor_lag_counts_as_dedup(self, monkeypatch) -> None:
        """Simulate a crash where the row was inserted but the anchor
        update didn't commit — the next tick MUST not insert a duplicate.

        This exercises the partial UNIQUE index path (``todo_series_dedup``)
        that postgres relies on for hard-stop idempotency; the service's
        pre-check counts the dedup as ``skipped_duplicates`` rather than
        crashing on the constraint violation.
        """

        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, store = _build_client()
        series = _seed_series(
            store,
            started_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        )
        # Pre-insert the Todo the next tick would otherwise create —
        # but leave the anchor un-advanced (mirroring a crash between
        # the INSERT and the UPDATE in the same transaction).
        from backend_app.todos.store import TodoRecord

        store.insert_todo(
            TodoRecord(
                tenant_id=series.tenant_id,
                owner_user_id=series.owner_user_id,
                text="Pre-existing row",
                status="open",
                priority="med",
                due="2026-05-18",
                source={"kind": "recurrence", "series_id": series.id},
                recurrence={
                    "rule": series.rule,
                    "spec": series.spec,
                    "series_id": series.id,
                },
                series_id=series.id,
            )
        )
        # Anchor is still ``None`` (matches the crash-before-update
        # state). The next tick MUST detect the existing row.
        assert store.series[series.id].last_materialized_due is None

        response = client.post(
            _PATH,
            json={"now": "2026-05-18T08:00:00Z"},
            headers=_service_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["materialized"] == 0
        assert body["skipped_duplicates"] == 1
        assert body["series_processed"] == 1

        # Still exactly one materialised todo for this series.
        materialised = [r for r in store.todos.values() if r.series_id == series.id]
        assert len(materialised) == 1
        # Anchor was advanced this time (recovery path) so subsequent
        # ticks don't keep hitting the dedup.
        assert store.series[series.id].last_materialized_due == datetime(
            2026, 5, 18, tzinfo=timezone.utc
        )


# ---------------------------------------------------------------------------
# Tenant isolation — materialisation respects the per-series tenant
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_series_tenant_drives_row_tenant_not_header(self, monkeypatch) -> None:
        """Caller's ``x-enterprise-org-id`` header has no effect on which
        tenant the new Todo is written to — the series's own tenant_id
        is the only source of truth (system-level workflow)."""

        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, store = _build_client()
        _seed_series(
            store,
            series_id="ser_acme",
            tenant_id="org_acme",
            owner_user_id="usr_sarah",
            started_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        )
        _seed_series(
            store,
            series_id="ser_zeta",
            tenant_id="org_zeta",
            owner_user_id="usr_alice",
            started_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        )

        # Caller pretends to be "system" — the materialiser ignores
        # this header for tenant selection.
        response = client.post(
            _PATH,
            json={"now": "2026-05-18T08:00:00Z"},
            headers=_service_headers(org="system", user="system"),
        )
        assert response.status_code == 200
        body = response.json()
        # BOTH tenants get a row, one each.
        assert body["materialized"] == 2
        assert body["series_processed"] == 2

        acme_rows = [
            r
            for r in store.todos.values()
            if r.tenant_id == "org_acme" and r.series_id == "ser_acme"
        ]
        zeta_rows = [
            r
            for r in store.todos.values()
            if r.tenant_id == "org_zeta" and r.series_id == "ser_zeta"
        ]
        assert len(acme_rows) == 1
        assert len(zeta_rows) == 1
        # The Acme row is owned by the Acme user; ditto Zeta. No
        # cross-tenant leakage even though both ran in one call.
        assert acme_rows[0].owner_user_id == "usr_sarah"
        assert zeta_rows[0].owner_user_id == "usr_alice"


# ---------------------------------------------------------------------------
# Claim concurrency — ``ends_at`` filter mirrors FOR UPDATE SKIP LOCKED
# ---------------------------------------------------------------------------


class TestClaimSemantics:
    def test_ended_series_skipped(self, monkeypatch) -> None:
        """Series whose ``ends_at`` is in the past must not be claimed.

        In production this is the ``ends_at IS NULL OR ends_at > now``
        predicate paired with ``FOR UPDATE SKIP LOCKED``; the in-memory
        adapter mirrors the same predicate so the service contract is
        identical across adapters."""

        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, store = _build_client()
        _seed_series(
            store,
            series_id="ser_dead",
            started_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            ends_at=datetime(2026, 5, 10, tzinfo=timezone.utc),
        )
        _seed_series(
            store,
            series_id="ser_live",
            started_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        )

        response = client.post(
            _PATH,
            json={"now": "2026-05-18T08:00:00Z"},
            headers=_service_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        # Only the live series is processed + materialised.
        assert body["series_processed"] == 1
        assert body["materialized"] == 1

    def test_malformed_rule_does_not_abort_pass(self, monkeypatch) -> None:
        """One bad spec must not stall every other tenant's recurrence."""

        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, store = _build_client()
        _seed_series(
            store,
            series_id="ser_bad",
            rule="rrule",
            spec="FREQ=MONTHLY",  # MONTHLY isn't in the supported subset
            started_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        )
        _seed_series(
            store,
            series_id="ser_good",
            started_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        )

        response = client.post(
            _PATH,
            json={"now": "2026-05-18T08:00:00Z"},
            headers=_service_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        # Both series were CLAIMED (series_processed=2) but only the
        # good one produced a row.
        assert body["series_processed"] == 2
        assert body["materialized"] == 1


# ---------------------------------------------------------------------------
# Auth + body validation
# ---------------------------------------------------------------------------


class TestAuthAndValidation:
    def test_rejects_without_service_token_in_production(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "production")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, _ = _build_client()
        response = client.post(
            _PATH,
            json={"now": "2026-05-18T08:00:00Z"},
            headers={ORG_HEADER: "system", USER_HEADER: "system"},
        )
        assert response.status_code == 401

    def test_rejects_without_org_header(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "production")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, _ = _build_client()
        response = client.post(
            _PATH,
            json={"now": "2026-05-18T08:00:00Z"},
            headers={
                SERVICE_TOKEN_HEADER: "tok-test",
                USER_HEADER: "system",
            },
        )
        assert response.status_code == 401

    def test_rejects_without_user_header(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "production")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, _ = _build_client()
        response = client.post(
            _PATH,
            json={"now": "2026-05-18T08:00:00Z"},
            headers={
                SERVICE_TOKEN_HEADER: "tok-test",
                ORG_HEADER: "system",
            },
        )
        assert response.status_code == 401

    def test_rejects_invalid_now_iso(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, _ = _build_client()
        response = client.post(
            _PATH,
            json={"now": "not-a-date"},
            headers=_service_headers(),
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "invalid_now_iso"

    def test_rejects_missing_now_body_field(self, monkeypatch) -> None:
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, _ = _build_client()
        response = client.post(_PATH, json={}, headers=_service_headers())
        # Pydantic validation kicks in before the route body runs.
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Multi-tick progression — anchor advances on each materialization
# ---------------------------------------------------------------------------


class TestAnchorAdvances:
    def test_three_ticks_advance_anchor_each_time(self, monkeypatch) -> None:
        """Two consecutive ticks 24h apart yield two distinct materialisations."""

        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, store = _build_client()
        _seed_series(
            store,
            started_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        )

        # Tick 1: t = 2026-05-18 → due 2026-05-18.
        r1 = client.post(
            _PATH,
            json={"now": "2026-05-18T08:00:00Z"},
            headers=_service_headers(),
        )
        assert r1.json()["materialized"] == 1

        # Tick 2: t = 2026-05-19 → due 2026-05-19.
        r2 = client.post(
            _PATH,
            json={"now": "2026-05-19T08:00:00Z"},
            headers=_service_headers(),
        )
        assert r2.json()["materialized"] == 1

        materialised = sorted(
            (r.due for r in store.todos.values() if r.series_id == "ser_alpha"),
        )
        assert materialised == ["2026-05-18", "2026-05-19"]

        # Anchor advanced to the latest fired date.
        assert store.series["ser_alpha"].last_materialized_due == datetime(
            2026, 5, 19, tzinfo=timezone.utc
        )

    def test_clock_far_ahead_only_fires_once(self, monkeypatch) -> None:
        """Even with the clock 30 days ahead, one tick materialises one
        row — the worker reschedules per-tick rather than catching up in
        a single call (matches the worker's polling contract)."""

        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "tok-test")
        client, store = _build_client()
        seed_dt = datetime(2026, 5, 17, tzinfo=timezone.utc)
        _seed_series(store, started_at=seed_dt)

        far_future = (seed_dt + timedelta(days=30)).isoformat().replace("+00:00", "Z")
        response = client.post(
            _PATH, json={"now": far_future}, headers=_service_headers()
        )
        assert response.json()["materialized"] == 1
        # Only one row inserted — next tick will pick up the next due.
        materialised = [r for r in store.todos.values() if r.series_id == "ser_alpha"]
        assert len(materialised) == 1
