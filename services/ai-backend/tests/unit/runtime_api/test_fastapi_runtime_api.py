from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalRequestRecord,
    CreateRunRequest,
    MessageRecord,
    MessageRole,
    RuntimeApiEventType,
)
from agent_runtime.api.events import RuntimeEventProducer
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
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
        ports = RuntimeAdapterFactory.from_store(store)
        app = RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
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

    async def create_conversation(self, client: TestClient) -> dict[str, Any]:
        response = client.post(
            "/v1/agent/conversations", json=self.conversation_payload()
        )
        assert response.status_code == 200
        return response.json()

    async def create_run(
        self, client: TestClient, conversation_id: str
    ) -> dict[str, Any]:
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
            service=client.app.state.conversation_query_service,
            org_id=self.Values.ORG_ID,
            user_id=self.Values.USER_ID,
            run_id=run_id,
            after_sequence=after_sequence,
            follow=follow,
        ):
            chunks.append(chunk)
        return "".join(chunks)


class TestMessageHistoryWindow(FastApiRuntimeApiTestMixin):
    """GET /messages returns the most-recent window (ASC) with a keyset cursor.

    AD-12 / NFR-7: past-the-limit newest turns were previously unreachable
    (ORDER BY created_at ASC LIMIT returned the OLDEST N). The window is now the
    tail; the response array stays ASC so transcript consumers are unaffected.
    """

    async def _seed_messages(self, store, conversation_id: str, *, count: int) -> None:
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for offset in range(count):
            await store.append_message(
                MessageRecord(
                    conversation_id=conversation_id,
                    org_id=self.Values.ORG_ID,
                    role=MessageRole.USER,
                    content_text=f"msg-{offset:02d}",
                    created_at=base + timedelta(seconds=offset),
                )
            )

    def _get_messages(self, client, conversation_id: str, **params):
        return client.get(
            f"/v1/agent/conversations/{conversation_id}/messages",
            params={
                "org_id": self.Values.ORG_ID,
                "user_id": self.Values.USER_ID,
                **params,
            },
        )

    async def test_returns_newest_window_ascending_with_cursor(self) -> None:
        client, store = self.create_client()
        conversation_id = (await self.create_conversation(client))["conversation_id"]
        await self._seed_messages(store, conversation_id, count=10)

        response = self._get_messages(client, conversation_id, limit=3)
        assert response.status_code == 200
        body = response.json()
        texts = [m["content_text"] for m in body["messages"]]
        # The newest three (the tail), ascending — NOT the oldest three.
        assert texts == ["msg-07", "msg-08", "msg-09"]
        assert body["has_more"] is True
        assert body["next_cursor"] is not None

    async def test_next_cursor_is_none_when_not_truncated(self) -> None:
        client, store = self.create_client()
        conversation_id = (await self.create_conversation(client))["conversation_id"]
        await self._seed_messages(store, conversation_id, count=3)

        body = self._get_messages(client, conversation_id, limit=50).json()
        assert [m["content_text"] for m in body["messages"]] == [
            "msg-00",
            "msg-01",
            "msg-02",
        ]
        assert body["has_more"] is False
        assert body["next_cursor"] is None

    async def test_cursor_round_trip_returns_strictly_older_page(self) -> None:
        client, store = self.create_client()
        conversation_id = (await self.create_conversation(client))["conversation_id"]
        await self._seed_messages(store, conversation_id, count=10)

        first = self._get_messages(client, conversation_id, limit=3).json()
        cursor = first["next_cursor"]
        assert cursor is not None

        older = self._get_messages(
            client, conversation_id, limit=3, before=cursor
        ).json()
        older_texts = [m["content_text"] for m in older["messages"]]
        # The page strictly older than msg-07, still ascending.
        assert older_texts == ["msg-04", "msg-05", "msg-06"]

    async def test_malformed_cursor_falls_back_to_most_recent_window(self) -> None:
        client, store = self.create_client()
        conversation_id = (await self.create_conversation(client))["conversation_id"]
        await self._seed_messages(store, conversation_id, count=10)

        response = self._get_messages(
            client, conversation_id, limit=3, before="!!!not-a-cursor!!!"
        )
        # A bad cursor degrades to the most-recent window — never a 500.
        assert response.status_code == 200
        texts = [m["content_text"] for m in response.json()["messages"]]
        assert texts == ["msg-07", "msg-08", "msg-09"]


