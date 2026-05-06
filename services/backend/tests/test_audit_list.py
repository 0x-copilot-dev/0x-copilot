"""Tests for the unified audit list endpoint (PR 7.1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.audit_reader import (
    AuditCursor,
    AuditFilters,
    AuditReader,
)
from backend_app.contracts import (
    AuditEventRecord,
    DeployImageDigest,
    IdentityAuditEventRecord,
    SkillAuditEventRecord,
    DeployAuditEventRecord,
)


@pytest.fixture(autouse=True)
def _audit_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_HMAC_KEY", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("AUDIT_HMAC_KEY_VERSION", "1")
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "svc-token")
    monkeypatch.setenv("ENTERPRISE_AUTH_SECRET", "auth-secret")
    monkeypatch.setenv("FACADE_ENVIRONMENT", "development")


def _build_app():  # type: ignore[no-untyped-def]
    app = create_app()
    return app


def _seed_mcp(app, *, org_id: str, user_id: str, action: str, when: datetime):  # type: ignore[no-untyped-def]
    app.state.mcp_service.store.append_audit(
        AuditEventRecord(
            org_id=org_id,
            user_id=user_id,
            server_id="srv-1",
            action=action,
            metadata={"hint": "x"},
            created_at=when,
        )
    )


def _seed_skill(app, *, org_id: str, user_id: str, action: str, when: datetime):  # type: ignore[no-untyped-def]
    app.state.skill_service.store.append_skill_audit(
        SkillAuditEventRecord(
            org_id=org_id,
            user_id=user_id,
            skill_id="skill-1",
            action=action,
            created_at=when,
        )
    )


def _seed_identity(app, *, org_id: str, action: str, when: datetime):  # type: ignore[no-untyped-def]
    app.state.identity_store.append_identity_audit(
        IdentityAuditEventRecord(
            org_id=org_id,
            actor_user_id="user_actor",
            subject_user_id="user_subject",
            action=action,
            metadata={"reason": "test"},
            created_at=when,
        )
    )


def _seed_deploy(app, *, org_id: str, when: datetime):  # type: ignore[no-untyped-def]
    app.state.deploy_audit_service.store.append_deploy_audit(
        DeployAuditEventRecord(
            org_id=org_id,
            user_id="user_ci",
            tenant_id="acme",
            environment="production",
            release_sha="abcdef0",
            image_digests=[
                DeployImageDigest(component="web", digest="sha256:" + "0" * 64)
            ],
            approver="alice",
            workflow_run_url="https://gh.com/run/1",
            started_at=when - timedelta(seconds=10),
            completed_at=when,
            outcome="success",
            force_deploy=False,
            actor_kind="ci",
            created_at=when,
        )
    )


class TestAuditReader:
    def test_fans_out_across_streams(self) -> None:
        app = _build_app()
        now = datetime.now(timezone.utc)
        _seed_mcp(
            app,
            org_id="org_a",
            user_id="u1",
            action="mcp.server.installed",
            when=now - timedelta(seconds=4),
        )
        _seed_skill(
            app,
            org_id="org_a",
            user_id="u1",
            action="skill.created",
            when=now - timedelta(seconds=3),
        )
        _seed_identity(
            app, org_id="org_a", action="member.added", when=now - timedelta(seconds=2)
        )
        _seed_deploy(app, org_id="org_a", when=now - timedelta(seconds=1))

        reader = AuditReader(
            mcp_store=app.state.mcp_service.store,
            skill_store=app.state.skill_service.store,
            deploy_store=app.state.deploy_audit_service.store,
            identity_store=app.state.identity_store,
        )
        page = reader.list(
            org_id="org_a",
            filters=AuditFilters(),
            cursor=AuditCursor(),
            limit=50,
        )
        # All four streams contributed one row.
        streams = {row.stream for row in page.rows}
        assert "mcp_audit_events" in streams
        assert "skill_audit_events" in streams
        assert "identity_audit_events" in streams
        assert "deploy_audit_events" in streams
        # Newest-first.
        timestamps = [row.created_at for row in page.rows]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_filter_action_prefix(self) -> None:
        app = _build_app()
        now = datetime.now(timezone.utc)
        _seed_mcp(
            app,
            org_id="org_a",
            user_id="u1",
            action="mcp.server.installed",
            when=now - timedelta(seconds=4),
        )
        _seed_skill(
            app,
            org_id="org_a",
            user_id="u1",
            action="skill.created",
            when=now - timedelta(seconds=3),
        )
        reader = AuditReader(
            mcp_store=app.state.mcp_service.store,
            skill_store=app.state.skill_service.store,
            deploy_store=app.state.deploy_audit_service.store,
            identity_store=app.state.identity_store,
        )
        page = reader.list(
            org_id="org_a",
            filters=AuditFilters(action_prefix="skill."),
            cursor=AuditCursor(),
            limit=50,
        )
        assert len(page.rows) == 1
        assert page.rows[0].stream == "skill_audit_events"

    def test_cross_org_isolation(self) -> None:
        app = _build_app()
        now = datetime.now(timezone.utc)
        _seed_mcp(app, org_id="org_a", user_id="u1", action="mcp.x", when=now)
        _seed_mcp(app, org_id="org_b", user_id="u1", action="mcp.x", when=now)
        reader = AuditReader(
            mcp_store=app.state.mcp_service.store,
            skill_store=app.state.skill_service.store,
            deploy_store=app.state.deploy_audit_service.store,
            identity_store=app.state.identity_store,
        )
        page = reader.list(
            org_id="org_a",
            filters=AuditFilters(),
            cursor=AuditCursor(),
            limit=50,
        )
        # No org_b row leaked.
        assert all(row.org_id == "org_a" for row in page.rows)

    def test_cursor_round_trip_avoids_duplicates(self) -> None:
        app = _build_app()
        now = datetime.now(timezone.utc)
        for index in range(5):
            _seed_mcp(
                app,
                org_id="org_a",
                user_id="u1",
                action="mcp.x",
                when=now - timedelta(seconds=index),
            )
        reader = AuditReader(
            mcp_store=app.state.mcp_service.store,
            skill_store=app.state.skill_service.store,
            deploy_store=app.state.deploy_audit_service.store,
            identity_store=app.state.identity_store,
        )
        page1 = reader.list(
            org_id="org_a",
            filters=AuditFilters(),
            cursor=AuditCursor(),
            limit=2,
        )
        assert len(page1.rows) == 2
        assert page1.next_cursor is not None
        cursor = AuditCursor.decode(page1.next_cursor)
        page2 = reader.list(
            org_id="org_a",
            filters=AuditFilters(),
            cursor=cursor,
            limit=2,
        )
        assert len(page2.rows) == 2
        # No duplicates between pages — seq strictly higher.
        seen_ids = {row.audit_id for row in page1.rows}
        for row in page2.rows:
            assert row.audit_id not in seen_ids

    def test_degraded_when_one_stream_errors(self) -> None:
        app = _build_app()
        now = datetime.now(timezone.utc)
        _seed_mcp(app, org_id="org_a", user_id="u1", action="mcp.x", when=now)

        class _BoomStore:
            def list_skill_audit_events(self, **kwargs):  # type: ignore[no-untyped-def]
                raise RuntimeError("boom")

        reader = AuditReader(
            mcp_store=app.state.mcp_service.store,
            skill_store=_BoomStore(),
            deploy_store=app.state.deploy_audit_service.store,
            identity_store=app.state.identity_store,
        )
        page = reader.list(
            org_id="org_a",
            filters=AuditFilters(),
            cursor=AuditCursor(),
            limit=50,
        )
        assert "skill_audit_events" in page.degraded_streams
        # The mcp row still appears.
        assert any(row.stream == "mcp_audit_events" for row in page.rows)

    def test_invalid_cursor_raises(self) -> None:
        with pytest.raises(ValueError):
            AuditCursor.decode("not-base64-or-json")


class TestAuditListEndpoint:
    def test_unauthorized_without_admin_scope(self) -> None:
        app = _build_app()
        client = TestClient(app)
        # No service token → auth rejects.
        response = client.get(
            "/internal/v1/audit/list",
            params={"org_id": "org_a", "user_id": "u1"},
        )
        assert response.status_code in (401, 403)

    def test_returns_unified_page(self) -> None:
        app = _build_app()
        now = datetime.now(timezone.utc)
        _seed_mcp(
            app,
            org_id="org_a",
            user_id="u1",
            action="mcp.x",
            when=now - timedelta(seconds=2),
        )
        _seed_skill(
            app,
            org_id="org_a",
            user_id="u1",
            action="skill.x",
            when=now - timedelta(seconds=1),
        )
        client = TestClient(app)
        response = client.get(
            "/internal/v1/audit/list",
            params={"org_id": "org_a", "user_id": "u1"},
            headers={
                "x-enterprise-service-token": "svc-token",
                "x-enterprise-org-id": "org_a",
                "x-enterprise-user-id": "u1",
                "x-enterprise-permission-scopes": "admin:audit_export",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert "rows" in body
        assert len(body["rows"]) == 2
        assert body["rows"][0]["stream"] in {"skill_audit_events", "mcp_audit_events"}
