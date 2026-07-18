"""File-persisted agent/user memory: round-trip, rebuild, gating, secret exclusion.

DoD #16 wiring #2. Memory that previously lived only in Deep Agents' ephemeral
``StateBackend`` is persisted as canonical ``memory/<scope>/<key>.json`` plus a
disposable human ``.md`` view. Covers:

* round-trip through :class:`FileMemoryStore` and through a deepagents
  ``CompositeBackend`` mounting :class:`FileMemoryBackend` at ``/memories/``;
* the ``.md`` view is rebuildable from the canonical JSON;
* scope isolation (user / org / agent land in distinct directories);
* the env gate (:class:`FileAgentStoreGate` / :class:`FileAgentStateWiring`);
* the secret canary — credential-shaped metadata never reaches disk.
"""

from __future__ import annotations

import json

from agent_runtime.context.memory.backends import MemoryRoutePlan
from agent_runtime.context.memory.constants import Values
from agent_runtime.context.memory.contracts import MemoryScope
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.state import StateBackend
from runtime_adapters.file import (
    FileAgentStateWiring,
    FileAgentStoreGate,
    FileMemoryBackend,
    FileMemoryBackendFactory,
    FileMemoryStore,
)
from runtime_adapters.file._paths import FileStoreLayout

_SECRET_CANARY = "sk-CANARY-tYq9-DO-NOT-PERSIST"


def _layout(tmp_path) -> FileStoreLayout:
    layout = FileStoreLayout(tmp_path / "store")
    layout.ensure_scaffold()
    return layout


class TestFileMemoryStore:
    def test_round_trips_document_as_json_and_md(self, tmp_path) -> None:
        store = FileMemoryStore(_layout(tmp_path))
        scope = MemoryScope(
            scope_type=Values.ScopeType.USER,
            org_id="org1",
            user_id="user1",
            namespace=("org", "org1", "user", "user1"),
        )
        document = store.write(
            scope=scope,
            memory_path="/memories/preferences.md",
            content="User prefers concise answers.",
        )
        assert document.version == 1

        loaded = store.read(scope=scope, memory_path="/memories/preferences.md")
        assert loaded is not None
        assert loaded.content == "User prefers concise answers."
        assert loaded.memory_path == "/memories/preferences.md"

        # A second write increments the version.
        again = store.write(
            scope=scope,
            memory_path="/memories/preferences.md",
            content="User prefers concise answers with citations.",
        )
        assert again.version == 2

    def test_human_md_view_is_rebuildable_from_json(self, tmp_path) -> None:
        layout = _layout(tmp_path)
        store = FileMemoryStore(layout)
        scope = MemoryScope(
            scope_type=Values.ScopeType.USER,
            org_id="org1",
            user_id="user1",
            namespace=("org", "org1", "user", "user1"),
        )
        store.write(
            scope=scope,
            memory_path="/memories/note.md",
            content="Remember the launch date.",
        )
        # Find and delete every disposable .md view; JSON stays authoritative.
        memory_dir = layout.root / "memory"
        md_files = list(memory_dir.rglob("*.md"))
        assert md_files, "a human .md view should have been written"
        views_before = {p.read_text() for p in md_files}
        for view in md_files:
            view.unlink()

        rebuilt = store.rebuild_human_views(scope)
        assert rebuilt == 1
        views_after = {p.read_text() for p in memory_dir.rglob("*.md")}
        assert views_after == views_before
        assert any("Remember the launch date." in v for v in views_after)

    def test_scopes_are_isolated_on_disk(self, tmp_path) -> None:
        from agent_runtime.execution.contracts import AgentRuntimeContext

        store = FileMemoryStore(_layout(tmp_path))
        user_scope = MemoryScope(
            scope_type=Values.ScopeType.USER,
            org_id="org1",
            user_id="user1",
            namespace=("org", "org1", "user", "user1"),
        )
        org_scope = MemoryScope(
            scope_type=Values.ScopeType.ORGANIZATION,
            org_id="org1",
            namespace=("org", "org1", "policies"),
        )
        store.write(scope=user_scope, memory_path="/memories/a.md", content="mine")
        store.write(scope=org_scope, memory_path="/policies/a.md", content="ours")

        assert store.read(scope=user_scope, memory_path="/policies/a.md") is None
        assert store.read(scope=org_scope, memory_path="/memories/a.md") is None
        assert len(store.list_documents(user_scope)) == 1
        assert len(store.list_documents(org_scope)) == 1
        assert AgentRuntimeContext  # keep import local + referenced


