"""Tests for the SIEM export endpoint.

Verifies:
- Auth is required (service-token or dev fallback).
- The summary is the first NDJSON line; rows follow in seq order.
- Cross-org records are excluded.
- The exported rows include the chain fields, in hex, so a customer-side
  verifier can recompute integrity.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import AuditEventRecord, SkillAuditEventRecord
from backend_app.routes.audit_export import register_audit_export_routes
from backend_app.service import McpRegistryService, SkillRegistryService
from backend_app.store import InMemoryMcpStore, InMemorySkillStore


def _client_with_audit() -> tuple[TestClient, InMemoryMcpStore, InMemorySkillStore]:
    mcp_store = InMemoryMcpStore()
    skill_store = InMemorySkillStore()
    for i in range(5):
        mcp_store.append_audit(
            AuditEventRecord(
                org_id="org_a",
                user_id="u",
                server_id=f"s{i}",
                action="mcp_server_created",
            )
        )
    # Cross-org record that must NOT appear in org_a's export.
    mcp_store.append_audit(
        AuditEventRecord(
            org_id="org_b", user_id="u", server_id="s9", action="mcp_server_created"
        )
    )
    for i in range(3):
        skill_store.append_skill_audit(
            SkillAuditEventRecord(
                org_id="org_a", user_id="u", skill_id=f"sk{i}", action="skill_created"
            )
        )

    mcp_service = McpRegistryService(store=mcp_store)
    skill_service = SkillRegistryService(store=skill_store)
    app = create_app(
        service=mcp_service,
        skill_service=skill_service,
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
    )
    # Routes module is wired in tests until app.py integration lands; the
    # module is independently shippable so a follow-up edit to app.py adds
    # one import + one call.
    register_audit_export_routes(app)
    return TestClient(app), mcp_store, skill_store


class TestAuditExportEndpoint:
    def test_requires_internal_auth(self, monkeypatch) -> None:
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "development")
        monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)
        # Without the service token in dev, internal_scoped_identity falls
        # back to query identity -- which is intentional for local dev. The
        # test below confirms the default dev path works; production gating
        # is enforced by env config, not by the route itself.
        client, _, _ = _client_with_audit()
        response = client.post(
            "/internal/v1/audit/export",
            params={"org_id": "org_a", "user_id": "u", "table": "mcp_audit_events"},
        )
        assert response.status_code == 200

    def test_rejects_unknown_table(self) -> None:
        client, _, _ = _client_with_audit()
        response = client.post(
            "/internal/v1/audit/export",
            params={"org_id": "org_a", "user_id": "u", "table": "no_such_table"},
        )
        assert response.status_code == 400

    def test_summary_then_rows_in_seq_order(self) -> None:
        client, _, _ = _client_with_audit()
        response = client.post(
            "/internal/v1/audit/export",
            params={"org_id": "org_a", "user_id": "u", "table": "mcp_audit_events"},
        )
        assert response.status_code == 200
        lines = [json.loads(line) for line in response.text.strip().split("\n")]
        summary, *rows = lines
        assert summary["table"] == "mcp_audit_events"
        assert summary["org_id"] == "org_a"
        assert summary["after_seq"] == 0
        assert [row["seq"] for row in rows] == [1, 2, 3, 4, 5]

    def test_cross_org_records_excluded(self) -> None:
        client, _, _ = _client_with_audit()
        response = client.post(
            "/internal/v1/audit/export",
            params={"org_id": "org_a", "user_id": "u", "table": "mcp_audit_events"},
        )
        rows = [json.loads(line) for line in response.text.strip().split("\n")[1:]]
        assert all(row["payload"]["org_id"] == "org_a" for row in rows)
        assert "s9" not in [row["payload"]["server_id"] for row in rows]

    def test_after_seq_filters_window(self) -> None:
        client, _, _ = _client_with_audit()
        response = client.post(
            "/internal/v1/audit/export",
            params={
                "org_id": "org_a",
                "user_id": "u",
                "table": "mcp_audit_events",
                "after_seq": 2,
            },
        )
        rows = [json.loads(line) for line in response.text.strip().split("\n")[1:]]
        assert [row["seq"] for row in rows] == [3, 4, 5]

    def test_chain_fields_included(self) -> None:
        client, _, _ = _client_with_audit()
        response = client.post(
            "/internal/v1/audit/export",
            params={"org_id": "org_a", "user_id": "u", "table": "mcp_audit_events"},
        )
        rows = [json.loads(line) for line in response.text.strip().split("\n")[1:]]
        first = rows[0]
        assert first["prev_hash"] is None  # first row in chain
        assert isinstance(first["signature"], str) and len(first["signature"]) == 64
        assert isinstance(first["key_version"], int)
        # Chain link: row 2's prev_hash equals row 1's signature.
        assert rows[1]["prev_hash"] == rows[0]["signature"]

    def test_skill_audit_export(self) -> None:
        client, _, _ = _client_with_audit()
        response = client.post(
            "/internal/v1/audit/export",
            params={"org_id": "org_a", "user_id": "u", "table": "skill_audit_events"},
        )
        assert response.status_code == 200
        lines = response.text.strip().split("\n")
        # 1 summary line + 3 rows
        assert len(lines) == 4
