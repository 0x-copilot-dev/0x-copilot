"""HTTP route tests for ``/v1/todo-extractions/*`` (P3-A2)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi.testclient import TestClient

from agent_runtime.api.todo_extractions import TodoExtractionsService
from agent_runtime.persistence.records import (
    TodoExtractionRecord,
    TodoExtractionState,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.todo_extraction_store import (
    InMemoryTodoExtractionStore,
)
from runtime_api.app import RuntimeApiAppFactory


# -- helpers -----------------------------------------------------------------


def _identity_headers(
    *, org_id: str = "acme", user_id: str = "sarah"
) -> dict[str, str]:
    """Return the headers tests use to authenticate as a given user."""
    return {
        "x-enterprise-org-id": org_id,
        "x-enterprise-user-id": user_id,
    }


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


def _build_client(
    *,
    extraction_store: InMemoryTodoExtractionStore,
    http_transport: httpx.MockTransport | None = None,
    service_token: str | None = None,
    monkeypatch=None,
) -> TestClient:
    """Build a FastAPI test client with the extractions service installed.

    The route-level RBAC layer requires the caller to send the matching
    ``x-enterprise-service-token`` only when the env var is set. We leave
    the var unset by default so identity headers alone authenticate (the
    same dev path other route tests use). The accept-flow tests opt in
    by passing ``service_token`` so the forwarded backend call carries it.
    """
    backend_store = InMemoryRuntimeApiStore()
    ports = RuntimeAdapterFactory.from_store(backend_store)
    app = RuntimeApiAppFactory.create_app(ports=ports, settings=_settings())

    if monkeypatch is not None:
        # Only register the env var when the test explicitly opts in;
        # leaving it unset lets identity headers alone authenticate the
        # caller, matching the dev path other route tests rely on.
        if service_token is not None:
            monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", service_token)
        else:
            monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)

    http_client = (
        httpx.AsyncClient(transport=http_transport) if http_transport else None
    )
    service = TodoExtractionsService(
        store=extraction_store,
        http_client=http_client,
        backend_base_url="http://backend.test",
        clock=lambda: datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
    )
    app.state.todo_extractions_service = service
    return TestClient(app)


# -- tests -------------------------------------------------------------------


class TestListPending:
    """``GET /v1/todo-extractions``"""

    def test_returns_owner_pending_rows(self, monkeypatch) -> None:
        store = InMemoryTodoExtractionStore()
        sarah_row = TodoExtractionRecord(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-a",
            conversation_id="conv-a",
            proposed_text="ship the deck",
        )
        other_org_row = TodoExtractionRecord(
            org_id="globex",
            owner_user_id="sarah",
            run_id="run-b",
            conversation_id="conv-b",
            proposed_text="not yours",
        )
        marcus_row = TodoExtractionRecord(
            org_id="acme",
            owner_user_id="marcus",
            run_id="run-c",
            conversation_id="conv-c",
            proposed_text="also not yours",
        )
        # Seed the store synchronously via its internal dict — the
        # ``insert_many`` coroutine would otherwise need its own loop,
        # which fights TestClient's loop ownership. The dict mutation
        # mirrors what the async path produces.
        store.rows[sarah_row.id] = sarah_row
        store.rows[other_org_row.id] = other_org_row
        store.rows[marcus_row.id] = marcus_row
        client = _build_client(extraction_store=store, monkeypatch=monkeypatch)

        response = client.get("/v1/todo-extractions", headers=_identity_headers())
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["proposed_text"] == "ship the deck"
        assert body["items"][0]["state"] == "pending"


class TestReject:
    """``POST /v1/todo-extractions/{id}/reject``"""

    def test_transitions_pending_to_rejected(self, monkeypatch) -> None:
        store = InMemoryTodoExtractionStore()
        record = TodoExtractionRecord(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-a",
            conversation_id="conv-a",
            proposed_text="archive Q3",
        )
        store.rows[record.id] = record
        client = _build_client(extraction_store=store, monkeypatch=monkeypatch)

        response = client.post(
            f"/v1/todo-extractions/{record.id}/reject",
            headers=_identity_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "rejected"
        assert body["resolved_at"] is not None

    def test_other_tenant_gets_404(self, monkeypatch) -> None:
        store = InMemoryTodoExtractionStore()
        record = TodoExtractionRecord(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-a",
            conversation_id="conv-a",
            proposed_text="private",
        )
        store.rows[record.id] = record
        client = _build_client(extraction_store=store, monkeypatch=monkeypatch)

        response = client.post(
            f"/v1/todo-extractions/{record.id}/reject",
            headers=_identity_headers(org_id="globex"),
        )
        assert response.status_code == 404

    def test_other_owner_gets_404(self, monkeypatch) -> None:
        store = InMemoryTodoExtractionStore()
        record = TodoExtractionRecord(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-a",
            conversation_id="conv-a",
            proposed_text="private",
        )
        store.rows[record.id] = record
        client = _build_client(extraction_store=store, monkeypatch=monkeypatch)

        response = client.post(
            f"/v1/todo-extractions/{record.id}/reject",
            headers=_identity_headers(user_id="marcus"),
        )
        assert response.status_code == 404

    def test_already_resolved_gets_409(self, monkeypatch) -> None:
        store = InMemoryTodoExtractionStore()
        record = TodoExtractionRecord(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-a",
            conversation_id="conv-a",
            proposed_text="x",
            state=TodoExtractionState.ACCEPTED,
            resolved_at=datetime(2026, 5, 17, 8, 0, tzinfo=timezone.utc),
        )
        store.rows[record.id] = record
        client = _build_client(extraction_store=store, monkeypatch=monkeypatch)

        response = client.post(
            f"/v1/todo-extractions/{record.id}/reject",
            headers=_identity_headers(),
        )
        assert response.status_code == 409


class TestAccept:
    """``POST /v1/todo-extractions/{id}/accept``"""

    def test_forwards_to_backend_and_transitions(self, monkeypatch) -> None:
        store = InMemoryTodoExtractionStore()
        record = TodoExtractionRecord(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-a",
            conversation_id="conv-a",
            proposed_text="send the deck to Dana",
            suggested_due="2026-05-22",
        )
        store.rows[record.id] = record

        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["method"] = request.method
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                201,
                json={
                    "id": "todo-1",
                    "tenant_id": "acme",
                    "owner_user_id": "sarah",
                    "text": record.proposed_text,
                    "done": False,
                    "priority": "med",
                    "source": {
                        "kind": "chat",
                        "thread_id": record.conversation_id,
                    },
                    "labels": [],
                    "sort_index": 1.0,
                    "created_at": "2026-05-18T12:00:00+00:00",
                    "updated_at": "2026-05-18T12:00:00+00:00",
                },
            )

        transport = httpx.MockTransport(handler)
        client = _build_client(
            extraction_store=store,
            http_transport=transport,
            service_token="test-token",
            monkeypatch=monkeypatch,
        )
        # The route-level identity gate now requires this header because we
        # set ENTERPRISE_SERVICE_TOKEN above.
        headers = {
            **_identity_headers(),
            "x-enterprise-service-token": "test-token",
        }
        response = client.post(
            f"/v1/todo-extractions/{record.id}/accept",
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["extraction_id"] == record.id
        assert body["todo"]["id"] == "todo-1"
        assert body["todo"]["text"] == record.proposed_text

        # The backend was called with service-token + tenant headers.
        assert captured["method"] == "POST"
        assert captured["url"] == "http://backend.test/v1/todos"
        assert captured["headers"]["x-enterprise-service-token"] == "test-token"
        assert captured["headers"]["x-enterprise-org-id"] == "acme"
        assert captured["headers"]["x-enterprise-user-id"] == "sarah"
        assert captured["body"]["text"] == record.proposed_text
        assert captured["body"]["due"] == "2026-05-22"
        assert captured["body"]["source"]["kind"] == "chat"

        # The proposal is now accepted, not pending.
        stored = store.rows[record.id]
        assert stored.state == TodoExtractionState.ACCEPTED
        assert stored.resolved_at is not None

    def test_accept_idempotent_against_second_call_when_pending_already_gone(
        self, monkeypatch
    ) -> None:
        store = InMemoryTodoExtractionStore()
        record = TodoExtractionRecord(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-a",
            conversation_id="conv-a",
            proposed_text="x",
            state=TodoExtractionState.ACCEPTED,
            resolved_at=datetime(2026, 5, 17, 8, 0, tzinfo=timezone.utc),
        )
        store.rows[record.id] = record
        client = _build_client(extraction_store=store, monkeypatch=monkeypatch)
        # Second accept attempt against an already-resolved row is a 409.
        # The route handler never reaches the backend POST; the service
        # rejects on the pending-check before any network call.
        response = client.post(
            f"/v1/todo-extractions/{record.id}/accept",
            headers=_identity_headers(),
        )
        assert response.status_code == 409

    def test_accept_502_on_backend_5xx(self, monkeypatch) -> None:
        store = InMemoryTodoExtractionStore()
        record = TodoExtractionRecord(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-a",
            conversation_id="conv-a",
            proposed_text="x",
        )
        store.rows[record.id] = record

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"detail": "backend down"})

        transport = httpx.MockTransport(handler)
        client = _build_client(
            extraction_store=store,
            http_transport=transport,
            service_token="test-token",
            monkeypatch=monkeypatch,
        )
        headers = {
            **_identity_headers(),
            "x-enterprise-service-token": "test-token",
        }
        response = client.post(
            f"/v1/todo-extractions/{record.id}/accept",
            headers=headers,
        )
        assert response.status_code == 502
        # The proposal is still pending — failed accept doesn't transition.
        assert store.rows[record.id].state == TodoExtractionState.PENDING

    def test_accept_503_when_service_token_missing(self, monkeypatch) -> None:
        store = InMemoryTodoExtractionStore()
        record = TodoExtractionRecord(
            org_id="acme",
            owner_user_id="sarah",
            run_id="run-a",
            conversation_id="conv-a",
            proposed_text="x",
        )
        store.rows[record.id] = record

        # Don't set ENTERPRISE_SERVICE_TOKEN at all.
        monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)
        client = _build_client(extraction_store=store)
        response = client.post(
            f"/v1/todo-extractions/{record.id}/accept",
            headers=_identity_headers(),
        )
        assert response.status_code == 503
        assert store.rows[record.id].state == TodoExtractionState.PENDING
