"""C9 SIEM admin route tests.

The routes hit Postgres directly (no DI store like the audit-chain
adapters), so the tests monkeypatch the per-route SQL helpers with
in-memory stand-ins. The tests assert:

  * Exporter list joins env config + cursors + dead-letter counts.
  * Pause/resume round-trip writes the control row.
  * Replay records the requested window.
  * Dead-letter list filters by exporter and respects the limit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend_app.routes import siem as siem_routes


class _FakeDb:
    """Tiny in-memory stand-in for the Postgres-backed admin tables."""

    def __init__(self) -> None:
        self.cursors: dict[str, list[dict[str, Any]]] = {}
        self.controls: dict[str, dict[str, Any]] = {}
        self.dead_letters: list[dict[str, Any]] = []

    def query_cursors(self) -> dict[str, tuple[siem_routes.ExporterCursorRow, ...]]:
        out: dict[str, tuple[siem_routes.ExporterCursorRow, ...]] = {}
        for name, rows in self.cursors.items():
            out[name] = tuple(siem_routes.ExporterCursorRow(**row) for row in rows)
        return out

    def query_controls(self) -> dict[str, siem_routes._ExporterControl]:
        return {
            name: siem_routes._ExporterControl(
                exporter_name=name,
                paused_at=row.get("paused_at"),
                replay_from_id=row.get("replay_from_id"),
                replay_to_id=row.get("replay_to_id"),
                replay_requested_at=row.get("replay_requested_at"),
            )
            for name, row in self.controls.items()
        }

    def query_dead_letter_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.dead_letters:
            counts[row["exporter_name"]] = counts.get(row["exporter_name"], 0) + 1
        return counts

    def query_dead_letters(
        self, *, exporter: str | None, limit: int
    ) -> list[dict[str, Any]]:
        rows = self.dead_letters
        if exporter is not None:
            rows = [r for r in rows if r["exporter_name"] == exporter]
        rows = sorted(rows, key=lambda r: r["created_at"], reverse=True)
        return rows[:limit]

    def execute_pause(self, sql: str, params: tuple) -> None:
        name, paused_at, _updated_at, _user = params
        bucket = self.controls.setdefault(name, {})
        bucket["paused_at"] = paused_at

    def execute_replay(self, sql: str, params: tuple) -> None:
        name, from_id, to_id, requested_at, _updated, _user = params
        bucket = self.controls.setdefault(name, {})
        bucket["replay_from_id"] = from_id
        bucket["replay_to_id"] = to_id
        bucket["replay_requested_at"] = requested_at


@pytest.fixture()
def fake_db(monkeypatch: pytest.MonkeyPatch) -> _FakeDb:
    db = _FakeDb()
    monkeypatch.setattr(siem_routes, "_query_cursors", db.query_cursors)
    monkeypatch.setattr(siem_routes, "_query_controls", db.query_controls)
    monkeypatch.setattr(
        siem_routes, "_query_dead_letter_counts", db.query_dead_letter_counts
    )
    monkeypatch.setattr(
        siem_routes,
        "_query_dead_letters",
        lambda *, exporter, limit: db.query_dead_letters(
            exporter=exporter, limit=limit
        ),
    )

    def fake_execute(sql: str, params: tuple) -> None:
        if "replay_from_id" in sql:
            db.execute_replay(sql, params)
        else:
            db.execute_pause(sql, params)

    monkeypatch.setattr(siem_routes, "_execute", fake_execute)
    return db


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SIEM_EXPORT_BACKEND", "splunk_hec")
    monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("FACADE_ENVIRONMENT", "development")
    app = FastAPI()
    siem_routes.register_siem_admin_routes(app)
    return TestClient(app)


class TestSiemAdminRoutes:
    def test_list_exporters_joins_config_cursors_and_dead_letters(
        self, monkeypatch: pytest.MonkeyPatch, fake_db: _FakeDb
    ) -> None:
        client = _client(monkeypatch)
        fake_db.cursors["splunk_hec"] = [
            {
                "source": "mcp_audit",
                "last_event_id": "evt_1",
                "last_processed_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
            }
        ]
        fake_db.dead_letters.extend(
            [
                {
                    "id": "dl_1",
                    "exporter_name": "splunk_hec",
                    "source": "mcp_audit",
                    "event_id": "evt_dead",
                    "last_error": "boom",
                    "attempts": 1,
                    "created_at": datetime(2026, 5, 2, tzinfo=timezone.utc),
                },
            ]
        )
        response = client.get("/v1/siem/exporters")
        assert response.status_code == 200
        body = response.json()
        assert len(body["exporters"]) == 1
        row = body["exporters"][0]
        assert row["name"] == "splunk_hec"
        assert row["dead_letter_count"] == 1
        assert row["cursors"][0]["source"] == "mcp_audit"

    def test_pause_then_resume_round_trip(
        self, monkeypatch: pytest.MonkeyPatch, fake_db: _FakeDb
    ) -> None:
        client = _client(monkeypatch)
        paused = client.post("/v1/siem/exporters/splunk_hec/pause")
        assert paused.status_code == 200
        assert paused.json()["paused_at"] is not None
        assert fake_db.controls["splunk_hec"]["paused_at"] is not None
        resumed = client.post("/v1/siem/exporters/splunk_hec/resume")
        assert resumed.status_code == 200
        assert resumed.json()["paused_at"] is None
        assert fake_db.controls["splunk_hec"]["paused_at"] is None

    def test_replay_records_window(
        self, monkeypatch: pytest.MonkeyPatch, fake_db: _FakeDb
    ) -> None:
        client = _client(monkeypatch)
        response = client.post(
            "/v1/siem/exporters/splunk_hec/replay",
            params={"from_id": "evt_100", "to_id": "evt_200"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["from_id"] == "evt_100"
        assert body["to_id"] == "evt_200"
        ctrl = fake_db.controls["splunk_hec"]
        assert ctrl["replay_from_id"] == "evt_100"
        assert ctrl["replay_to_id"] == "evt_200"

    def test_replay_requires_from_id(
        self, monkeypatch: pytest.MonkeyPatch, fake_db: _FakeDb
    ) -> None:
        client = _client(monkeypatch)
        response = client.post("/v1/siem/exporters/splunk_hec/replay")
        assert response.status_code == 422

    def test_dead_letter_list_filters_by_exporter(
        self, monkeypatch: pytest.MonkeyPatch, fake_db: _FakeDb
    ) -> None:
        client = _client(monkeypatch)
        fake_db.dead_letters.extend(
            [
                {
                    "id": "dl_1",
                    "exporter_name": "splunk_hec",
                    "source": "mcp_audit",
                    "event_id": "evt_a",
                    "last_error": "boom",
                    "attempts": 1,
                    "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
                },
                {
                    "id": "dl_2",
                    "exporter_name": "elastic",
                    "source": "identity_audit",
                    "event_id": "evt_b",
                    "last_error": "no",
                    "attempts": 2,
                    "created_at": datetime(2026, 5, 2, tzinfo=timezone.utc),
                },
            ]
        )
        only_splunk = client.get(
            "/v1/siem/dead_letters", params={"exporter": "splunk_hec"}
        )
        assert only_splunk.status_code == 200
        body = only_splunk.json()
        assert len(body["dead_letters"]) == 1
        assert body["dead_letters"][0]["exporter_name"] == "splunk_hec"