class TestFileMemoryStoreSecretExclusion:
    def test_credential_metadata_is_redacted_before_disk(self, tmp_path) -> None:
        layout = _layout(tmp_path)
        store = FileMemoryStore(layout)
        scope = MemoryScope(
            scope_type=Values.ScopeType.USER,
            org_id="org1",
            user_id="user1",
            namespace=("org", "org1", "user", "user1"),
        )
        document = store.write(
            scope=scope,
            memory_path="/memories/creds.md",
            content="A harmless note.",
            metadata={"api_key": _SECRET_CANARY, "topic": "launch"},
        )
        # The credential-shaped key is redacted; the benign key survives.
        assert document.metadata["api_key"] == "[redacted]"
        assert document.metadata["topic"] == "launch"

        # Canary sweep: the secret value must appear nowhere in the store tree.
        for path in layout.root.rglob("*"):
            if path.is_file():
                assert _SECRET_CANARY not in path.read_text(
                    encoding="utf-8", errors="ignore"
                )


class TestFileMemoryBackendThroughComposite:
    """The orchestrator's ``write_file`` / ``read_file`` route to the file store."""

    def _plan_route(self, runtime_context_admin):
        plan = MemoryRoutePlan.for_context(runtime_context_admin)
        return plan.route_for_path("/memories/x.md")

    async def test_write_and_read_through_composite(
        self, tmp_path, runtime_context_admin
    ) -> None:
        layout = _layout(tmp_path)
        route = self._plan_route(runtime_context_admin)
        backend = FileMemoryBackend(store=FileMemoryStore(layout), route=route)
        composite = CompositeBackend(
            default=StateBackend(), routes={"/memories/": backend}
        )

        write = await composite.awrite("/memories/todo.md", "Ship the file store.")
        assert write.error is None

        read = await composite.aread("/memories/todo.md")
        assert read.error is None
        assert read.file_data["content"] == "Ship the file store."

        # Persisted to disk as canonical JSON (not just in-process state).
        json_files = list((layout.root / "memory").rglob("*.json"))
        assert json_files
        payload = json.loads(json_files[0].read_text())
        assert payload["content"] == "Ship the file store."
        assert payload["memory_path"] == "/memories/todo.md"

    async def test_edit_round_trips_through_composite(
        self, tmp_path, runtime_context_admin
    ) -> None:
        layout = _layout(tmp_path)
        route = self._plan_route(runtime_context_admin)
        backend = FileMemoryBackend(store=FileMemoryStore(layout), route=route)
        composite = CompositeBackend(
            default=StateBackend(), routes={"/memories/": backend}
        )
        await composite.awrite("/memories/plan.md", "step one; step two")

        edit = await composite.aedit(
            "/memories/plan.md", "step two", "step two; step three"
        )
        assert edit.error is None
        read = await composite.aread("/memories/plan.md")
        assert read.file_data["content"] == "step one; step two; step three"

    async def test_ls_lists_scope_documents(
        self, tmp_path, runtime_context_admin
    ) -> None:
        layout = _layout(tmp_path)
        route = self._plan_route(runtime_context_admin)
        backend = FileMemoryBackend(store=FileMemoryStore(layout), route=route)
        composite = CompositeBackend(
            default=StateBackend(), routes={"/memories/": backend}
        )
        await composite.awrite("/memories/one.md", "1")
        await composite.awrite("/memories/two.md", "2")

        listing = await composite.als("/memories/")
        paths = {entry["path"] for entry in (listing.entries or [])}
        assert "/memories/one.md" in paths
        assert "/memories/two.md" in paths


class TestFileMemoryGating:
    def test_gate_returns_none_without_env(self, monkeypatch) -> None:
        monkeypatch.delenv("RUNTIME_STORE_BACKEND", raising=False)
        monkeypatch.delenv("RUNTIME_FILE_STORE_ROOT", raising=False)
        assert FileAgentStoreGate.active_layout() is None
        wiring = FileAgentStateWiring()
        assert wiring.active is False
        assert wiring.memory_backend_builder() is None
        assert wiring.skills_root() is None
        assert wiring.subagent_definition_provider() is None

    def test_gate_activates_with_file_backend_env(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "file")
        monkeypatch.setenv("RUNTIME_FILE_STORE_ROOT", str(tmp_path / "store"))
        layout = FileAgentStoreGate.active_layout()
        assert layout is not None
        wiring = FileAgentStateWiring()
        assert wiring.active is True
        assert isinstance(wiring.memory_backend_builder(), FileMemoryBackendFactory)
        assert wiring.skills_root() is not None
        assert wiring.subagent_definition_provider() is not None

    def test_postgres_backend_env_does_not_activate(self, monkeypatch) -> None:
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "postgres")
        monkeypatch.setenv("RUNTIME_FILE_STORE_ROOT", "/unused")
        assert FileAgentStoreGate.active_layout() is None
