"""C8 Phase 3 — write-time retention_until stamping + recompute tests.

These tests verify:
  1. The port declares ``recompute_retention_until_for_policy``.
  2. The in-memory adapter satisfies the protocol.
  3. The retention-routes helper correctly calls recompute after upsert /
     delete and resolves the fallback TTL on delete.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_runtime.api.ports import PersistencePort
from agent_runtime.persistence.records.retention import (
    RetentionKind,
    RetentionPolicyRecord,
    RetentionScope,
)
from agent_runtime.retention import DEPLOYMENT_DEFAULT_TTL_SECONDS
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore


def _policy(
    org_id: str,
    kind: RetentionKind,
    ttl_seconds: int,
    scope: RetentionScope = RetentionScope.ORG,
    resource_id: str | None = None,
    policy_id: str = "pol_1",
) -> RetentionPolicyRecord:
    return RetentionPolicyRecord(
        id=policy_id,
        org_id=org_id,
        scope=scope,
        resource_id=resource_id,
        kind=kind,
        ttl_seconds=ttl_seconds,
    )


class _FakePersistence:
    """Minimal stub tracking upsert/delete/recompute calls."""

    def __init__(
        self,
        *,
        policies: dict[str, tuple[RetentionPolicyRecord, ...]] | None = None,
    ) -> None:
        self._policies = policies or {}
        self.upsert_calls: list[RetentionPolicyRecord] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.recompute_calls: list[dict[str, Any]] = []

    async def list_retention_policies(
        self, *, org_id: str
    ) -> tuple[RetentionPolicyRecord, ...]:
        return self._policies.get(org_id, ())

    async def upsert_retention_policy(
        self, record: RetentionPolicyRecord
    ) -> RetentionPolicyRecord:
        self.upsert_calls.append(record)
        existing = list(self._policies.get(record.org_id, ()))
        existing.append(record)
        self._policies[record.org_id] = tuple(existing)
        return record

    async def delete_retention_policy(self, *, org_id: str, policy_id: str) -> None:
        self.delete_calls.append({"org_id": org_id, "policy_id": policy_id})
        remaining = tuple(
            p for p in self._policies.get(org_id, ()) if p.id != policy_id
        )
        self._policies[org_id] = remaining

    async def recompute_retention_until_for_policy(
        self,
        *,
        org_id: str,
        kind: RetentionKind,
        scope: RetentionScope,
        resource_id: str | None,
        ttl_seconds: int | None,
    ) -> int:
        self.recompute_calls.append(
            {
                "org_id": org_id,
                "kind": kind,
                "scope": scope,
                "resource_id": resource_id,
                "ttl_seconds": ttl_seconds,
            }
        )
        return 0


class TestPortDeclaration:
    def test_recompute_method_on_protocol(self) -> None:
        assert hasattr(PersistencePort, "recompute_retention_until_for_policy")

    def test_in_memory_adapter_satisfies_protocol(self) -> None:
        store = InMemoryRuntimeApiStore()
        assert isinstance(store, PersistencePort)

    @pytest.mark.asyncio
    async def test_in_memory_recompute_returns_zero(self) -> None:
        store = InMemoryRuntimeApiStore()
        result = await store.recompute_retention_until_for_policy(
            org_id="org_a",
            kind=RetentionKind.MESSAGES,
            scope=RetentionScope.ORG,
            resource_id=None,
            ttl_seconds=86400,
        )
        assert result == 0


class TestUpsertRecompute:
    """After upsert_policy, recompute is called with the new TTL."""

    @pytest.mark.asyncio
    async def test_recompute_called_after_upsert(self) -> None:
        from runtime_api.http.retention_routes import RetentionAdminRoutes

        persistence = _FakePersistence()
        record = _policy("org_a", RetentionKind.MESSAGES, 7200)

        request = _make_request(persistence)
        await RetentionAdminRoutes.upsert_policy.__func__(
            RetentionAdminRoutes,
            request,
            _make_upsert_payload(record),
            org_id="org_a",
            user_id="u1",
        )

        assert len(persistence.recompute_calls) == 1
        call = persistence.recompute_calls[0]
        assert call["org_id"] == "org_a"
        assert call["kind"] is RetentionKind.MESSAGES
        assert call["scope"] is RetentionScope.ORG
        assert call["ttl_seconds"] == 7200

    @pytest.mark.asyncio
    async def test_recompute_passes_conversation_scope(self) -> None:
        from runtime_api.http.retention_routes import RetentionAdminRoutes

        persistence = _FakePersistence()
        record = _policy(
            "org_a",
            RetentionKind.EVENTS,
            3600,
            scope=RetentionScope.CONVERSATION,
            resource_id="conv_1",
        )

        request = _make_request(persistence)
        await RetentionAdminRoutes.upsert_policy.__func__(
            RetentionAdminRoutes,
            request,
            _make_upsert_payload(record),
            org_id="org_a",
            user_id="u1",
        )

        call = persistence.recompute_calls[0]
        assert call["scope"] is RetentionScope.CONVERSATION
        assert call["resource_id"] == "conv_1"


class TestDeleteRecompute:
    """After delete_policy, recompute uses the fallback TTL from remaining policies."""

    @pytest.mark.asyncio
    async def test_recompute_called_after_delete(self) -> None:
        from runtime_api.http.retention_routes import RetentionAdminRoutes

        pol = _policy("org_a", RetentionKind.MESSAGES, 7200, policy_id="pol_1")
        persistence = _FakePersistence(policies={"org_a": (pol,)})

        request = _make_request(persistence)
        await RetentionAdminRoutes.delete_policy.__func__(
            RetentionAdminRoutes,
            request,
            "pol_1",
            org_id="org_a",
            user_id="u1",
        )

        assert len(persistence.recompute_calls) == 1

    @pytest.mark.asyncio
    async def test_recompute_uses_deployment_default_when_no_remaining(self) -> None:
        from runtime_api.http.retention_routes import RetentionAdminRoutes

        pol = _policy("org_a", RetentionKind.MESSAGES, 7200, policy_id="pol_1")
        persistence = _FakePersistence(policies={"org_a": (pol,)})

        request = _make_request(persistence)
        await RetentionAdminRoutes.delete_policy.__func__(
            RetentionAdminRoutes,
            request,
            "pol_1",
            org_id="org_a",
            user_id="u1",
        )

        call = persistence.recompute_calls[0]
        # No remaining policy → falls back to deployment default (365 days).
        assert (
            call["ttl_seconds"]
            == DEPLOYMENT_DEFAULT_TTL_SECONDS[RetentionKind.MESSAGES]
        )

    @pytest.mark.asyncio
    async def test_recompute_uses_null_when_no_default(self) -> None:
        from runtime_api.http.retention_routes import RetentionAdminRoutes

        # MEMORY_ITEMS has no deployment default — fallback is None.
        pol = _policy("org_a", RetentionKind.MEMORY_ITEMS, 86400, policy_id="pol_1")
        persistence = _FakePersistence(policies={"org_a": (pol,)})

        request = _make_request(persistence)
        await RetentionAdminRoutes.delete_policy.__func__(
            RetentionAdminRoutes,
            request,
            "pol_1",
            org_id="org_a",
            user_id="u1",
        )

        call = persistence.recompute_calls[0]
        assert call["ttl_seconds"] is None

    @pytest.mark.asyncio
    async def test_no_recompute_when_policy_not_found(self) -> None:
        from runtime_api.http.retention_routes import RetentionAdminRoutes

        # Empty policies — deleting a non-existent id should not recompute.
        persistence = _FakePersistence(policies={})

        request = _make_request(persistence)
        await RetentionAdminRoutes.delete_policy.__func__(
            RetentionAdminRoutes,
            request,
            "nonexistent",
            org_id="org_a",
            user_id="u1",
        )

        assert len(persistence.recompute_calls) == 0

    @pytest.mark.asyncio
    async def test_recompute_falls_back_to_remaining_org_policy(self) -> None:
        from runtime_api.http.retention_routes import RetentionAdminRoutes

        # CONVERSATION-scope policy deleted; ORG-scope remains → fallback TTL is org TTL.
        org_pol = _policy("org_a", RetentionKind.MESSAGES, 86400, policy_id="org_pol")
        conv_pol = _policy(
            "org_a",
            RetentionKind.MESSAGES,
            3600,
            scope=RetentionScope.CONVERSATION,
            resource_id="conv_1",
            policy_id="conv_pol",
        )
        persistence = _FakePersistence(policies={"org_a": (org_pol, conv_pol)})

        request = _make_request(persistence)
        await RetentionAdminRoutes.delete_policy.__func__(
            RetentionAdminRoutes,
            request,
            "conv_pol",
            org_id="org_a",
            user_id="u1",
        )

        call = persistence.recompute_calls[0]
        # CONVERSATION policy deleted; resolver falls back to ORG (86400 s).
        assert call["ttl_seconds"] == 86400


# ---------------------------------------------------------------------------
# Test-local helpers that build minimal fakes for FastAPI Request + payload
# ---------------------------------------------------------------------------


def _make_request(persistence) -> Any:
    """Fake FastAPI Request with app.state.runtime_persistence set."""

    class _State:
        runtime_persistence = persistence

    class _App:
        state = _State()

    class _Request:
        app = _App()
        # RuntimeApiRoutes.scoped_identity reads these headers:
        state = type("S", (), {"identity": None})()

        def __init__(self):
            self._headers: dict[str, str] = {
                "x-enterprise-org-id": "org_a",
                "x-enterprise-user-id": "u1",
            }

        @property
        def headers(self):
            return self._headers

    return _Request()


class _UpsertPayload:
    def __init__(self, record: RetentionPolicyRecord) -> None:
        self.scope = record.scope
        self.resource_id = record.resource_id
        self.kind = record.kind
        self.ttl_seconds = record.ttl_seconds


def _make_upsert_payload(record: RetentionPolicyRecord) -> _UpsertPayload:
    return _UpsertPayload(record)
