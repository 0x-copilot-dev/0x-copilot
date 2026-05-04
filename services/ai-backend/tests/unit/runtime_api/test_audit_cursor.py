"""C9 internal audit-cursor endpoint tests.

The endpoint is the SIEM pump's read source for ai-backend's
``runtime_audit_log``. It runs cross-tenant via the worker role and is
gated by service-token auth.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_runtime.api.service import RuntimeApiService
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import (
    AsyncInMemoryRuntimeApiStore,
    InMemoryRuntimeApiStore,
)
from runtime_api.app import RuntimeApiAppFactory


class TestAuditCursor:
    def _client(self) -> TestClient:
        sync_store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(sync_store)
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        service = RuntimeApiService(
            persistence=async_store,
            event_store=async_store,
            queue=async_store,
            settings=settings,
        )
        return TestClient(RuntimeApiAppFactory.create_app(service))

    def test_endpoint_returns_empty_in_dev(self) -> None:
        """Without ENTERPRISE_SERVICE_TOKEN the route is open in dev; the
        in-memory adapter returns an empty event list (no audit data
        seeded)."""

        client = self._client()
        response = client.get("/internal/v1/audit/cursor", params={"limit": 100})
        assert response.status_code == 200
        body = response.json()
        assert body["events"] == []
        # No prior cursor → next_cursor is also None (no rows to advance past).
        assert body["next_cursor"] is None

    def test_limit_clamped_to_valid_range(self) -> None:
        client = self._client()
        # Out-of-range -> rejected; backend's RuntimeApiError middleware
        # surfaces validation as 400. The exact 4xx code is less load-
        # bearing than "rejection happens before the SQL fires".
        response = client.get("/internal/v1/audit/cursor", params={"limit": 5000})
        assert response.status_code in {400, 422}
