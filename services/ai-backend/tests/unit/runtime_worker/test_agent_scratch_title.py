"""The chat's name reaches its scratch ``meta.json`` (PRD-FS-12 D5).

Every ``meta.json`` on the live machine read ``"title": null`` — nineteen of
nineteen. The provisioner was correct and ``AgentScratchWorkerWiring.provision``
had taken a ``title`` since the day it shipped; the run path simply never passed
one, and because ``provision`` rewrites ``meta.json`` on every run, a title
written by anything else would have been erased by the next one.

So these tests drive the HANDLER seam against a real store rather than the
provisioner: the provisioner was never the broken part, and a test that calls it
directly is exactly the test that stayed green through the whole defect.
"""

from __future__ import annotations

import json

import pytest

from agent_runtime.capabilities.desktop.agent_scratch import agent_scratch_root
from runtime_api.schemas.conversations import CreateConversationRequest

from tests.unit.runtime_worker.test_workspace_effect_wiring import (
    _attach,
    _command,
    _handler,
)

pytestmark = pytest.mark.anyio


class _WorkspacePresent:
    """The desktop gate. Provisioning keys off "a workspace backend exists"."""


class TestScratchMetaTitle:
    """What a human browsing ``$COPILOT_HOME/.tmp/<id>/`` is told."""

    @staticmethod
    def _scratch_home(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
        monkeypatch.setenv("COPILOT_HOME", str(tmp_path))

    @staticmethod
    async def _titled_conversation(store: object, title: str | None) -> str:
        command = _command()
        conversation = await store.create_conversation(  # type: ignore[attr-defined]
            CreateConversationRequest(
                org_id=command.org_id, user_id=command.user_id, title=title
            )
        )
        return conversation.conversation_id

    @staticmethod
    def _meta(conversation_id: str) -> dict[str, object]:
        return json.loads(
            (agent_scratch_root().conversation(conversation_id).meta_path).read_text(
                encoding="utf-8"
            )
        )

    async def test_the_scratch_is_named_by_the_chat_it_belongs_to(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        """The defect, stated positively.

        The desktop creates conversations WITH a title — the FTUE sends the
        first 60 characters of the prompt (``firstRunTitle``), the destination
        binder sends ``"Desktop session"`` — so this is the ordinary case, not
        an edge one, and it is the case that produced every ``null`` on disk.
        """

        self._scratch_home(monkeypatch, tmp_path)
        handler, store = _handler(sessions=None, broker=_attach())
        conversation_id = await self._titled_conversation(
            store, "Reconcile the July treasury spreadsheet"
        )
        command = _command().model_copy(update={"conversation_id": conversation_id})

        await handler._provision_agent_scratch(
            command, workspace_backend=_WorkspacePresent()
        )

        meta = self._meta(conversation_id)
        assert meta["title"] == "Reconcile the July treasury spreadsheet"
        assert meta["conversation_id"] == conversation_id

    async def test_a_rename_is_picked_up_by_the_next_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        """Read per run, not carried on the command.

        A command is a snapshot taken at enqueue time. Had the title travelled
        on it, a renamed chat would keep announcing its old name for the life of
        the conversation — and ``provision`` rewriting ``meta.json`` every run,
        which is what makes the rewrite worth doing at all, would be pointless.
        """

        self._scratch_home(monkeypatch, tmp_path)
        handler, store = _handler(sessions=None, broker=_attach())
        conversation_id = await self._titled_conversation(store, "Untitled draft")
        command = _command().model_copy(update={"conversation_id": conversation_id})
        await handler._provision_agent_scratch(
            command, workspace_backend=_WorkspacePresent()
        )
        assert self._meta(conversation_id)["title"] == "Untitled draft"

        await store.update_conversation(
            org_id=command.org_id,
            user_id=command.user_id,
            conversation_id=conversation_id,
            title="Q3 board memo",
            title_changed=True,
            folder=None,
            folder_changed=False,
            archived=None,
            archived_changed=False,
            project_id=None,
            project_id_changed=False,
            now=command.created_at,
        )
        await handler._provision_agent_scratch(
            command, workspace_backend=_WorkspacePresent()
        )

        assert self._meta(conversation_id)["title"] == "Q3 board memo"

    async def test_an_untitled_chat_still_gets_its_working_directories(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        """``None`` is a real answer, not a failure — the run still needs the dirs."""

        self._scratch_home(monkeypatch, tmp_path)
        handler, store = _handler(sessions=None, broker=_attach())
        conversation_id = await self._titled_conversation(store, None)
        command = _command().model_copy(update={"conversation_id": conversation_id})

        await handler._provision_agent_scratch(
            command, workspace_backend=_WorkspacePresent()
        )

        assert self._meta(conversation_id)["title"] is None
        assert agent_scratch_root().conversation(conversation_id).drafts.is_dir()

    async def test_no_workspace_backend_writes_nothing_at_all(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        """The desktop gate.

        On a hosted image there is no user at the machine and no host filesystem
        lane; provisioning there would put per-tenant scratch on the SERVER's
        home directory, one tree per conversation in the deployment.
        """

        self._scratch_home(monkeypatch, tmp_path)
        handler, store = _handler(sessions=None, broker=_attach())
        conversation_id = await self._titled_conversation(store, "Hosted chat")
        command = _command().model_copy(update={"conversation_id": conversation_id})

        await handler._provision_agent_scratch(command, workspace_backend=None)

        assert not agent_scratch_root().conversation(conversation_id).path.exists()

    async def test_a_store_that_cannot_answer_does_not_fail_the_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: object
    ) -> None:
        """Orientation is not worth a failed run.

        A title is what a human reads when browsing ``.tmp/``; the directories
        are what the agent needs in order to work at all. Losing the first must
        never cost the second.
        """

        self._scratch_home(monkeypatch, tmp_path)
        handler, store = _handler(sessions=None, broker=_attach())
        conversation_id = await self._titled_conversation(store, "Doomed lookup")
        command = _command().model_copy(update={"conversation_id": conversation_id})

        async def _explode(**_kwargs: object) -> None:
            raise RuntimeError("store is down")

        monkeypatch.setattr(store, "get_conversation", _explode)

        await handler._provision_agent_scratch(
            command, workspace_backend=_WorkspacePresent()
        )

        assert self._meta(conversation_id)["title"] is None
        assert agent_scratch_root().conversation(conversation_id).drafts.is_dir()
