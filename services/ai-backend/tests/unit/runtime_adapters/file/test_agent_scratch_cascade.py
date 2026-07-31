"""Deleting a chat deletes its `.tmp` directory — PRD-FS-12 D6, end to end.

These drive the REAL deletion path: `ConversationCoordinator.delete_conversation`
on a real `FileRuntimeApiStore`, with `COPILOT_HOME` pointed at a tmp dir so the
scratch resolution under test is the production one rather than an injected
root. Nothing about the scratch is stubbed; the only thing configured is where
the user's home happens to be.

That distinction is the point. A test that handed the store a scratch object
would prove the store calls whatever it was given, not that the resolution the
packaged app performs reaches the directory the packaged app writes.
"""

from __future__ import annotations

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.capabilities.desktop.agent_scratch import (
    COPILOT_HOME_ENV,
    agent_scratch_root,
)
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas import CreateConversationRequest
from runtime_worker.agent_scratch_wiring import AgentScratchWorkerWiring

_ORG = "org_scratch"
_USER = "user_scratch"


@pytest.fixture
def copilot_home(tmp_path, monkeypatch):
    """Point the production `COPILOT_HOME` resolution at a temporary directory."""

    home = tmp_path / "copilot-home"
    monkeypatch.setenv(COPILOT_HOME_ENV, str(home))
    return home


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


def _coordinator(store: FileRuntimeApiStore) -> ConversationCoordinator:
    settings = _settings()
    event_producer = RuntimeEventProducer(
        persistence=store, event_store=store, on_event_appended=None
    )
    return ConversationCoordinator(
        persistence=store,
        settings=settings,
        run_coordinator=RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=event_producer,
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        ),
    )


async def _conversation(store: FileRuntimeApiStore):
    return await _coordinator(store).create_conversation(
        CreateConversationRequest(
            org_id=_ORG, user_id=_USER, assistant_id="assistant", metadata={}
        )
    )


async def _store(tmp_path) -> FileRuntimeApiStore:
    store = FileRuntimeApiStore(tmp_path / "store")
    await store.open()
    return store


class TestDeletingAChatDeletesItsScratch:
    """D6, through the coordinator the HTTP route actually calls."""

    async def test_the_scratch_tree_is_gone_after_delete(
        self, tmp_path, copilot_home
    ) -> None:
        store = await _store(tmp_path)
        conversation = await _conversation(store)
        conversation_id = conversation.conversation_id

        scratch = agent_scratch_root().conversation(conversation_id)
        scratch.provision(title="Q3 plan for Acme Corp")
        run = scratch.run("run-1").provision()
        (run.tool_results / "big.txt").write_text("offloaded output")
        (scratch.drafts / "reply.md").write_text("draft body")
        assert scratch.path.is_dir()

        await _coordinator(store).delete_conversation(
            org_id=_ORG, user_id=_USER, conversation_id=conversation_id
        )

        assert not scratch.path.exists()
        # ...and the scratch ROOT survives — we delete a chat, not the tree.
        assert agent_scratch_root().path.is_dir()

    async def test_deleting_one_chat_leaves_another_chats_scratch_intact(
        self, tmp_path, copilot_home
    ) -> None:
        store = await _store(tmp_path)
        doomed = await _conversation(store)
        kept = await _conversation(store)
        root = agent_scratch_root()
        doomed_scratch = root.conversation(doomed.conversation_id).provision()
        kept_scratch = root.conversation(kept.conversation_id).provision()

        await _coordinator(store).delete_conversation(
            org_id=_ORG, user_id=_USER, conversation_id=doomed.conversation_id
        )

        assert not doomed_scratch.path.exists()
        assert kept_scratch.meta_path.is_file()

    async def test_deleting_a_chat_that_never_ran_is_not_an_error(
        self, tmp_path, copilot_home
    ) -> None:
        """`.tmp/<conv>/` is created on first NEED, so it usually does not exist."""

        store = await _store(tmp_path)
        conversation = await _conversation(store)

        await _coordinator(store).delete_conversation(
            org_id=_ORG, user_id=_USER, conversation_id=conversation.conversation_id
        )

        assert (
            await store.get_conversation(
                org_id=_ORG,
                user_id=_USER,
                conversation_id=conversation.conversation_id,
            )
        ).deleted_at is not None


class TestNothingElseRemovesFromTheScratch:
    """D8 — no timer, no TTL, no size cap. Only D6 collects."""

    async def test_a_retention_purge_of_a_DIFFERENT_chat_leaves_this_one(
        self, tmp_path, copilot_home
    ) -> None:
        """The hard-purge site cascades to the right conversation only."""

        store = await _store(tmp_path)
        purged = await _conversation(store)
        bystander = await _conversation(store)
        root = agent_scratch_root()
        purged_scratch = root.conversation(purged.conversation_id).provision()
        bystander_scratch = root.conversation(bystander.conversation_id).provision()

        record = await store.get_conversation(
            org_id=_ORG, user_id=_USER, conversation_id=purged.conversation_id
        )
        store._drop_conversation_state(record, victim_runs=())

        assert not purged_scratch.path.exists()
        assert bystander_scratch.path.is_dir()


class TestProvisioningIsDesktopOnly:
    """The scratch is a desktop concept; a hosted image must not create one."""

    def test_no_workspace_backend_provisions_nothing(self, copilot_home) -> None:
        """A hosted deployment would otherwise write per-tenant scratch onto the
        SERVER's home directory for every conversation it hosts."""

        wiring = AgentScratchWorkerWiring(workspace_backend=None)

        assert wiring.enabled is False
        assert wiring.provision(conversation_id="conv1", run_id="run1") is None
        assert not copilot_home.exists()

    def test_a_workspace_backend_provisions_the_run_tier(self, copilot_home) -> None:
        wiring = AgentScratchWorkerWiring(workspace_backend=object())

        scratch = wiring.provision(conversation_id="conv1", run_id="run1", title="Hi")

        assert scratch is not None
        assert scratch.meta_path.is_file()
        assert scratch.drafts.is_dir()
        assert scratch.run("run1").tool_results.is_dir()
        assert scratch.run("run1").subagents.is_dir()

    def test_an_unusable_conversation_id_provisions_nothing_and_does_not_raise(
        self, copilot_home
    ) -> None:
        """Fail closed: no scratch beats a directory named after user content."""

        wiring = AgentScratchWorkerWiring(workspace_backend=object())

        assert wiring.provision(conversation_id="Q3 plan for Acme") is None
        assert not (copilot_home / ".tmp").exists()
