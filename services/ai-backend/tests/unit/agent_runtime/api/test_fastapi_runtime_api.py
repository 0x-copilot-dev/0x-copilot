from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from agent_runtime.api.app import RuntimeApiAppFactory
from agent_runtime.api.contracts import ApprovalRequestRecord
from agent_runtime.api.in_memory import InMemoryRuntimeApiStore
from agent_runtime.api.service import RuntimeApiService
from agent_runtime.persistence.contracts import RuntimeWorkerResult


class FastApiRuntimeApiTestMixin:
    class Values:
        ORG_ID = "org_456"
        USER_ID = "user_123"
        ASSISTANT_ID = "assistant_123"
        TRACE_ID = "trace_123"
        REQUEST_ID = "request_123"
        RUN_ID = "run_123"
        IDEMPOTENCY_KEY = "idem_123"
        USER_INPUT = "Find launch risks."
        APPROVAL_ID = "approval_123"
        SECRET = "secret-token"

    def create_client(self) -> tuple[TestClient, InMemoryRuntimeApiStore]:
        store = InMemoryRuntimeApiStore()
        service = RuntimeApiService(persistence=store, event_store=store, queue=store)
        app = RuntimeApiAppFactory.create_app(service)
        app.state.runtime_api_store = store
        return TestClient(app), store

    def conversation_payload(self) -> dict[str, Any]:
        return {
            "org_id": self.Values.ORG_ID,
            "user_id": self.Values.USER_ID,
            "assistant_id": self.Values.ASSISTANT_ID,
            "title": "Launch review",
            "metadata": {"token": self.Values.SECRET, "source": "unit-test"},
            "idempotency_key": "conversation_idem_123",
        }

    def runtime_context_payload(self, *, run_id: str | None = None) -> dict[str, Any]:
        return {
            "user_id": self.Values.USER_ID,
            "org_id": self.Values.ORG_ID,
            "roles": ["employee"],
            "permission_scopes": ["search:read", "docs:read"],
            "connector_scopes": {"google-drive": ["docs:read"]},
            "model_profile": {
                "provider": "fake",
                "model_name": "fake-enterprise-model",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
            "request_id": self.Values.REQUEST_ID,
            "run_id": run_id or self.Values.RUN_ID,
            "trace_id": self.Values.TRACE_ID,
            "feature_flags": ["streaming_observability"],
        }

    def run_payload(self, conversation_id: str, *, run_id: str | None = None) -> dict[str, Any]:
        return {
            "conversation_id": conversation_id,
            "user_input": self.Values.USER_INPUT,
            "content_format": "text",
            "idempotency_key": self.Values.IDEMPOTENCY_KEY,
            "runtime_context": self.runtime_context_payload(run_id=run_id),
            "request_options": {"authorization": self.Values.SECRET},
        }

    def create_conversation(self, client: TestClient) -> dict[str, Any]:
        response = client.post("/v1/agent/conversations", json=self.conversation_payload())
        assert response.status_code == 200
        return response.json()

    def create_run(self, client: TestClient, conversation_id: str) -> dict[str, Any]:
        response = client.post("/v1/agent/runs", json=self.run_payload(conversation_id))
        assert response.status_code == 200
        return response.json()


class TestFastApiRuntimeApi(FastApiRuntimeApiTestMixin):
    def test_conversation_endpoints_return_scoped_redacted_contracts(self) -> None:
        client, _store = self.create_client()

        created = self.create_conversation(client)
        conversation_id = created["conversation_id"]

        assert created["metadata"]["token"] == "[redacted]"
        response = client.get(
            f"/v1/agent/conversations/{conversation_id}",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )
        messages = client.get(
            f"/v1/agent/conversations/{conversation_id}/messages",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )

        assert response.status_code == 200
        assert response.json()["conversation_id"] == conversation_id
        assert messages.status_code == 200
        assert messages.json()["messages"] == []

    def test_run_submission_is_idempotent_and_enqueues_worker_command(self) -> None:
        client, store = self.create_client()
        conversation = self.create_conversation(client)

        first = self.create_run(client, conversation["conversation_id"])
        second_response = client.post(
            "/v1/agent/runs",
            json=self.run_payload(conversation["conversation_id"], run_id="run_retry_123"),
        )
        messages = client.get(
            f"/v1/agent/conversations/{conversation['conversation_id']}/messages",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )

        assert second_response.status_code == 200
        assert second_response.json()["run_id"] == first["run_id"]
        assert len(store.run_commands) == 1
        assert len(store.events_by_run[first["run_id"]]) == 1
        assert messages.json()["messages"][0]["content_text"] == self.Values.USER_INPUT

    def test_event_replay_and_sse_stream_use_ordered_event_envelope(self) -> None:
        client, _store = self.create_client()
        conversation = self.create_conversation(client)
        run = self.create_run(client, conversation["conversation_id"])

        replay = client.get(
            f"/v1/agent/runs/{run['run_id']}/events",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )
        stream = client.get(
            f"/v1/agent/runs/{run['run_id']}/stream",
            params={
                "org_id": self.Values.ORG_ID,
                "user_id": self.Values.USER_ID,
                "after_sequence": 1,
            },
        )

        assert replay.status_code == 200
        assert replay.json()["events"][0]["sequence_no"] == 1
        assert replay.json()["events"][0]["event_type"] == "run_queued"
        assert stream.status_code == 200
        assert "event: runtime_event" in stream.text
        assert '"event_type":"heartbeat"' in stream.text

    def test_cancel_run_persists_cancelling_state_event_and_command(self) -> None:
        client, store = self.create_client()
        conversation = self.create_conversation(client)
        run = self.create_run(client, conversation["conversation_id"])

        response = client.post(
            f"/v1/agent/runs/{run['run_id']}/cancel",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
            json={
                "requested_by_user_id": self.Values.USER_ID,
                "reason": "User closed the laptop.",
            },
        )
        replay = client.get(
            f"/v1/agent/runs/{run['run_id']}/events",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "cancelling"
        assert len(store.cancel_commands) == 1
        assert [event["event_type"] for event in replay.json()["events"]] == [
            "run_queued",
            "run_cancelling",
        ]

    def test_approval_decision_persists_and_enqueues_resume_command(self) -> None:
        client, store = self.create_client()
        conversation = self.create_conversation(client)
        run = self.create_run(client, conversation["conversation_id"])
        store.seed_approval_request(
            ApprovalRequestRecord(
                approval_id=self.Values.APPROVAL_ID,
                run_id=run["run_id"],
                conversation_id=conversation["conversation_id"],
                org_id=self.Values.ORG_ID,
                user_id=self.Values.USER_ID,
            )
        )

        response = client.post(
            f"/v1/agent/approvals/{self.Values.APPROVAL_ID}/decision",
            params={"org_id": self.Values.ORG_ID},
            json={"decision": "approved", "decided_by_user_id": self.Values.USER_ID},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "approved"
        assert len(store.approval_commands) == 1
        assert store.approval_requests[self.Values.APPROVAL_ID].status == "approved"

    def test_worker_queue_claim_retry_and_dead_letter_semantics(self) -> None:
        client, store = self.create_client()
        conversation = self.create_conversation(client)
        run = self.create_run(client, conversation["conversation_id"])

        first_claim = store.claim_next(
            worker_id="worker_1",
            lock_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )
        locked_claim = store.claim_next(
            worker_id="worker_2",
            lock_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )

        assert first_claim is not None
        assert first_claim.run_id == run["run_id"]
        assert first_claim.attempts == 1
        assert locked_claim is None

        store.mark_retry(
            result=RuntimeWorkerResult(
                command_id=first_claim.command_id,
                succeeded=False,
                retry_available_at=datetime.now(UTC),
            )
        )
        retry_claim = store.claim_next(
            worker_id="worker_2",
            lock_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )

        assert retry_claim is not None
        assert retry_claim.locked_by == "worker_2"
        assert retry_claim.attempts == 2

        store.mark_dead_letter(
            result=RuntimeWorkerResult(command_id=retry_claim.command_id, succeeded=False)
        )
        assert (
            store.claim_next(
                worker_id="worker_3",
                lock_expires_at=datetime.now(UTC) + timedelta(seconds=30),
            )
            is None
        )

    def test_safe_error_mapping_for_missing_run_and_invalid_payload(self) -> None:
        client, _store = self.create_client()

        missing = client.get(
            "/v1/agent/runs/missing_run",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )
        invalid = client.post("/v1/agent/conversations", json={"org_id": ""})

        assert missing.status_code == 404
        assert missing.json()["safe_message"] == "Run was not found for this scope."
        assert invalid.status_code == 400
        assert invalid.json()["code"] == "validation_error"