class TestFastApiRuntimeApi(FastApiRuntimeApiTestMixin):
    async def test_conversation_endpoints_return_scoped_redacted_contracts(
        self,
    ) -> None:
        client, _store = self.create_client()

        created = await self.create_conversation(client)
        conversation_id = created["conversation_id"]

        # P11.5: conversation metadata flows through whole; redaction
        # happens only at the log emission boundary, not in API
        # response shapes.
        assert created["metadata"]["token"] == self.Values.SECRET
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

    async def test_list_conversations_returns_scoped_recent_conversations(self) -> None:
        client, _store = self.create_client()

        first = await self.create_conversation(client)
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
        # P11.5: see test_conversation_endpoints_return_scoped_redacted_contracts.
        assert all(
            item["metadata"]["token"] == self.Values.SECRET for item in conversations
        )

    async def test_pinned_bucket_is_complete_across_cursor_pages(self) -> None:
        """PRD-09 D3 / DoD #2 — the pinned bucket is server-scoped and cursored, so
        a pinned row buried under 150 unpinned rows is still reachable. The
        regression guard for the silently-incomplete Pinned bucket."""
        from datetime import datetime, timezone

        from runtime_api.schemas import CreateConversationRequest

        client, store = self.create_client()
        base = datetime(2026, 5, 1, tzinfo=timezone.utc)

        pinned_ids: set[str] = set()
        for i in range(3):
            conv = await store.create_conversation(
                CreateConversationRequest(
                    org_id=self.Values.ORG_ID,
                    user_id=self.Values.USER_ID,
                    assistant_id=self.Values.ASSISTANT_ID,
                    title=f"pin-{i}",
                )
            )
            await store.set_conversation_pinned(
                org_id=self.Values.ORG_ID,
                user_id=self.Values.USER_ID,
                conversation_id=conv.conversation_id,
                pinned=True,
                now=base + timedelta(minutes=i),
            )
            pinned_ids.add(conv.conversation_id)
        # 150 unpinned rows, all NEWER than the pinned ones (so a flat page-1
        # fetch would never surface the pinned rows).
        for i in range(150):
            conv = await store.create_conversation(
                CreateConversationRequest(
                    org_id=self.Values.ORG_ID,
                    user_id=self.Values.USER_ID,
                    assistant_id=self.Values.ASSISTANT_ID,
                    title=f"noise-{i}",
                )
            )
            await store.set_conversation_pinned(
                org_id=self.Values.ORG_ID,
                user_id=self.Values.USER_ID,
                conversation_id=conv.conversation_id,
                pinned=False,
                now=base + timedelta(hours=1, minutes=i),
            )

        scope = {"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID}
        seen: set[str] = set()
        cursor: str | None = None
        for _ in range(3):
            params = {**scope, "bucket": "pinned", "limit": 1}
            if cursor is not None:
                params["cursor"] = cursor
            body = client.get("/v1/agent/conversations", params=params).json()
            rows = body["conversations"]
            assert len(rows) == 1
            assert rows[0]["pinned"] is True
            seen.add(rows[0]["conversation_id"])
            cursor = body.get("next_cursor")
            if len(seen) == 1:
                # First page must advertise a next_cursor for the remaining two.
                assert cursor is not None
        # Following the cursor twice yielded the other two pinned rows.
        assert seen == pinned_ids

    async def test_legacy_list_omits_next_cursor_and_is_ordered(self) -> None:
        """PRD-09 D3 / DoD #3 — the legacy caller (no bucket/cursor) sees a payload
        whose top-level keys are exactly {conversations, has_more} and whose ids
        are in ``(updated_at DESC, id DESC)`` order — i.e. no change."""

        from runtime_api.schemas import CreateConversationRequest

        client, store = self.create_client()
        for i in range(4):
            await store.create_conversation(
                CreateConversationRequest(
                    org_id=self.Values.ORG_ID,
                    user_id=self.Values.USER_ID,
                    assistant_id=self.Values.ASSISTANT_ID,
                    title=f"c-{i}",
                    # explicit updated_at handled by store default; created in order
                )
            )
        scope = {"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID}
        body = client.get("/v1/agent/conversations", params=scope).json()
        assert set(body.keys()) == {"conversations", "has_more"}
        ids = [c["conversation_id"] for c in body["conversations"]]
        records = await store.list_conversations(
            org_id=self.Values.ORG_ID, user_id=self.Values.USER_ID, limit=200
        )
        expected = [r.conversation_id for r in records]
        assert ids == expected

    async def test_list_projects_pinned_preview_and_model(self) -> None:
        """PRD-H.4 — the list contract carries pinned/preview/model."""
        client, _store = self.create_client()
        conversation = await self.create_conversation(client)
        conversation_id = conversation["conversation_id"]

        # A never-run conversation: pinned defaults False, preview/model null.
        listed = client.get(
            "/v1/agent/conversations",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        ).json()["conversations"]
        row = next(r for r in listed if r["conversation_id"] == conversation_id)
        assert row["pinned"] is False
        assert row["preview"] is None
        assert row["model"] is None

        # Give it a run so preview + model project.
        await self.create_run(client, conversation_id)
        listed = client.get(
            "/v1/agent/conversations",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        ).json()["conversations"]
        row = next(r for r in listed if r["conversation_id"] == conversation_id)
        assert row["preview"] == self.Values.USER_INPUT
        assert row["model"] == "gpt-5.4-mini"

    async def test_head_run_id_any_status_survives_completion_on_list_and_get(
        self,
    ) -> None:
        """desktop-run-identity §D2 — ``latest_run_id_any_status`` resolves the
        conversation's head run on BOTH the list and the get endpoint, and —
        unlike the active-only ``latest_run_id`` — stays populated once the run
        reaches a terminal state, so a client reopening a finished conversation
        can still bind its last run (kills the "NO ACTIVE RUN" reopen bug)."""
        client, store = self.create_client()
        conversation = await self.create_conversation(client)
        conversation_id = conversation["conversation_id"]
        scope = {"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID}

        def list_row() -> dict[str, Any]:
            listed = client.get("/v1/agent/conversations", params=scope).json()[
                "conversations"
            ]
            return next(r for r in listed if r["conversation_id"] == conversation_id)

        def get_row() -> dict[str, Any]:
            return client.get(
                f"/v1/agent/conversations/{conversation_id}", params=scope
            ).json()

        # Never-run conversation: the head-run id is null on both endpoints.
        assert list_row()["latest_run_id_any_status"] is None
        assert get_row()["latest_run_id_any_status"] is None

        # After a (non-terminal) run: both the active id and the head id resolve
        # to it, identically on the list and get shapes.
        run = await self.create_run(client, conversation_id)
        run_id = run["run_id"]
        for row in (list_row(), get_row()):
            assert row["latest_run_id_any_status"] == run_id
            assert row["latest_run_id"] == run_id

        # Once the run COMPLETES: the active-only projection drops to null, but
        # the head id PERSISTS on both endpoints — the property reopen depends on.
        await store.update_run_status(run_id=run_id, status=AgentRunStatus.COMPLETED)
        for row in (list_row(), get_row()):
            assert row["latest_run_id"] is None
            assert row["latest_run_id_any_status"] == run_id

    async def test_list_conversation_runs_returns_newest_first(self) -> None:
        """desktop-run-identity §D2 (Phase 6) — GET /conversations/{id}/runs lists
        the conversation's runs newest-first for the multi-run selector, tenant-scoped."""
        client, _store = self.create_client()
        conversation = await self.create_conversation(client)
        conversation_id = conversation["conversation_id"]
        scope = {"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID}

        # No runs yet → empty list.
        empty = client.get(
            f"/v1/agent/conversations/{conversation_id}/runs", params=scope
        )
        assert empty.status_code == 200
        assert empty.json()["runs"] == []

        # Three runs (distinct run idempotency keys so they don't dedupe).
        run_ids: list[str] = []
        for i in range(3):
            resp = client.post(
                "/v1/agent/runs",
                json={
                    **self.run_payload(conversation_id),
                    "idempotency_key": f"run-{i}",
                },
            )
            assert resp.status_code == 200
            run_ids.append(resp.json()["run_id"])

        listed = client.get(
            f"/v1/agent/conversations/{conversation_id}/runs", params=scope
        )
        assert listed.status_code == 200
        runs = listed.json()["runs"]
        assert len(runs) == 3
        assert {r["run_id"] for r in runs} == set(run_ids)
        # Newest-first: created_at is non-increasing, each summary carries a model.
        times = [r["created_at"] for r in runs]
        assert times == sorted(times, reverse=True)
        assert all(r["model_name"] for r in runs)
        assert all(r["status"] == "queued" for r in runs)

        # Tenant scope: another user in the org can't read this conversation's runs.
        intruder = client.get(
            f"/v1/agent/conversations/{conversation_id}/runs",
            params={"org_id": self.Values.ORG_ID, "user_id": "intruder_user"},
        )
        assert intruder.status_code == 404

    async def test_pin_route_toggles_persists_and_is_tenant_scoped(self) -> None:
        """PRD-H.4 — POST /pin toggles + persists; other users get 404."""
        client, _store = self.create_client()
        conversation = await self.create_conversation(client)
        conversation_id = conversation["conversation_id"]
        scope = {"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID}

        # A bare POST pins.
        pinned = client.post(
            f"/v1/agent/conversations/{conversation_id}/pin",
            params=scope,
            json={},
        )
        assert pinned.status_code == 200
        assert pinned.json()["pinned"] is True

        # Persisted across a fresh list read.
        listed = client.get("/v1/agent/conversations", params=scope).json()[
            "conversations"
        ]
        row = next(r for r in listed if r["conversation_id"] == conversation_id)
        assert row["pinned"] is True

        # Explicit unpin.
        unpinned = client.post(
            f"/v1/agent/conversations/{conversation_id}/pin",
            params=scope,
            json={"pinned": False},
        )
        assert unpinned.status_code == 200
        assert unpinned.json()["pinned"] is False

        # Another user in the same org cannot pin this chat.
        intruder = client.post(
            f"/v1/agent/conversations/{conversation_id}/pin",
            params={"org_id": self.Values.ORG_ID, "user_id": "intruder_user"},
            json={},
        )
        assert intruder.status_code == 404

    async def test_new_chat_run_ensures_conversation_idempotently(self) -> None:
        """desktop-run-identity §D3 — a run with NO conversation_id but a
        conversation_idempotency_key server-side get-or-creates the conversation
        and binds the run to it in ONE call; the same key never duplicates."""
        client, _store = self.create_client()

        def new_chat_body(*, run_idem: str) -> dict[str, Any]:
            return {
                "org_id": self.Values.ORG_ID,
                "user_id": self.Values.USER_ID,
                "user_input": self.Values.USER_INPUT,
                "content_format": "text",
                "idempotency_key": run_idem,
                "conversation_idempotency_key": "new-chat-intent-1",
                "conversation_title": "First chat",
                "model": {"provider": "openai", "model_name": "gpt-5.4-mini"},
                "request_context": {
                    "roles": ["employee"],
                    "permission_scopes": ["search:read"],
                },
            }

        # First send: creates the conversation + the run, returns the new id.
        first = client.post("/v1/agent/runs", json=new_chat_body(run_idem="r1"))
        assert first.status_code == 200
        conversation_id = first.json()["conversation_id"]
        assert conversation_id  # a real, server-minted id

        # The conversation really exists and carried the supplied title.
        got = client.get(
            f"/v1/agent/conversations/{conversation_id}",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )
        assert got.status_code == 200
        assert got.json()["title"] == "First chat"

        # Second send with the SAME conversation_idempotency_key (still no
        # conversation_id, distinct run key) reuses the SAME conversation — never
        # a duplicate (kills the "two Desktop session" race, desktop-run-identity §D3).
        second = client.post("/v1/agent/runs", json=new_chat_body(run_idem="r2"))
        assert second.status_code == 200
        assert second.json()["conversation_id"] == conversation_id

        listed = client.get(
            "/v1/agent/conversations",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        ).json()["conversations"]
        assert [c["conversation_id"] for c in listed].count(conversation_id) == 1
        assert len(listed) == 1

    def test_new_chat_run_requires_conversation_idempotency_key(self) -> None:
        """desktop-run-identity §D3 — a new-chat run (no conversation_id) is valid
        WITH a conversation_idempotency_key and a validation error WITHOUT one: the
        de-dup key is mandatory so concurrent first sends can't create two chats.
        Unit-level on the contract (the HTTP route is RBAC-gated separately)."""
        # New-chat shape is valid when the de-dup key is present.
        new_chat = CreateRunRequest(
            org_id="o",
            user_id="u",
            user_input="hi",
            conversation_idempotency_key="new-chat-1",
        )
        assert new_chat.conversation_id is None

        # Omitting BOTH conversation_id and the key is rejected at the contract.
        try:
            CreateRunRequest(org_id="o", user_id="u", user_input="hi")
        except ValidationError as exc:
            assert "conversation_idempotency_key" in str(exc)
        else:
            raise AssertionError("expected a validation error for the missing key")

    async def test_run_submission_is_idempotent_and_enqueues_worker_command(
        self,
    ) -> None:
        client, store = self.create_client()
        conversation = await self.create_conversation(client)

        first = await self.create_run(client, conversation["conversation_id"])
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
        ports = RuntimeAdapterFactory.from_store(store)
        app = RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
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

    async def test_run_submission_round_trips_composer_metadata(self) -> None:
        client, store = self.create_client()
        conversation = await self.create_conversation(client)
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

    async def test_event_replay_and_sse_stream_use_ordered_event_envelope(self) -> None:
        client, _store = self.create_client()
        conversation = await self.create_conversation(client)
        run = await self.create_run(client, conversation["conversation_id"])

        replay = client.get(
            f"/v1/agent/runs/{run['run_id']}/events",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )
        stream_text = await self.collect_sse_stream(
            client,
            run["run_id"],
            after_sequence=1,
            follow=False,
        )

        assert replay.status_code == 200
        assert replay.json()["events"][0]["sequence_no"] == 1
        assert replay.json()["events"][0]["event_type"] == "run_queued"
        assert "event: runtime_event" in stream_text
        assert '"event_type":"heartbeat"' in stream_text

    async def test_cancel_run_persists_cancelling_state_event_and_command(self) -> None:
        client, store = self.create_client()
        conversation = await self.create_conversation(client)
        run = await self.create_run(client, conversation["conversation_id"])

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

    async def test_approval_decision_persists_and_enqueues_resume_command(self) -> None:
        client, store = self.create_client()
        conversation = await self.create_conversation(client)
        run = await self.create_run(client, conversation["conversation_id"])
        await store.seed_approval_request(
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

    async def test_worker_queue_claim_retry_and_dead_letter_semantics(self) -> None:
        client, store = self.create_client()
        conversation = await self.create_conversation(client)
        run = await self.create_run(client, conversation["conversation_id"])

        first_claim = await store.claim_next(
            worker_id="worker_1",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
        locked_claim = await store.claim_next(
            worker_id="worker_2",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

        assert first_claim is not None
        assert first_claim.run_id == run["run_id"]
        assert first_claim.attempts == 1
        assert locked_claim is None

        await store.mark_retry(
            result=RuntimeWorkerResult(
                command_id=first_claim.command_id,
                succeeded=False,
                retry_available_at=datetime.now(timezone.utc),
            )
        )
        retry_claim = await store.claim_next(
            worker_id="worker_2",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

        assert retry_claim is not None
        assert retry_claim.locked_by == "worker_2"
        assert retry_claim.attempts == 2

        await store.mark_dead_letter(
            result=RuntimeWorkerResult(
                command_id=retry_claim.command_id, succeeded=False
            )
        )
        assert (
            await store.claim_next(
                worker_id="worker_3",
                lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
            )
            is None
        )

    async def test_runtime_api_acceptance_flow_covers_multi_turn_lifecycle(
        self,
    ) -> None:
        client, store = self.create_client()
        conversation = await self.create_conversation(client)
        conversation_id = conversation["conversation_id"]
        producer = RuntimeEventProducer(persistence=store, event_store=store)

        first_run = await self.create_run(client, conversation_id)
        first_claim = await store.claim_next(
            worker_id="worker_1",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
        assert first_claim is not None
        assert first_claim.run_id == first_run["run_id"]

        running = await store.update_run_status(
            run_id=first_run["run_id"],
            status=AgentRunStatus.RUNNING,
        )
        await producer.append_api_event(
            run=running,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.RUN_STARTED,
            payload={
                "message": "Worker started.",
                "authorization": self.Values.SECRET,
            },
        )
        completed = await store.update_run_status(
            run_id=first_run["run_id"],
            status=AgentRunStatus.COMPLETED,
        )
        await producer.append_api_event(
            run=completed,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.RUN_COMPLETED,
            payload={"message": "Worker completed."},
        )
        await store.mark_complete(
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
        stream_text = await self.collect_sse_stream(
            client,
            first_run["run_id"],
            after_sequence=1,
            follow=False,
        )

        assert replay.status_code == 200
        assert [event["event_type"] for event in replay.json()["events"]] == [
            "run_started",
            "run_completed",
        ]
        # P11.5: event payloads pass through whole. Whatever the worker
        # appended is what the replay returns. Logs filter via
        # ``DENY_KEYS``; SSE and replay do not.
        assert "authorization" in replay.json()["events"][0]["payload"]
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
        second_claim = await store.claim_next(
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

        await store.seed_approval_request(
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
        await approval_handler.handle(store.approval_commands[0])
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


class TestRunHistoryRoute(FastApiRuntimeApiTestMixin):
    """PRD-05 — GET /v1/agent/runs: the org-scoped run-history collection read."""

    def _scope(self) -> dict[str, Any]:
        return {"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID}

    async def _seed(
        self, client, store, specs: list[tuple[datetime, AgentRunStatus]]
    ) -> tuple[str, list[str]]:
        """Create one run per spec, then patch its created_at + status directly.

        Runs are created through the real POST /runs path (so idempotency +
        persistence match production), then their created_at/status are rewritten
        on the in-memory record to place them on specific calendar days / states —
        the wire has no way to backdate a run, but the store is the test's to shape.
        """
        conversation = await self.create_conversation(client)
        conversation_id = conversation["conversation_id"]
        run_ids: list[str] = []
        for index, (created_at, status) in enumerate(specs):
            resp = client.post(
                "/v1/agent/runs",
                json={
                    **self.run_payload(conversation_id),
                    "idempotency_key": f"rh-{index}",
                },
            )
            assert resp.status_code == 200
            run_id = resp.json()["run_id"]
            run_ids.append(run_id)
            record = store.runs[run_id]
            store.runs[run_id] = record.model_copy(
                update={"created_at": created_at, "status": status}
            )
        return conversation_id, run_ids

    async def test_run_history_returns_paginated_shape(self) -> None:
        client, store = self.create_client()
        base = datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)
        await self._seed(
            client,
            store,
            [
                (base, AgentRunStatus.COMPLETED),
                (base + timedelta(minutes=5), AgentRunStatus.RUNNING),
            ],
        )
        resp = client.get("/v1/agent/runs", params=self._scope())
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"runs", "next_cursor", "has_more"}
        assert len(body["runs"]) == 2
        assert body["has_more"] is False
        assert body["next_cursor"] is None
        # Newest-first; the terminal (completed) run is reachable — the bug fix.
        assert {r["status"] for r in body["runs"]} == {"completed", "running"}

    async def test_run_history_limit_over_cap_is_clamped_not_rejected(self) -> None:
        client, store = self.create_client()
        base = datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)
        await self._seed(
            client,
            store,
            [(base + timedelta(minutes=i), AgentRunStatus.COMPLETED) for i in range(3)],
        )
        # limit=500 is clamped to 200 by the service, not 422'd by the route.
        resp = client.get("/v1/agent/runs", params={**self._scope(), "limit": 500})
        assert resp.status_code == 200
        assert len(resp.json()["runs"]) == 3

    async def test_run_history_malformed_cursor_returns_newest_window(self) -> None:
        client, store = self.create_client()
        base = datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)
        await self._seed(
            client,
            store,
            [(base + timedelta(minutes=i), AgentRunStatus.COMPLETED) for i in range(3)],
        )
        clean = client.get("/v1/agent/runs", params=self._scope()).json()
        garbled = client.get(
            "/v1/agent/runs",
            params={**self._scope(), "cursor": "!!not-a-valid-cursor!!"},
        )
        assert garbled.status_code == 200
        assert [r["run_id"] for r in garbled.json()["runs"]] == [
            r["run_id"] for r in clean["runs"]
        ]

    async def test_run_history_requires_scope(self) -> None:
        client, _store = self.create_client()
        # Neither service-token headers nor org_id+user_id supplied.
        resp = client.get("/v1/agent/runs")
        assert resp.status_code == 400

    async def test_run_history_cursor_round_trips_to_strictly_older_page(self) -> None:
        client, store = self.create_client()
        base = datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)
        await self._seed(
            client,
            store,
            [(base + timedelta(minutes=i), AgentRunStatus.COMPLETED) for i in range(5)],
        )
        page1 = client.get(
            "/v1/agent/runs", params={**self._scope(), "limit": 2}
        ).json()
        assert page1["has_more"] is True
        assert page1["next_cursor"] is not None
        page2 = client.get(
            "/v1/agent/runs",
            params={**self._scope(), "limit": 2, "cursor": page1["next_cursor"]},
        ).json()
        ids1 = {r["run_id"] for r in page1["runs"]}
        ids2 = {r["run_id"] for r in page2["runs"]}
        assert ids1.isdisjoint(ids2)

    async def test_run_history_matches_design_census(self) -> None:
        """Design value pinned numerically (PRD-05 DoD 17): the design fixture
        (tools/design-parity/design-kit/app-v3/copilot-data.jsx:600-645) is 8 runs
        across 3 calendar days, 1 non-terminal + 7 terminal. The endpoint returns
        all 8, spanning 3 distinct dates, newest-first."""
        client, store = self.create_client()
        today = datetime(2026, 7, 16, 11, 44, tzinfo=timezone.utc)
        yesterday = datetime(2026, 7, 15, 9, 2, tzinfo=timezone.utc)
        mon_jul_14 = datetime(2026, 7, 14, 18, 30, tzinfo=timezone.utc)
        # 8 runs across 3 days: 1 non-terminal (running) + 7 terminal.
        specs = [
            (today + timedelta(minutes=0), AgentRunStatus.RUNNING),
            (today + timedelta(minutes=1), AgentRunStatus.COMPLETED),
            (today + timedelta(minutes=2), AgentRunStatus.COMPLETED),
            (yesterday + timedelta(minutes=0), AgentRunStatus.COMPLETED),
            (yesterday + timedelta(minutes=1), AgentRunStatus.CANCELLED),
            (yesterday + timedelta(minutes=2), AgentRunStatus.COMPLETED),
            (mon_jul_14 + timedelta(minutes=0), AgentRunStatus.COMPLETED),
            (mon_jul_14 + timedelta(minutes=1), AgentRunStatus.FAILED),
        ]
        await self._seed(client, store, specs)
        body = client.get(
            "/v1/agent/runs", params={**self._scope(), "limit": 50}
        ).json()
        runs = body["runs"]
        assert len(runs) == 8
        dates = {r["created_at"][:10] for r in runs}
        assert len(dates) == 3
        created = [r["created_at"] for r in runs]
        assert created == sorted(created, reverse=True)
        terminal = {"cancelled", "completed", "failed", "timed_out"}
        assert sum(1 for r in runs if r["status"] in terminal) == 7
        assert sum(1 for r in runs if r["status"] == "running") == 1
