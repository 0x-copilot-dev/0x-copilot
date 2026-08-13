"""``POST /v1/agent/runs/{run_id}/steer`` — the entry point, through the app.

Driven over the real router (router-level ``runtime:use`` scope, the real
coordinator, the real event producer) rather than by calling the coordinator
directly, because three of the four claims here are about things only the wired
route decides: which identity is trusted, what the transcript ends up holding,
and what a client replaying the run sees.

The fourth claim is the seal. ``run_steered`` is a *causal* event — no
:class:`LedgerAmendment` — so appending one after a run's terminal event would
raise :class:`LedgerSealViolation` and turn a user's mid-run message into a 500.
The coordinator's non-terminal check is what keeps that unreachable, and the
last test is that check's proof rather than a restatement of it.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ledger_seal import LedgerSealViolation
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import RuntimeErrorCode, StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.execution.run_steering import SteeringMessage
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    AgentRunStatus,
    RuntimeApiEventType,
    SteerRunRequest,
)


class SteerApiMixin:
    """One app, one conversation, one queued run — the state a steer needs."""

    class Values:
        ORG_ID = "org_456"
        USER_ID = "user_123"
        OTHER_USER_ID = "user_999"
        ASSISTANT_ID = "assistant_123"
        STEER_TEXT = "Actually, only look at EU launches."

    def create_client(self) -> tuple[TestClient, InMemoryRuntimeApiStore]:
        store = InMemoryRuntimeApiStore()
        settings = self.settings()
        app = RuntimeApiAppFactory.create_app(
            ports=RuntimeAdapterFactory.from_store(store), settings=settings
        )
        app.state.runtime_api_store = store
        return TestClient(app), store

    @staticmethod
    def settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )

    def coordinator(self, store: InMemoryRuntimeApiStore) -> RunCoordinator:
        settings = self.settings()
        return RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=RuntimeEventProducer(
                persistence=store, event_store=store, on_event_appended=None
            ),
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )

    def create_run(self, client: TestClient) -> dict[str, Any]:
        conversation = client.post(
            "/v1/agent/conversations",
            json={
                "org_id": self.Values.ORG_ID,
                "user_id": self.Values.USER_ID,
                "assistant_id": self.Values.ASSISTANT_ID,
            },
        )
        assert conversation.status_code == 200
        run = client.post(
            "/v1/agent/runs",
            json={
                "conversation_id": conversation.json()["conversation_id"],
                "org_id": self.Values.ORG_ID,
                "user_id": self.Values.USER_ID,
                "user_input": "Summarize launch risks.",
                "model": {"provider": "openai", "model_name": "gpt-5.4-mini"},
            },
        )
        assert run.status_code == 200
        return run.json()

    def steer(
        self,
        client: TestClient,
        run_id: str,
        *,
        text: str | None = None,
        requested_by_user_id: str | None = None,
    ):
        return client.post(
            f"/v1/agent/runs/{run_id}/steer",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
            json={
                "text": text if text is not None else self.Values.STEER_TEXT,
                "requested_by_user_id": (requested_by_user_id or self.Values.USER_ID),
            },
        )

    def replayed_events(self, client: TestClient, run_id: str) -> list[dict[str, Any]]:
        response = client.get(
            f"/v1/agent/runs/{run_id}/events",
            params={"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID},
        )
        assert response.status_code == 200
        return response.json()["events"]


class TestSteerRunEndpoint(SteerApiMixin):
    async def test_steer_records_the_message_and_queues_it_for_delivery(self) -> None:
        client, store = self.create_client()
        run = self.create_run(client)

        response = self.steer(client, run["run_id"])

        assert response.status_code == 200
        body = response.json()
        assert body["steer_id"].startswith("steer_")
        # A steer is context, not a state transition: the run keeps running.
        assert body["status"] == AgentRunStatus.QUEUED.value
        assert len(store.steer_commands) == 1
        command = store.steer_commands[0]
        assert command.run_id == run["run_id"]
        assert command.steer.text == self.Values.STEER_TEXT
        assert command.steer.steer_id == body["steer_id"]

    async def test_the_transcript_records_the_steer_and_it_survives_replay(
        self,
    ) -> None:
        """The record has to show that the user steered, and when.

        Replay — not the in-process event list — because that is the path a
        reconnecting client takes, and a payload that does not survive the
        projector is a note the user's own words fell out of.
        """

        client, _store = self.create_client()
        run = self.create_run(client)
        response = self.steer(client, run["run_id"])

        events = self.replayed_events(client, run["run_id"])

        assert [event["event_type"] for event in events] == [
            "run_queued",
            "run_steered",
        ]
        note = events[-1]
        assert note["sequence_no"] == response.json()["sequence_no"]
        assert note["payload"]["steer"]["text"] == self.Values.STEER_TEXT
        assert note["payload"]["steer"]["requested_by_user_id"] == (self.Values.USER_ID)
        # NOTE, not MESSAGE: routing a user interjection through the assistant's
        # own prose bucket would render the user's words as something the agent
        # said.
        assert note["activity_kind"] == "note"

    async def test_a_body_supplied_requester_cannot_put_words_in_another_run(
        self,
    ) -> None:
        """The session identity wins, exactly as it does for cancel."""

        client, store = self.create_client()
        run = self.create_run(client)

        response = self.steer(
            client,
            run["run_id"],
            requested_by_user_id=self.Values.OTHER_USER_ID,
        )

        assert response.status_code == 200
        assert store.steer_commands[0].requested_by_user_id == self.Values.USER_ID
        assert store.steer_commands[0].steer.requested_by_user_id == (
            self.Values.USER_ID
        )

    async def test_an_empty_steer_is_refused_before_it_reaches_the_ledger(
        self,
    ) -> None:
        client, store = self.create_client()
        run = self.create_run(client)

        response = self.steer(client, run["run_id"], text="   ")

        # The app maps a contract ValidationError to a safe 400 envelope; what
        # matters here is that the bound field rejected before any append.
        assert response.status_code == 400
        assert store.steer_commands == []
        assert [
            event["event_type"] for event in self.replayed_events(client, run["run_id"])
        ] == ["run_queued"]


class TestSteerAgainstAFinishedRun(SteerApiMixin):
    async def test_a_finished_run_refuses_with_a_typed_error(self) -> None:
        """Refuse, do not no-op.

        Cancel is idempotent on a terminal run because it already got what it
        asked for. A steer did not: answering 200 would tell the user their
        message landed in a turn that was already over.
        """

        client, store = self.create_client()
        run = self.create_run(client)
        await store.update_run_status(
            run_id=run["run_id"], status=AgentRunStatus.COMPLETED
        )

        with pytest.raises(RuntimeApiError) as caught:
            await self.coordinator(store).steer_run(
                org_id=self.Values.ORG_ID,
                user_id=self.Values.USER_ID,
                run_id=run["run_id"],
                request=SteerRunRequest(
                    text=self.Values.STEER_TEXT,
                    requested_by_user_id=self.Values.USER_ID,
                ),
            )

        assert caught.value.envelope.code is RuntimeErrorCode.VALIDATION_ERROR
        assert caught.value.http_status == 409
        assert caught.value.envelope.safe_message == (
            "This run is no longer in flight; send your message as a new turn."
        )
        assert caught.value.envelope.retryable is False
        assert store.steer_commands == []

    async def test_the_refusal_is_what_keeps_the_causal_prefix_seal_intact(
        self,
    ) -> None:
        """``run_steered`` is causal, so a post-terminal append would raise.

        Seeded on the route's OWN producer, because the seal is scoped to the
        producer instance that observed the terminal append — a seal established
        on a second producer would prove nothing about what the route does. So
        this asserts the hazard is genuinely armed for the request path, and
        then that the coordinator's non-terminal check answers 409 before ever
        reaching it. Delete that check and this test reports a 500.
        """

        client, store = self.create_client()
        run = self.create_run(client)
        producer = client.app.state.run_coordinator._event_producer
        record = await store.get_run(org_id=self.Values.ORG_ID, run_id=run["run_id"])
        assert record is not None
        await producer.append_api_event(
            run=record,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.RUN_COMPLETED,
            payload={"message": "Run completed."},
        )
        await store.update_run_status(
            run_id=run["run_id"], status=AgentRunStatus.COMPLETED
        )
        # The seal is armed: this producer now refuses any further causal event
        # for this run, and ``run_steered`` declares no amendment.
        with pytest.raises(LedgerSealViolation):
            await producer.append_api_event(
                run=record,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.RUN_STEERED,
                payload={
                    "steer": SteeringMessage(
                        text=self.Values.STEER_TEXT,
                        requested_by_user_id=self.Values.USER_ID,
                    ).model_dump(mode="json")
                },
            )

        response = self.steer(client, run["run_id"])

        assert response.status_code == 409
        assert [
            event["event_type"] for event in self.replayed_events(client, run["run_id"])
        ] == ["run_queued", "run_completed"]
