"""Tests for the MCP → connector projection helper (PR-E.3 Decision D1).

``mcp_upsert_input_from_server`` is the service-layer projection from the
MCP registry's :class:`McpServerRecord` into the connectors store's
:class:`McpUpsertInput`. The store never sees ``McpServerRecord`` — this
helper is where the honest auth_state → connector-status mapping lives.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend_app.connectors.service import (
    mcp_connector_slug,
    mcp_upsert_input_from_server,
    project_mcp_status,
)
from backend_app.connectors.store import ConnectorRecord, ConnectorScopeEntry
from backend_app.contracts import (
    McpAuthMode,
    McpAuthState,
    McpServerRecord,
)


def _server(**overrides) -> McpServerRecord:
    base: dict = {
        "org_id": "org_acme",
        "user_id": "usr_sarah",
        "name": "drive_mcp",
        "display_name": "Drive MCP",
        "url": "https://mcp.example.com",
    }
    base.update(overrides)
    return McpServerRecord(**base)


class TestSlug:
    def test_catalog_install_strips_seed_prefix(self) -> None:
        record = _server(server_id="seed:github", name="github")
        assert mcp_connector_slug(record) == "github"

    def test_custom_server_uses_stable_name(self) -> None:
        record = _server(name="drive_mcp")
        assert mcp_connector_slug(record) == "drive_mcp"


class TestStatusProjection:
    @pytest.mark.parametrize(
        ("auth_state", "expected_status", "expected_reason"),
        [
            (McpAuthState.AUTHENTICATED, "connected", None),
            (McpAuthState.AUTH_SKIPPED, "connected", "auth_skipped"),
            (McpAuthState.AUTH_PENDING, "disconnected", "auth_pending"),
            (McpAuthState.UNAUTHENTICATED, "disconnected", "unauthenticated"),
            (McpAuthState.AUTH_FAILED, "error", "auth_failed"),
            (McpAuthState.AUTH_UNSUPPORTED, "error", "auth_unsupported"),
        ],
    )
    def test_oauth_server_states(
        self,
        auth_state: McpAuthState,
        expected_status: str,
        expected_reason: str | None,
    ) -> None:
        record = _server(auth_mode=McpAuthMode.OAUTH2, auth_state=auth_state)
        assert project_mcp_status(record) == (expected_status, expected_reason)

    def test_no_auth_server_is_connected(self) -> None:
        record = _server(
            auth_mode=McpAuthMode.NONE,
            auth_state=McpAuthState.UNAUTHENTICATED,
        )
        assert project_mcp_status(record) == ("connected", None)

    def test_disabled_wins_over_auth_state(self) -> None:
        record = _server(
            enabled=False,
            auth_state=McpAuthState.AUTHENTICATED,
        )
        assert project_mcp_status(record) == ("disconnected", "disabled")


class TestUpsertInputProjection:
    def test_maps_identity_and_vault_ref(self) -> None:
        record = _server(auth_state=McpAuthState.AUTHENTICATED)
        mcp_input = mcp_upsert_input_from_server(record)
        assert mcp_input.tenant_id == "org_acme"
        assert mcp_input.owner_user_id == "usr_sarah"
        assert mcp_input.slug == "drive_mcp"
        assert mcp_input.display_name == "Drive MCP"
        assert mcp_input.status == "connected"
        assert mcp_input.vault_ref == f"mcp:{record.server_id}"
        assert mcp_input.existing_id is None

    def test_scopes_from_required_scopes_granted_when_connected(self) -> None:
        record = _server(
            auth_state=McpAuthState.AUTHENTICATED,
            required_scopes=("drive.read", "drive.write"),
        )
        mcp_input = mcp_upsert_input_from_server(record)
        assert [s.scope for s in mcp_input.scopes] == ["drive.read", "drive.write"]
        assert all(s.granted for s in mcp_input.scopes)

    def test_scopes_not_granted_when_not_connected(self) -> None:
        record = _server(
            auth_state=McpAuthState.AUTH_PENDING,
            required_scopes=("drive.read",),
        )
        mcp_input = mcp_upsert_input_from_server(record)
        assert [s.granted for s in mcp_input.scopes] == [False]

    def test_default_scopes_fallback(self) -> None:
        record = _server(
            server_id="seed:github",
            auth_state=McpAuthState.AUTHENTICATED,
            default_scopes=("repo",),
        )
        mcp_input = mcp_upsert_input_from_server(record)
        assert [s.scope for s in mcp_input.scopes] == ["repo"]

    def test_authenticated_sets_last_sync_at(self) -> None:
        record = _server(auth_state=McpAuthState.AUTHENTICATED)
        mcp_input = mcp_upsert_input_from_server(record)
        assert mcp_input.last_sync_at == record.updated_at

    def test_error_sets_last_error_at(self) -> None:
        record = _server(auth_state=McpAuthState.AUTH_FAILED)
        mcp_input = mcp_upsert_input_from_server(record)
        assert mcp_input.last_error_at is not None
        assert mcp_input.status == "error"

    def test_existing_row_preserves_sync_bookkeeping_and_id(self) -> None:
        synced_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
        errored_at = datetime(2026, 7, 2, tzinfo=timezone.utc)
        existing = ConnectorRecord(
            id="conn_existing",
            tenant_id="org_acme",
            slug="drive_mcp",
            display_name="Drive MCP",
            owner_user_id="usr_sarah",
            status="connected",
            scopes=[ConnectorScopeEntry(scope="drive.read")],
            last_sync_at=synced_at,
            last_error_at=errored_at,
        )
        record = _server(auth_state=McpAuthState.AUTH_PENDING)
        mcp_input = mcp_upsert_input_from_server(record, existing=existing)
        assert mcp_input.existing_id == "conn_existing"
        assert mcp_input.last_sync_at == synced_at
        assert mcp_input.last_error_at == errored_at
