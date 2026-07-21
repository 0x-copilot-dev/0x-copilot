"""CLI-level tests for ``runtime_adapters.migrate`` `--on-boot` mode.

`--on-boot` is the command the desktop supervisor runs on the first file-store
boot to carry existing Postgres history across (slice 3). These tests pin its
**fail-safe exit contract** without a live database by substituting the Postgres
source with an in-memory-backed, Postgres-shaped fake:

* migrated / nothing-to-migrate -> exit 0 (the file store is authoritative);
* verify mismatch               -> exit 2 (import is not trustworthy);
* any other failure / bad args  -> exit 1.

The supervisor maps any non-zero to "serve the Postgres store this boot", so
these codes are the safety boundary between "file store now authoritative" and
"fall back, never strand the user".
"""

from __future__ import annotations

import runtime_adapters.migrate as migrate_cli
from runtime_adapters.file.migration import MigrationVerificationError
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    MessageRecord,
    MessageRole,
)


class _FakePgSource:
    """Postgres-shaped migration source over an in-memory backing store.

    Presents the exact structural surface ``_run_on_boot`` consumes: ``open`` /
    ``close``, an async ``list_conversation_scopes`` (the Postgres adapter's
    ``SELECT DISTINCT``), and the port read methods. It deliberately does NOT
    expose a ``conversations`` mapping, so the migrator is forced down the
    ``list_conversation_scopes`` auto-discovery branch — the real Postgres path.
    """

    def __init__(self, backing: InMemoryRuntimeApiStore) -> None:
        self._backing = backing
        self.opened = False
        self.closed = False

    async def open(self) -> None:
        self.opened = True

    async def close(self) -> None:
        self.closed = True

    async def list_conversation_scopes(self) -> list[tuple[str, str]]:
        return sorted(
            {(c.org_id, c.user_id) for c in self._backing.conversations.values()}
        )

    async def list_conversations(self, **kwargs):
        return await self._backing.list_conversations(**kwargs)

    async def get_conversation(self, **kwargs):
        return await self._backing.get_conversation(**kwargs)

    async def list_messages(self, **kwargs):
        return await self._backing.list_messages(**kwargs)

    async def get_run(self, **kwargs):
        return await self._backing.get_run(**kwargs)

    async def list_events_after(self, **kwargs):
        return await self._backing.list_events_after(**kwargs)


async def _seed(store, *, org: str, user: str) -> str:
    conversation = await store.create_conversation(
        CreateConversationRequest(
            org_id=org, user_id=user, assistant_id="assistant", metadata={}
        )
    )
    cid = conversation.conversation_id
    await store.append_message(
        MessageRecord(
            conversation_id=cid,
            org_id=org,
            role=MessageRole.USER,
            content_text="hello",
        )
    )
    return cid


def _on_boot_args(dest_root, *, source="postgres", url="postgresql://x/y"):
    argv = ["--on-boot", "--source", source, "--dest-root", str(dest_root)]
    if url is not None:
        argv += ["--source-database-url", url]
    return migrate_cli._build_arg_parser().parse_args(argv)


def _patch_source(monkeypatch, source) -> None:
    import runtime_adapters.postgres as pg_module

    monkeypatch.setattr(pg_module, "PostgresRuntimeApiStore", lambda *a, **k: source)


class TestOnBootArgGuards:
    # Sync tests on purpose: main() drives its own asyncio.run(), so these must
    # NOT run inside pytest-asyncio's event loop. The guards fire before any
    # store is constructed, so this exercises the full flag wiring + dispatch.

    def test_rejects_non_postgres_source(self, tmp_path) -> None:
        code = migrate_cli.main(
            [
                "--on-boot",
                "--source",
                "in_memory",
                "--dest-root",
                str(tmp_path / "dst"),
            ]
        )
        assert code == 1

    def test_requires_database_url(self, tmp_path) -> None:
        code = migrate_cli.main(
            ["--on-boot", "--source", "postgres", "--dest-root", str(tmp_path / "dst")]
        )
        assert code == 1


class TestOnBootImport:
    async def test_migrates_every_tenant_and_exits_zero(
        self, tmp_path, monkeypatch
    ) -> None:
        backing = InMemoryRuntimeApiStore()
        await backing.open()
        cid_a = await _seed(backing, org="org_a", user="user_a")
        cid_b = await _seed(backing, org="org_b", user="user_b")
        source = _FakePgSource(backing)
        _patch_source(monkeypatch, source)

        dest_root = tmp_path / "dst"
        code = await migrate_cli._run_on_boot(_on_boot_args(dest_root))

        assert code == 0
        assert source.opened and source.closed
        # Both tenants' conversations are readable in the destination file store.
        dest = FileRuntimeApiStore(dest_root)
        await dest.open()
        try:
            assert (
                await dest.get_conversation(
                    org_id="org_a", user_id="user_a", conversation_id=cid_a
                )
                is not None
            )
            assert (
                await dest.get_conversation(
                    org_id="org_b", user_id="user_b", conversation_id=cid_b
                )
                is not None
            )
        finally:
            await dest.close()
        await backing.close()

    async def test_empty_source_is_a_clean_no_op_exit_zero(
        self, tmp_path, monkeypatch
    ) -> None:
        # Fresh install: nothing in Postgres -> exit 0, empty destination. This
        # is the common case and MUST NOT fall back to Postgres.
        backing = InMemoryRuntimeApiStore()
        await backing.open()
        source = _FakePgSource(backing)
        _patch_source(monkeypatch, source)

        dest_root = tmp_path / "dst"
        code = await migrate_cli._run_on_boot(_on_boot_args(dest_root))

        assert code == 0
        dest = FileRuntimeApiStore(dest_root)
        await dest.open()
        try:
            listed = await dest.list_conversations(
                org_id="org_a", user_id="user_a", limit=10, include_deleted=True
            )
            assert listed == ()
        finally:
            await dest.close()
        await backing.close()

    async def test_verify_mismatch_exits_two(self, tmp_path, monkeypatch) -> None:
        backing = InMemoryRuntimeApiStore()
        await backing.open()
        source = _FakePgSource(backing)
        _patch_source(monkeypatch, source)

        class _MismatchingMigrator:
            def __init__(self, *a, **k) -> None: ...

            async def migrate(self, *a, **k):
                raise MigrationVerificationError(["conv_x: event count 3 != 2"])

        monkeypatch.setattr(migrate_cli, "StoreMigrator", _MismatchingMigrator)

        code = await migrate_cli._run_on_boot(_on_boot_args(tmp_path / "dst"))

        # A verify failure is exit 2 — distinct from other failures so the
        # supervisor could treat it specially if it ever wants to.
        assert code == 2
        # The source was still cleanly closed on the failure path.
        assert source.closed

    async def test_unexpected_error_exits_one(self, tmp_path, monkeypatch) -> None:
        class _ExplodingOpen(_FakePgSource):
            async def open(self) -> None:
                raise RuntimeError("connection refused")

        backing = InMemoryRuntimeApiStore()
        await backing.open()
        source = _ExplodingOpen(backing)
        _patch_source(monkeypatch, source)

        code = await migrate_cli._run_on_boot(_on_boot_args(tmp_path / "dst"))

        # Unreachable source (or any other error) -> exit 1, supervisor falls
        # back to Postgres. Never a crash, never a partial write claimed as good.
        assert code == 1
        await backing.close()
