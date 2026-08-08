"""A conversation names itself from the message that opened it.

Nothing generated a title: one was set only by an explicit PATCH, so the
ordinary path — open the cockpit, type, send — left it unset and the Run header
fell through to the literal string "Untitled run". Reported from the live
desktop app on a five-exchange thread.

Naming happens at run creation because that is the first moment a conversation
and the text that opened it exist together. The hosts cannot cover this: they
can only name a conversation they create FROM a prompt, and every other flow
creates the conversation first, with nothing to name it after yet.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.unit.runtime_api.test_fastapi_runtime_api import (
    FastApiRuntimeApiTestMixin,
)


class TestConversationAutoTitle(FastApiRuntimeApiTestMixin):
    def _scope(self) -> dict[str, str]:
        return {"org_id": self.Values.ORG_ID, "user_id": self.Values.USER_ID}

    def _create_untitled(self, client: TestClient) -> str:
        """A conversation with NO title — the state this feature exists for.

        The shared mixin's `conversation_payload` carries "Launch review", so a
        fixture built on it can only ever exercise the leave-it-alone branch.
        """
        payload = {
            key: value
            for key, value in self.conversation_payload().items()
            if key != "title"
        }
        response = client.post("/v1/agent/conversations", json=payload)
        assert response.status_code == 200
        return str(response.json()["conversation_id"])

    def _title(self, client: TestClient, conversation_id: str) -> str | None:
        response = client.get(
            f"/v1/agent/conversations/{conversation_id}",
            params=self._scope(),
        )
        assert response.status_code == 200
        title = response.json().get("title")
        return None if title is None else str(title)

    async def test_an_untitled_conversation_is_named_from_its_first_message(
        self,
    ) -> None:
        client, _ = self.create_client()
        conversation_id = self._create_untitled(client)
        assert self._title(client, conversation_id) in (None, "")

        await self.create_run(client, conversation_id)

        title = self._title(client, conversation_id)
        assert title
        assert title != "Untitled run"
        # Named after what was actually said, not a generic placeholder.
        assert title == self.run_payload(conversation_id)["user_input"]

    async def test_a_user_supplied_title_is_never_overwritten(self) -> None:
        """The user's own name for a thread outranks anything derived.

        This also covers the desktop first-run flow, which creates the
        conversation WITH a title — auto-naming must not rename it on the very
        first send.
        """
        client, _ = self.create_client()
        conversation = await self.create_conversation(client)
        patched = client.patch(
            f"/v1/agent/conversations/{conversation['conversation_id']}",
            params=self._scope(),
            json={"title": "Q3 migration plan"},
        )
        assert patched.status_code == 200

        await self.create_run(client, conversation["conversation_id"])

        assert (
            self._title(client, conversation["conversation_id"]) == "Q3 migration plan"
        )

    async def test_naming_does_not_fail_the_run(self) -> None:
        """A run whose conversation cannot be renamed still answers.

        Naming is cosmetic; failing the run over it would turn a naming problem
        into an unanswerable prompt.
        """
        client, _ = self.create_client()
        conversation = await self.create_conversation(client)
        response = client.post(
            "/v1/agent/runs",
            json=self.run_payload(conversation["conversation_id"]),
        )
        assert response.status_code == 200
        assert response.json()["run_id"]
