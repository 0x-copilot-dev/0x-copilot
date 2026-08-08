"""A settled turn's tool cards must survive the run that produced them.

The transcript folds cards from the ACTIVE run's event stream, and the client
drops that stream when it rebinds to the next run. So the moment a second turn
started, the first turn's tool cards had no source left and it rendered bare —
permanently. The sealed message cannot carry them either: ``TurnPartsProjection``
persists prose only, deliberately, so that card state has exactly one home.

This endpoint hands the renderer that home back for a whole conversation. It
returns FRAMES, not folded cards, so the fold stays in the one place it already
lives instead of being reimplemented server-side.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from runtime_api.schemas import RuntimeApiEventType, RuntimeEventDraft

from tests.unit.runtime_api.test_fastapi_runtime_api import (
    FastApiRuntimeApiTestMixin,
)


class TestConversationCardEvents(FastApiRuntimeApiTestMixin):
    def _new_run(self, client: TestClient, conversation_id: str, key: str) -> str:
        """A DISTINCT run per call.

        `create_run` posts a fixed idempotency key, so calling it twice returns
        the same run — which silently collapsed a two-turn fixture into one turn
        and made this endpoint look like it was mixing runs together when it was
        faithfully reporting a single one.
        """
        response = client.post(
            "/v1/agent/runs",
            json={**self.run_payload(conversation_id), "idempotency_key": key},
        )
        assert response.status_code == 200
        return str(response.json()["run_id"])

    def _scope(self) -> dict[str, str]:
        return {"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID}

    async def _seed_tool_call(
        self,
        store: Any,
        *,
        run_id: str,
        conversation_id: str,
        tool: str,
        seq_hint: int,
    ) -> None:
        """One started/result pair — the frames a tool card is folded from."""
        for event_type in (
            RuntimeApiEventType.TOOL_CALL_STARTED,
            RuntimeApiEventType.TOOL_RESULT,
        ):
            await store.append_event(
                RuntimeEventDraft(
                    org_id=self.Values.ORG_ID,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    event_type=event_type,
                    source="runtime",
                    trace_id=f"trace-{run_id}-{seq_hint}",
                    payload={"tool_name": tool, "call_id": f"{tool}-{seq_hint}"},
                )
            )

    async def test_returns_card_frames_for_every_run_tagged_by_run(self) -> None:
        """The whole point: turn 1's frames come back AFTER turn 2 has started.

        Each envelope carries its own ``run_id``, which is what lets the renderer
        put a card back on the turn that produced it — ``sequence_no`` alone
        cannot, because every run numbers its events from 0.
        """
        client, store = self.create_client()
        conversation = await self.create_conversation(client)
        conversation_id = conversation["conversation_id"]

        first_id = self._new_run(client, conversation_id, "turn-1")
        second_id = self._new_run(client, conversation_id, "turn-2")
        await self._seed_tool_call(
            store,
            run_id=first_id,
            conversation_id=conversation_id,
            tool="glob",
            seq_hint=1,
        )
        await self._seed_tool_call(
            store,
            run_id=second_id,
            conversation_id=conversation_id,
            tool="read_file",
            seq_hint=1,
        )

        response = client.get(
            f"/v1/agent/conversations/{conversation_id}/card-events",
            params=self._scope(),
        )

        assert response.status_code == 200
        body = response.json()
        by_run: dict[str, set[str]] = {}
        for event in body["events"]:
            by_run.setdefault(event["run_id"], set()).add(event["payload"]["tool_name"])
        assert by_run[first_id] == {"glob"}
        assert by_run[second_id] == {"read_file"}
        assert set(body["run_ids"]) == {first_id, second_id}

    async def test_carries_only_card_frames(self) -> None:
        """Filtered, not a second full replay.

        Returning the whole ledger for every run is the expensive option this
        design was chosen over; if this assertion goes, so does that reason.
        """
        client, store = self.create_client()
        conversation = await self.create_conversation(client)
        run = await self.create_run(client, conversation["conversation_id"])
        await self._seed_tool_call(
            store,
            run_id=run["run_id"],
            conversation_id=conversation["conversation_id"],
            tool="glob",
            seq_hint=1,
        )

        response = client.get(
            f"/v1/agent/conversations/{conversation['conversation_id']}/card-events",
            params=self._scope(),
        )

        kinds = {event["event_type"] for event in response.json()["events"]}
        assert kinds <= {
            RuntimeApiEventType.TOOL_CALL_STARTED.value,
            RuntimeApiEventType.TOOL_CALL_DELTA.value,
            RuntimeApiEventType.TOOL_CALL_COMPLETED.value,
            RuntimeApiEventType.TOOL_RESULT.value,
        }
        # `run_queued` is on every run's ledger and is NOT a card frame.
        assert RuntimeApiEventType.RUN_QUEUED.value not in kinds

    async def test_carries_citation_made_so_cross_turn_ordinals_resolve(self) -> None:
        """`[[N]]` is conversation-scoped; this endpoint is how the client learns it.

        The ordinal allocator numbers per CONVERSATION, so turn 2's prose cites
        turn 1's tool call. The client builds its link registry from the bound
        run's stream, which cannot contain that binding — so without this frame
        the chip renders a bare "?" over prose the server resolved correctly
        (observed live: `resolver.match ordinal=1 tool_call_id='call_x'` with no
        `unbound_ordinal` warning, and a "?" on screen).
        """
        client, store = self.create_client()
        conversation = await self.create_conversation(client)
        run = await self.create_run(client, conversation["conversation_id"])
        await store.append_event(
            RuntimeEventDraft(
                org_id=self.Values.ORG_ID,
                run_id=run["run_id"],
                conversation_id=conversation["conversation_id"],
                event_type=RuntimeApiEventType.CITATION_MADE,
                source="runtime",
                trace_id=f"trace-{run['run_id']}-cite",
                payload={
                    "link": {
                        "conversation_ordinal": 1,
                        "message_id": "msg-1",
                        "prose_offset": 0,
                        "prose_length": 5,
                        "source_tool_call_id": "call_x",
                    }
                },
            )
        )

        response = client.get(
            f"/v1/agent/conversations/{conversation['conversation_id']}/card-events",
            params=self._scope(),
        )

        events = response.json()["events"]
        cites = [
            event
            for event in events
            if event["event_type"] == RuntimeApiEventType.CITATION_MADE.value
        ]
        assert len(cites) == 1
        # The binding itself has to survive the trip, not just the frame type —
        # an empty `source_tool_call_id` is exactly what renders as "?".
        assert cites[0]["payload"]["link"]["source_tool_call_id"] == "call_x"
        assert cites[0]["payload"]["link"]["conversation_ordinal"] == 1

    async def test_a_conversation_with_no_tool_calls_is_empty_not_absent(self) -> None:
        """A turn that ran no tools is a real answer, not a truncation.

        `run_ids` still lists the run, so the client can tell "nothing to draw"
        apart from "your frames were dropped".
        """
        client, _store = self.create_client()
        conversation = await self.create_conversation(client)
        run = await self.create_run(client, conversation["conversation_id"])

        body = client.get(
            f"/v1/agent/conversations/{conversation['conversation_id']}/card-events",
            params=self._scope(),
        ).json()

        assert body["events"] == []
        assert body["run_ids"] == [run["run_id"]]
        assert body["has_more"] is False

    async def test_truncation_is_declared(self) -> None:
        """`has_more` is the difference between a short thread and a clipped one."""
        client, _store = self.create_client()
        conversation = await self.create_conversation(client)
        for index in range(3):
            self._new_run(client, conversation["conversation_id"], f"turn-{index}")

        body = client.get(
            f"/v1/agent/conversations/{conversation['conversation_id']}/card-events",
            params={**self._scope(), "run_limit": 2},
        ).json()

        assert len(body["run_ids"]) == 2
        assert body["has_more"] is True

    async def test_is_tenant_scoped(self) -> None:
        """Scope is checked on the conversation; a stranger gets 404, not a leak."""
        client, _store = self.create_client()
        conversation = await self.create_conversation(client)

        intruder = client.get(
            f"/v1/agent/conversations/{conversation['conversation_id']}/card-events",
            params={"org_id": self.Values.ORG_ID, "user_id": "intruder_user"},
        )

        assert intruder.status_code == 404
