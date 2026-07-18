"""Factory-level ``/memories/`` composite mounting for the desktop file store.

DoD #16 follow-up. The file-native memory backend
(:class:`~runtime_adapters.file.FileMemoryBackend`) is merged in #16, but the
runtime factory did not mount it: ``_composed_deep_backend`` only registered the
``/subagents/`` · ``/drafts/`` · ``/large_tool_results/`` · ``/workspace/``
routes, so the agent's ``read_file`` / ``write_file`` on ``/memories/`` fell to
the ephemeral ``StateBackend`` and never touched disk.

These tests prove the factory seam now:

* with the file store active, the memory routes produced by
  :class:`FileMemoryBackendFactory` (through the production
  :class:`ScopedMemoryBackendFactory`) are mounted on the composed backend, and
  a write to ``/memories/`` lands as canonical JSON under ``memory/`` and reads
  back through the composite;
* with the store NOT file-backed, ``_file_memory_routes`` yields ``None`` and no
  ``/memories/`` route is added — the composed backend stays byte-identical to
  before.
"""

from __future__ import annotations

import json

from agent_runtime.context.memory.backends import (
    MemoryRoutePlan,
    ScopedMemoryBackendFactory,
)
from agent_runtime.execution.factory import (
    _composed_deep_backend,
    _file_memory_routes,
)
from runtime_adapters.file import FileMemoryBackendFactory
from runtime_adapters.file._paths import FileStoreLayout


def _layout(tmp_path) -> FileStoreLayout:
    layout = FileStoreLayout(tmp_path / "store")
    layout.ensure_scaffold()
    return layout


def _file_backed_memory(layout, runtime_context_admin):
    """Mirror the worker wiring: file-backed ``ScopedMemoryBackendFactory``."""

    factory = ScopedMemoryBackendFactory(
        backend_builder=FileMemoryBackendFactory(layout)
    )
    return factory.create(runtime_context_admin)


class TestFileMemoryRoutesActive:
    """File store active → ``/memories/`` is mounted and persists to disk."""

    def test_file_store_yields_mountable_memory_routes(
        self, tmp_path, runtime_context_admin
    ) -> None:
        memory_backend = _file_backed_memory(_layout(tmp_path), runtime_context_admin)
        routes = _file_memory_routes(memory_backend)
        assert routes is not None
        # The FileMemoryBackendFactory owns the prefix set (user / org / agent).
        assert "/memories/" in routes
        assert "/policies/" in routes
        assert "/skills/" in routes

    def test_composed_backend_mounts_memory_route(
        self, tmp_path, runtime_context_admin
    ) -> None:
        memory_backend = _file_backed_memory(_layout(tmp_path), runtime_context_admin)
        composite = _composed_deep_backend(
            None, memory_routes=_file_memory_routes(memory_backend)
        )
        assert composite is not None
        assert "/memories/" in composite.routes

    async def test_write_through_composite_lands_on_disk(
        self, tmp_path, runtime_context_admin
    ) -> None:
        layout = _layout(tmp_path)
        memory_backend = _file_backed_memory(layout, runtime_context_admin)
        composite = _composed_deep_backend(
            None, memory_routes=_file_memory_routes(memory_backend)
        )

        write = await composite.awrite("/memories/todo.md", "Mount the memory route.")
        assert write.error is None

        # Persisted as canonical JSON under memory/ (not just in-process state).
        json_files = list((layout.root / "memory").rglob("*.json"))
        assert json_files, "a canonical memory JSON should have been written"
        payload = json.loads(json_files[0].read_text())
        assert payload["content"] == "Mount the memory route."
        assert payload["memory_path"] == "/memories/todo.md"

        # And it reads back through the same composite route.
        read = await composite.aread("/memories/todo.md")
        assert read.error is None
        assert read.file_data["content"] == "Mount the memory route."


class TestFileMemoryRoutesInactive:
    """Store not file-backed → no ``/memories/`` route, no disk writes."""

    def test_route_plan_is_not_mountable(self, runtime_context_admin) -> None:
        # No backend_builder → ScopedMemoryBackendFactory returns a route plan.
        memory_backend = ScopedMemoryBackendFactory().create(runtime_context_admin)
        assert isinstance(memory_backend, MemoryRoutePlan)
        assert _file_memory_routes(memory_backend) is None

    def test_sentinel_and_none_are_not_mountable(self) -> None:
        # A test fake's sentinel (a string) and ``None`` never mount memory.
        assert _file_memory_routes("memory") is None
        assert _file_memory_routes(None) is None
        assert _file_memory_routes({}) is None

    def test_composed_backend_omits_memory_without_file_store(
        self, runtime_context_admin
    ) -> None:
        memory_backend = ScopedMemoryBackendFactory().create(runtime_context_admin)
        # No Atlas backends and no file memory routes → no composite at all, so
        # every path (including ``/memories/``) stays on the StateBackend default.
        composite = _composed_deep_backend(
            None, memory_routes=_file_memory_routes(memory_backend)
        )
        assert composite is None
