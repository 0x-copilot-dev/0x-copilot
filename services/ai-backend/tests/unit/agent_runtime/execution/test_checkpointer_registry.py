"""Checkpointer selection — the domain-side half of a storage backend.

``runtime_checkpointer()`` must pick the durable saver the selected backend
registered, and fall back to the process-local in-memory saver whenever there is
no durable option. Getting the fallback wrong is silent: the run works, and
in-flight graph state (paused approvals included) dies with the process.
"""

from __future__ import annotations

import pytest

from agent_runtime.execution import deep_agent_builder
from agent_runtime.execution.checkpointing import (
    CheckpointerRegistry,
    build_in_memory_checkpointer,
    selected_backend,
)


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    """The saver is a module singleton; no test may leak one into the next."""

    monkeypatch.setattr(deep_agent_builder, "_runtime_checkpointer", None)


class TestBackendSelection:
    def test_an_unset_backend_reads_as_empty(self, monkeypatch) -> None:
        monkeypatch.delenv("RUNTIME_STORE_BACKEND", raising=False)

        assert selected_backend() == ""

    def test_the_backend_name_is_normalized(self, monkeypatch) -> None:
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "  FILE  ")

        assert selected_backend() == "file"


class TestRegistry:
    def test_an_unregistered_backend_has_no_durable_saver(self) -> None:
        assert CheckpointerRegistry().build("in_memory_async") is None

    def test_a_registered_builder_is_used(self) -> None:
        registry = CheckpointerRegistry()
        registry.register("mystore", lambda: "SAVER")

        assert registry.build("mystore") == "SAVER"

    def test_a_builder_may_decline_by_returning_none(self) -> None:
        """Registered but env-gated off is the same outcome as unregistered."""

        registry = CheckpointerRegistry()
        registry.register("mystore", lambda: None)

        assert registry.build("mystore") is None

    def test_names_lists_only_backends_with_a_durable_saver(self) -> None:
        registry = CheckpointerRegistry()
        registry.register("b", lambda: None)
        registry.register("a", lambda: None)

        assert registry.names() == ("a", "b")

    def test_the_shipped_registry_covers_file_and_postgres_but_not_in_memory(
        self,
    ) -> None:
        from agent_runtime.execution.checkpointing import CHECKPOINTERS

        assert CHECKPOINTERS.names() == ("file", "postgres")
        # A process-local store offering a "durable" saver would be a lie.
        assert CHECKPOINTERS.build("in_memory_async") is None


class TestRuntimeCheckpointerSelection:
    def test_an_explicit_checkpointer_always_wins(self) -> None:
        assert deep_agent_builder.runtime_checkpointer("EXPLICIT") == "EXPLICIT"

    def test_a_backend_with_no_durable_saver_falls_back_to_in_memory(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "in_memory_async")

        saver = deep_agent_builder.runtime_checkpointer()

        assert type(saver).__name__ in {"InMemorySaver", "MemorySaver"}

    async def test_the_file_backend_gets_a_durable_sqlite_saver(
        self, monkeypatch, tmp_path
    ) -> None:
        # Async on purpose: ``AsyncSqliteSaver`` binds to the running loop at
        # construction, so the saver can only be built from inside one — which
        # is where the worker and the API startup seam both call it.
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "file")
        monkeypatch.setenv("RUNTIME_FILE_STORE_ROOT", str(tmp_path / "store"))

        saver = deep_agent_builder.runtime_checkpointer()

        assert type(saver).__name__ == "AsyncSqliteSaver"
        # Beside the disposable catalog index, never inside it: wiping
        # index/catalog.sqlite3 must not drop in-flight graph state.
        assert (tmp_path / "store" / "index").is_dir()

    def test_the_file_backend_without_a_root_falls_back_rather_than_crashing(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "file")
        monkeypatch.delenv("RUNTIME_FILE_STORE_ROOT", raising=False)

        saver = deep_agent_builder.runtime_checkpointer()

        assert type(saver).__name__ in {"InMemorySaver", "MemorySaver"}

    def test_the_saver_is_a_singleton_across_calls(self, monkeypatch) -> None:
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "in_memory_async")

        assert deep_agent_builder.runtime_checkpointer() is (
            deep_agent_builder.runtime_checkpointer()
        )

    def test_in_memory_builder_returns_a_fresh_saver_each_call(self) -> None:
        assert build_in_memory_checkpointer() is not build_in_memory_checkpointer()
