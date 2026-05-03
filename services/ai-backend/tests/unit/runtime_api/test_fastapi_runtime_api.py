from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.testclient import TestClient

from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalRequestRecord,
    RuntimeApiEventType,
)
from agent_runtime.api.events import RuntimeEventProducer
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from agent_runtime.api.service import RuntimeApiService
from agent_runtime.persistence.records import RuntimeWorkerResult
from agent_runtime.settings import RuntimeSettings
from runtime_api.sse.adapter import RuntimeSseAdapter
from runtime_worker.handlers.approval import RuntimeApprovalHandler


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
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_MAX_PARALLEL_TASKS": "4",
            }
        )
        service = RuntimeApiService(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
        )
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
            "idempotency_key": self.Values.IDEMPOTENCY_KEY,
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

    def run_payload(
        self, conversation_id: str, *, run_id: str | None = None
    ) -> dict[str, Any]:
        _ = run_id
        return {
            "conversation_id": conversation_id,
            "org_id": self.Values.ORG_ID,
            "user_id": self.Values.USER_ID,
            "user_input": self.Values.USER_INPUT,
            "content_format": "text",
            "idempotency_key": self.Values.IDEMPOTENCY_KEY,
            "model": {"provider": "openai", "model_name": "gpt-5.4-mini"},
            "request_context": {
                "roles": ["employee"],
                "permission_scopes": ["search:read", "docs:read"],
                "connector_scopes": {"google-drive": ["docs:read"]},
                "trace_metadata": {"source": "unit-test"},
                "feature_flags": ["streaming_observability"],
            },
            "request_options": {"authorization": self.Values.SECRET},
        }

    def create_conversation(self, client: TestClient) -> dict[str, Any]:
        response = client.post(
            "/v1/agent/conversations", json=self.conversation_payload()
        )
        assert response.status_code == 200
        return response.json()

    def create_run(self, client: TestClient, conversation_id: str) -> dict[str, Any]:
        response = client.post("/v1/agent/runs", json=self.run_payload(conversation_id))
        assert response.status_code == 200
        return response.json()

    async def collect_sse_stream(
        self,
        client: TestClient,
        run_id: str,
        *,
        after_sequence: int,
        follow: bool = False,
    ) -> str:
        chunks: list[str] = []
        async for chunk in RuntimeSseAdapter.stream(
            service=client.app.state.runtime_api_service,
            org_id=self.Values.ORG_ID,
            user_id=self.Values.USER_ID,
            run_id=run_id,
            after_sequence=after_sequence,
            follow=follow,
        ):
            chunks.append(chunk)
        return "".join(chunks)


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

    def test_list_conversations_returns_scoped_recent_conversations(self) -> None:
        client, _store = self.create_client()

        first = self.create_conversation(client)
        second_payload = {
            **self.conversation_payload(),
            "title": "Follow-up review",
            "idempotency_key": "idem_follow_up",
        }
        second_response = client.post("/v1/agent/conversations", json=second_payload)

        response = client.get(
            "/v1/agent/conversations",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )

        assert second_response.status_code == 200
        assert response.status_code == 200
        conversations = response.json()["conversations"]
        assert {item["conversation_id"] for item in conversations} == {
            first["conversation_id"],
            second_response.json()["conversation_id"],
        }
        assert all(item["metadata"]["token"] == "[redacted]" for item in conversations)

    def test_run_submission_is_idempotent_and_enqueues_worker_command(self) -> None:
        client, store = self.create_client()
        conversation = self.create_conversation(client)

        first = self.create_run(client, conversation["conversation_id"])
        second_response = client.post(
            "/v1/agent/runs",
            json=self.run_payload(
                conversation["conversation_id"], run_id="run_retry_123"
            ),
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

    def test_simple_run_request_builds_runtime_context_from_model_selection(
        self,
    ) -> None:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_MAX_PARALLEL_TASKS": "4",
            }
        )
        service = RuntimeApiService(
            persistence=store,
            event_store=store,
            queue=store,
            settings=settings,
        )
        app = RuntimeApiAppFactory.create_app(service)
        client = TestClient(app)
        conversation = client.post(
            "/v1/agent/conversations", json=self.conversation_payload()
        ).json()

        response = client.post(
            "/v1/agent/runs",
            json={
                "conversation_id": conversation["conversation_id"],
                "org_id": self.Values.ORG_ID,
                "user_id": self.Values.USER_ID,
                "user_input": self.Values.USER_INPUT,
                "model": {"provider": "openai", "model_name": "gpt-5.4-mini"},
                "request_context": {
                    "roles": ["employee"],
                    "permission_scopes": ["docs:read"],
                    "trace_metadata": {"source": "simple-request"},
                },
            },
        )

        assert response.status_code == 200
        run = store.runs[response.json()["run_id"]]
        assert run.runtime_context.org_id == self.Values.ORG_ID
        assert run.runtime_context.user_id == self.Values.USER_ID
        assert run.runtime_context.model_profile.provider == "openai"
        assert run.runtime_context.model_profile.model_name == "gpt-5.4-mini"
        assert run.runtime_context.max_parallel_tasks == 4

    def test_run_submission_round_trips_composer_metadata(self) -> None:
        client, store = self.create_client()
        conversation = self.create_conversation(client)
        payload = self.run_payload(conversation["conversation_id"])
        payload["idempotency_key"] = "idem_composer_metadata"
        payload["content"] = [{"type": "text", "text": self.Values.USER_INPUT}]
        payload["attachments"] = [
            {
                "id": "attachment_1",
                "type": "document",
                "name": "brief.txt",
                "content_type": "text/plain",
                "size": 5,
                "file_id": "file_brief",
                "content": [{"type": "text", "text": "brief"}],
            }
        ]
        payload["quote"] = {
            "text": "quoted selection",
            "message_id": "message_quote",
        }
        payload["source_message_id"] = "message_source"
        payload["branch_id"] = "branch_1"
        payload["branch"] = {"replace_from_message_id": "assistant_old"}

        run_response = client.post("/v1/agent/runs", json=payload)
        messages = client.get(
            f"/v1/agent/conversations/{conversation['conversation_id']}/messages",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )

        assert run_response.status_code == 200
        message_payload = messages.json()["messages"][0]
        assert message_payload["content"] == payload["content"]
        assert message_payload["attachments"] == payload["attachments"]
        assert message_payload["quote"] == payload["quote"]
        assert message_payload["source_message_id"] == "message_source"
        assert message_payload["branch_id"] == "branch_1"
        assert message_payload["metadata"]["branch"] == payload["branch"]
        run = store.runs[run_response.json()["run_id"]]
        assert (
            run.runtime_context.trace_metadata["attachments"] == payload["attachments"]
        )
        assert run.runtime_context.trace_metadata["branch_id"] == "branch_1"
        assert run.runtime_context.trace_metadata["branch"] == payload["branch"]

    def test_event_replay_and_sse_stream_use_ordered_event_envelope(self) -> None:
        client, _store = self.create_client()
        conversation = self.create_conversation(client)
        run = self.create_run(client, conversation["conversation_id"])

        replay = client.get(
            f"/v1/agent/runs/{run['run_id']}/events",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )
        stream_text = asyncio.run(
            self.collect_sse_stream(
                client,
                run["run_id"],
                after_sequence=1,
                follow=False,
            )
        )

        assert replay.status_code == 200
        assert replay.json()["events"][0]["sequence_no"] == 1
        assert replay.json()["events"][0]["event_type"] == "run_queued"
        assert "event: runtime_event" in stream_text
        assert '"event_type":"heartbeat"' in stream_text

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
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
        locked_claim = store.claim_next(
            worker_id="worker_2",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

        assert first_claim is not None
        assert first_claim.run_id == run["run_id"]
        assert first_claim.attempts == 1
        assert locked_claim is None

        store.mark_retry(
            result=RuntimeWorkerResult(
                command_id=first_claim.command_id,
                succeeded=False,
                retry_available_at=datetime.now(timezone.utc),
            )
        )
        retry_claim = store.claim_next(
            worker_id="worker_2",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

        assert retry_claim is not None
        assert retry_claim.locked_by == "worker_2"
        assert retry_claim.attempts == 2

        store.mark_dead_letter(
            result=RuntimeWorkerResult(
                command_id=retry_claim.command_id, succeeded=False
            )
        )
        assert (
            store.claim_next(
                worker_id="worker_3",
                lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            )
            is None
        )

    def test_runtime_api_acceptance_flow_covers_multi_turn_lifecycle(self) -> None:
        client, store = self.create_client()
        conversation = self.create_conversation(client)
        conversation_id = conversation["conversation_id"]
        producer = RuntimeEventProducer(persistence=store, event_store=store)

        first_run = self.create_run(client, conversation_id)
        first_claim = store.claim_next(
            worker_id="worker_1",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
        assert first_claim is not None
        assert first_claim.run_id == first_run["run_id"]

        running = store.update_run_status(
            run_id=first_run["run_id"],
            status=AgentRunStatus.RUNNING,
        )
        producer.append_api_event(
            run=running,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.RUN_STARTED,
            payload={"message": "Worker started.", "authorization": self.Values.SECRET},
        )
        completed = store.update_run_status(
            run_id=first_run["run_id"],
            status=AgentRunStatus.COMPLETED,
        )
        producer.append_api_event(
            run=completed,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.RUN_COMPLETED,
            payload={"message": "Worker completed."},
        )
        store.mark_complete(
            result=RuntimeWorkerResult(
                command_id=first_claim.command_id, succeeded=True
            )
        )

        replay = client.get(
            f"/v1/agent/runs/{first_run['run_id']}/events",
            params={
                "org_id": self.Values.ORG_ID,
                "user_id": self.Values.USER_ID,
                "after_sequence": 1,
                "follow": False,
            },
        )
        stream_text = asyncio.run(
            self.collect_sse_stream(
                client,
                first_run["run_id"],
                after_sequence=1,
                follow=False,
            )
        )

        assert replay.status_code == 200
        assert [event["event_type"] for event in replay.json()["events"]] == [
            "run_started",
            "run_completed",
        ]
        assert replay.json()["events"][0]["payload"]["authorization"] == "[redacted]"
        assert "run_started" in stream_text
        assert "heartbeat" not in stream_text

        follow_up_payload = self.run_payload(conversation_id, run_id="run_followup_123")
        follow_up_payload["user_input"] = (
            "Now focus only on launch risks without named owners."
        )
        follow_up_payload["idempotency_key"] = f"{self.Values.IDEMPOTENCY_KEY}_followup"
        follow_up_payload["request_context"]["trace_metadata"] = {
            "requested_run_id": "run_followup_123",
            "requested_trace_id": "trace_followup_123",
        }
        second_response = client.post("/v1/agent/runs", json=follow_up_payload)
        second_run = second_response.json()

        messages = client.get(
            f"/v1/agent/conversations/{conversation_id}/messages",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )
        second_claim = store.claim_next(
            worker_id="worker_2",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

        assert second_response.status_code == 200
        assert second_claim is not None
        assert second_claim.run_id == second_run["run_id"]
        assert [message["content_text"] for message in messages.json()["messages"]] == [
            self.Values.USER_INPUT,
            "Now focus only on launch risks without named owners.",
        ]

        store.seed_approval_request(
            ApprovalRequestRecord(
                approval_id=self.Values.APPROVAL_ID,
                run_id=second_run["run_id"],
                conversation_id=conversation_id,
                org_id=self.Values.ORG_ID,
                user_id=self.Values.USER_ID,
            )
        )
        approval = client.post(
            f"/v1/agent/approvals/{self.Values.APPROVAL_ID}/decision",
            params={"org_id": self.Values.ORG_ID},
            json={"decision": "approved", "decided_by_user_id": self.Values.USER_ID},
        )
        cancel = client.post(
            f"/v1/agent/runs/{second_run['run_id']}/cancel",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
            json={
                "requested_by_user_id": self.Values.USER_ID,
                "reason": "User wants to rewrite the later-turn request.",
            },
        )
        second_replay = client.get(
            f"/v1/agent/runs/{second_run['run_id']}/events",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )

        assert approval.status_code == 200
        assert approval.json()["status"] == "approved"
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelling"
        assert len(store.run_commands) == 2
        assert len(store.approval_commands) == 1
        assert len(store.cancel_commands) == 1
        assert [event["event_type"] for event in second_replay.json()["events"]] == [
            "run_queued",
            "approval_resolved",
            "run_cancelling",
        ]
        approval_handler = RuntimeApprovalHandler(persistence=store, event_store=store)
        asyncio.run(approval_handler.handle(store.approval_commands[0]))
        after_worker_replay = client.get(
            f"/v1/agent/runs/{second_run['run_id']}/events",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )
        assert [
            event["event_type"] for event in after_worker_replay.json()["events"]
        ] == [
            "run_queued",
            "approval_resolved",
            "run_cancelling",
        ]

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
