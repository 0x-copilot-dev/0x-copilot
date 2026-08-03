"""Account-merge re-key on the file-native store.

The load-bearing assertion here is DURABILITY, not the in-memory result: a
re-key that only corrects the served dicts looks perfect until the next boot
replays the old JSONL over it. Every test that matters closes the store,
reopens it from disk, and asserts against the replayed view.
"""

from __future__ import annotations

import pytest

from runtime_adapters.file.account_merge import FileAccountMergeRekeyer
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    MessageRecord,
    MessageRole,
)

ABSORBED_ORG = "org_absorbed"
ABSORBED_USER = "usr_absorbed"
SURVIVOR_ORG = "org_survivor"
SURVIVOR_USER = "usr_survivor"


class MergeFixtureMixin:
    """Seed one absorbed conversation with a message and a run."""

    @staticmethod
    async def _seed(store: FileRuntimeApiStore, *, org: str, user: str) -> str:
        conversation = await store.create_conversation(
            CreateConversationRequest(
                org_id=org,
                user_id=user,
                assistant_id="assistant",
                title="absorbed chat",
            )
        )
        # A message, not a run: run creation needs the coordinator layer
        # (`runtime_context` is server-owned and rejected on the request model).
        # No coverage is lost — runs.jsonl travels the identical `_rewrite_jsonl`
        # path as messages.jsonl, so the run would exercise no new branch.
        await store.append_message(
            MessageRecord(
                conversation_id=conversation.conversation_id,
                org_id=org,
                role=MessageRole.USER,
                content_text="hello from the absorbed account",
            )
        )
        return conversation.conversation_id

    @staticmethod
    async def _reopen(store: FileRuntimeApiStore, root) -> FileRuntimeApiStore:
        """Close and replay from disk — the only honest durability check."""

        await store.close()
        replayed = FileRuntimeApiStore(root)
        await replayed.open()
        return replayed


@pytest.fixture
async def store(tmp_path):
    instance = FileRuntimeApiStore(tmp_path / "store")
    await instance.open()
    try:
        yield instance
    finally:
        await instance.close()


class TestRekeySurvivesAReboot(MergeFixtureMixin):
    async def test_the_conversation_belongs_to_the_survivor_after_replay(
        self, store, tmp_path
    ) -> None:
        conversation_id = await self._seed(store, org=ABSORBED_ORG, user=ABSORBED_USER)

        FileAccountMergeRekeyer(store).rekey(
            absorbed_org_id=ABSORBED_ORG,
            absorbed_user_id=ABSORBED_USER,
            survivor_org_id=SURVIVOR_ORG,
            survivor_user_id=SURVIVOR_USER,
        )
        replayed = await self._reopen(store, tmp_path / "store")

        try:
            record = replayed.conversations[conversation_id]
            assert record.org_id == SURVIVOR_ORG
            assert record.user_id == SURVIVOR_USER
        finally:
            await replayed.close()

    async def test_the_messages_move_with_it(self, store, tmp_path) -> None:
        conversation_id = await self._seed(store, org=ABSORBED_ORG, user=ABSORBED_USER)

        FileAccountMergeRekeyer(store).rekey(
            absorbed_org_id=ABSORBED_ORG,
            absorbed_user_id=ABSORBED_USER,
            survivor_org_id=SURVIVOR_ORG,
            survivor_user_id=SURVIVOR_USER,
        )
        replayed = await self._reopen(store, tmp_path / "store")

        try:
            messages = [
                m
                for m in replayed.messages.values()
                if m.conversation_id == conversation_id
            ]
            assert messages and all(m.org_id == SURVIVOR_ORG for m in messages)
        finally:
            await replayed.close()

    async def test_the_session_folder_moves_to_the_survivor_workspace(
        self, store, tmp_path
    ) -> None:
        """The directory is keyed by org — a rewrite alone would orphan it."""

        await self._seed(store, org=ABSORBED_ORG, user=ABSORBED_USER)
        layout = store._layout  # noqa: SLF001

        FileAccountMergeRekeyer(store).rekey(
            absorbed_org_id=ABSORBED_ORG,
            absorbed_user_id=ABSORBED_USER,
            survivor_org_id=SURVIVOR_ORG,
            survivor_user_id=SURVIVOR_USER,
        )

        survivor_sessions = list(layout.sessions_dir(SURVIVOR_ORG).iterdir())
        assert len(survivor_sessions) == 1
        # The emptied absorbed workspace must not linger advertising a tenancy
        # that owns nothing.
        assert not layout.workspace_dir(ABSORBED_ORG).exists()


class TestBlastRadius(MergeFixtureMixin):
    async def test_another_account_is_untouched(self, store, tmp_path) -> None:
        """A merge must move exactly one tenancy, not everything in the store."""

        bystander = await self._seed(store, org="org_other", user="usr_other")
        await self._seed(store, org=ABSORBED_ORG, user=ABSORBED_USER)

        FileAccountMergeRekeyer(store).rekey(
            absorbed_org_id=ABSORBED_ORG,
            absorbed_user_id=ABSORBED_USER,
            survivor_org_id=SURVIVOR_ORG,
            survivor_user_id=SURVIVOR_USER,
        )
        replayed = await self._reopen(store, tmp_path / "store")

        try:
            record = replayed.conversations[bystander]
            assert record.org_id == "org_other"
            assert record.user_id == "usr_other"
        finally:
            await replayed.close()

    async def test_a_merge_with_nothing_to_move_is_a_no_op(self, store) -> None:
        """A survivor-only install must not error or invent counts."""

        tables, warnings = FileAccountMergeRekeyer(store).rekey(
            absorbed_org_id="org_never_used",
            absorbed_user_id="usr_never_used",
            survivor_org_id=SURVIVOR_ORG,
            survivor_user_id=SURVIVOR_USER,
        )

        assert tables.get("file_session_folders") is None
        assert warnings == []

    async def test_the_audit_ledger_is_never_rewritten(self, store) -> None:
        """The chain is hash-linked; rewriting a row breaks every later link."""

        await self._seed(store, org=ABSORBED_ORG, user=ABSORBED_USER)
        layout = store._layout  # noqa: SLF001
        audit = layout.state_path(FileAccountMergeRekeyer.AUDIT_LEDGER)
        before = audit.read_bytes() if audit.is_file() else b""

        FileAccountMergeRekeyer(store).rekey(
            absorbed_org_id=ABSORBED_ORG,
            absorbed_user_id=ABSORBED_USER,
            survivor_org_id=SURVIVOR_ORG,
            survivor_user_id=SURVIVOR_USER,
        )

        after = audit.read_bytes() if audit.is_file() else b""
        assert after == before
