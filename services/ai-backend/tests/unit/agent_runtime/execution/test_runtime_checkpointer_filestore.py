"""File-store checkpointer wiring (desktop only).

Covers the DoD restart test for wiring #1: on the ``file`` store backend the
runtime checkpointer is a durable ``AsyncSqliteSaver`` under
``<root>/index/checkpoints.sqlite3`` whose state survives a simulated worker
restart. Every other backend keeps the in-memory saver.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.base import empty_checkpoint

from agent_runtime.execution import deep_agent_builder as builder_module


class _CheckpointerEnvMixin:
    """Reset the process-global checkpointer singleton around each test."""

    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        builder_module._runtime_checkpointer = None
        yield
        builder_module._runtime_checkpointer = None

    @staticmethod
    def _thread_config(thread_id: str = "thread-1") -> dict[str, object]:
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


class TestNonDesktopCheckpointerUnchanged(_CheckpointerEnvMixin):
    def test_returns_in_memory_saver_when_backend_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("RUNTIME_STORE_BACKEND", raising=False)
        monkeypatch.delenv("RUNTIME_FILE_STORE_ROOT", raising=False)
        saver = builder_module.runtime_checkpointer()
        assert type(saver).__name__ == "InMemorySaver"

    def test_in_memory_saver_when_file_root_missing(self, monkeypatch) -> None:
        # Backend=file but no root: fail safe to the in-memory saver rather than
        # constructing a sqlite file at an unknown path.
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "file")
        monkeypatch.delenv("RUNTIME_FILE_STORE_ROOT", raising=False)
        saver = builder_module.runtime_checkpointer()
        assert type(saver).__name__ == "InMemorySaver"

    def test_explicit_checkpointer_passthrough(self, monkeypatch) -> None:
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "postgres")
        sentinel = object()
        assert builder_module.runtime_checkpointer(sentinel) is sentinel


class TestFileStoreCheckpointer(_CheckpointerEnvMixin):
    async def test_sqlite_saver_survives_restart(self, tmp_path, monkeypatch) -> None:
        root = tmp_path / "store"
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "file")
        monkeypatch.setenv("RUNTIME_FILE_STORE_ROOT", str(root))

        saver = builder_module.runtime_checkpointer()
        assert type(saver).__name__ == "AsyncSqliteSaver"
        db_path = root / "index" / "checkpoints.sqlite3"

        config = self._thread_config()
        checkpoint = empty_checkpoint()
        await saver.aput(
            config,
            checkpoint,
            {"source": "loop", "step": 1, "parents": {}},
            {},
        )
        got = await saver.aget_tuple(config)
        assert got is not None
        assert got.checkpoint["id"] == checkpoint["id"]
        assert db_path.is_file()
        # Close the aiosqlite connection so its worker thread does not linger.
        await saver.conn.close()

        # Simulate a worker restart: drop the singleton so a fresh saver
        # reconnects to the same on-disk database.
        builder_module._runtime_checkpointer = None
        reopened = builder_module.runtime_checkpointer()
        assert type(reopened).__name__ == "AsyncSqliteSaver"
        restored = await reopened.aget_tuple(config)
        assert restored is not None
        assert restored.checkpoint["id"] == checkpoint["id"]
        await reopened.conn.close()

    async def test_checkpoint_db_is_not_the_disposable_index(
        self, tmp_path, monkeypatch
    ) -> None:
        # The checkpoint DB lives at index/checkpoints.sqlite3, distinct from the
        # rebuildable catalog index (index/catalog.sqlite3) — wiping the catalog
        # must never drop in-flight graph state.
        root = tmp_path / "store"
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "file")
        monkeypatch.setenv("RUNTIME_FILE_STORE_ROOT", str(root))
        saver = builder_module.runtime_checkpointer()
        await saver.aput(
            self._thread_config(),
            empty_checkpoint(),
            {"source": "loop", "step": 1, "parents": {}},
            {},
        )
        assert (root / "index" / "checkpoints.sqlite3").is_file()
        assert (root / "index" / "checkpoints.sqlite3").name != "catalog.sqlite3"
        await saver.conn.close()


class TestServerPostgresCheckpointer(_CheckpointerEnvMixin):
    """Server-profile selection of the durable AsyncPostgresSaver.

    These are SELECTION tests only: the pool is built with ``open=False`` so no
    live Postgres is touched. Durability/resume ("kill a worker mid-run, it
    resumes") needs a real database and belongs in an integration suite, not
    PR-CI — so these never call ``setup()``/``aput``.
    """

    _DATABASE_URL = "postgresql://u:p@127.0.0.1:5432/does_not_connect"

    async def test_returns_postgres_saver_on_server_profile(self, monkeypatch) -> None:
        # RUNTIME_STORE_BACKEND=postgres + DATABASE_URL selects the durable
        # AsyncPostgresSaver over a connection pool that is constructed but NOT
        # opened — setup_runtime_checkpointer() owns opening it at the worker
        # startup seam, so import/selection never blocks on a live database.
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "postgres")
        monkeypatch.setenv("DATABASE_URL", self._DATABASE_URL)
        monkeypatch.delenv("RUNTIME_FILE_STORE_ROOT", raising=False)

        saver = builder_module.runtime_checkpointer()
        assert type(saver).__name__ == "AsyncPostgresSaver"

        from psycopg_pool import AsyncConnectionPool

        assert isinstance(saver.conn, AsyncConnectionPool)
        # ``closed`` is True for a pool built with open=False and never opened.
        assert saver.conn.closed is True

    def test_postgres_backend_without_database_url_falls_back(
        self, monkeypatch
    ) -> None:
        # backend=postgres but no DATABASE_URL: the postgres builder returns None
        # and the or-chain falls through to the in-memory saver rather than
        # constructing a pool with no conninfo.
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "postgres")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("RUNTIME_FILE_STORE_ROOT", raising=False)
        saver = builder_module.runtime_checkpointer()
        assert type(saver).__name__ == "InMemorySaver"

    async def test_desktop_file_store_wins_over_database_url(
        self, tmp_path, monkeypatch
    ) -> None:
        # The or-chain order is load-bearing: the desktop file store is tried
        # first, so a single_user_desktop whose env also carries DATABASE_URL
        # still gets the SQLite saver, never the postgres one.
        root = tmp_path / "store"
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "file")
        monkeypatch.setenv("RUNTIME_FILE_STORE_ROOT", str(root))
        monkeypatch.setenv("DATABASE_URL", self._DATABASE_URL)
        saver = builder_module.runtime_checkpointer()
        assert type(saver).__name__ == "AsyncSqliteSaver"
        await saver.conn.close()

    async def test_setup_runtime_checkpointer_noop_when_not_postgres(
        self, monkeypatch
    ) -> None:
        # setup_runtime_checkpointer() is duck-typed on the class name: on a
        # non-postgres saver it returns without opening anything (the in-memory
        # saver has no pool), so calling it on the dev/test profile is safe.
        monkeypatch.delenv("RUNTIME_STORE_BACKEND", raising=False)
        monkeypatch.delenv("RUNTIME_FILE_STORE_ROOT", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        await builder_module.setup_runtime_checkpointer()
        assert type(builder_module._runtime_checkpointer).__name__ == "InMemorySaver"
