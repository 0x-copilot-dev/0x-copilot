"""File-persisted skills + subagent definitions, and the worker wiring seam.

DoD #16 wiring #3. Skills are written as ``skills/<name>/SKILL.md`` and loaded by
the standard :class:`SkillSourceRegistry`; subagent definitions are written as
``subagent_defs/<name>.json`` and loaded by the standard
:class:`DynamicSubagentCatalog`. Also asserts the
:class:`DefaultRuntimeDependenciesFactory` seam: file-store sources are added
only when the store is active, and the dependency graph is byte-identical when
it is not.
"""

from __future__ import annotations

from agent_runtime.capabilities.skills.sources import (
    SkillSourceConfig,
    SkillSourceRegistry,
)
from agent_runtime.delegation.subagents.contracts import SubagentDefinition
from agent_runtime.delegation.subagents.definitions import DynamicSubagentCatalog
from runtime_adapters.file import (
    FileSkillsStore,
    FileSubagentDefinitionProvider,
    FileSubagentDefinitionStore,
)
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.agent_state_store import FileAgentStateWiring
from runtime_worker.dependencies import (
    BUILTIN_SKILLS_ROOT,
    DefaultRuntimeDependenciesFactory,
    EmptySubagentCatalog,
)

_SKILL_MD = """---
name: launch-helper
description: Helps draft launch checklists and release notes for the team.
---

# Launch Helper

Draft a checklist covering QA, comms, and rollback.
"""


def _layout(tmp_path) -> FileStoreLayout:
    layout = FileStoreLayout(tmp_path / "store")
    layout.ensure_scaffold()
    return layout


class TestFileSkillsStore:
    def test_written_skill_is_discovered_by_registry(self, tmp_path) -> None:
        layout = _layout(tmp_path)
        store = FileSkillsStore(layout)
        store.write_skill(name="launch-helper", markdown=_SKILL_MD)

        config = SkillSourceConfig(sources=({"path": str(store.root)},))
        discovered = SkillSourceRegistry.discover_configured_skills(config)
        names = {skill.manifest.name for skill in discovered}
        assert "launch-helper" in names

    def test_asset_path_traversal_is_rejected(self, tmp_path) -> None:
        store = FileSkillsStore(_layout(tmp_path))
        store.write_skill(name="launch-helper", markdown=_SKILL_MD)
        import pytest

        with pytest.raises(ValueError):
            store.write_asset(
                skill_name="launch-helper",
                relative_path="../escape.txt",
                data="nope",
            )


class TestFileSubagentDefinitionStore:
    def _definition(self) -> SubagentDefinition:
        return SubagentDefinition(
            name="researcher",
            description="Researches topics thoroughly on behalf of the orchestrator.",
            graph_id="researcher_graph",
        )

    def test_written_definition_loads_through_catalog(
        self, tmp_path, runtime_context_admin
    ) -> None:
        layout = _layout(tmp_path)
        store = FileSubagentDefinitionStore(layout)
        store.write_definition(self._definition())

        catalog = DynamicSubagentCatalog(
            providers=(FileSubagentDefinitionProvider(layout),)
        )
        definitions = catalog.list_available_subagents(runtime_context_admin)
        assert {d.name for d in definitions} == {"researcher"}

    def test_definition_json_is_inspectable(self, tmp_path) -> None:
        import json

        layout = _layout(tmp_path)
        store = FileSubagentDefinitionStore(layout)
        store.write_definition(self._definition())
        files = list((layout.root / "subagent_defs").glob("*.json"))
        assert len(files) == 1
        payload = json.loads(files[0].read_text())
        assert payload["name"] == "researcher"
        assert payload["graph_id"] == "researcher_graph"

    def test_empty_store_lists_nothing(self, tmp_path) -> None:
        provider = FileSubagentDefinitionProvider(_layout(tmp_path))
        assert provider.list_subagent_definitions() == ()


class TestDependenciesWiringSeam:
    """The worker dependency factory only adds file sources when gated."""

    def _factory(self) -> DefaultRuntimeDependenciesFactory:
        from agent_runtime.settings import RuntimeSettings

        return DefaultRuntimeDependenciesFactory(settings=RuntimeSettings.load())

    def test_skills_config_unchanged_without_file_store(self) -> None:
        config = self._factory()._skill_source_config(None)
        source_paths = {str(source.path) for source in config.sources}
        # Only the built-in wheel skills (when present); no file-store root.
        if BUILTIN_SKILLS_ROOT.is_dir():
            assert str(BUILTIN_SKILLS_ROOT.resolve()) in source_paths
        assert all("subagent_defs" not in path for path in source_paths)

    def test_skills_config_adds_file_store_root_when_active(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "file")
        monkeypatch.setenv("RUNTIME_FILE_STORE_ROOT", str(tmp_path / "store"))
        wiring = FileAgentStateWiring()
        config = self._factory()._skill_source_config(wiring)
        source_paths = {str(source.path) for source in config.sources}
        assert any(path.endswith("/skills") for path in source_paths)

    def test_subagent_catalog_empty_without_file_store(self) -> None:
        catalog = DefaultRuntimeDependenciesFactory._subagent_catalog(None)
        assert isinstance(catalog, EmptySubagentCatalog)

    def test_subagent_catalog_dynamic_when_active(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "file")
        monkeypatch.setenv("RUNTIME_FILE_STORE_ROOT", str(tmp_path / "store"))
        wiring = FileAgentStateWiring()
        catalog = DefaultRuntimeDependenciesFactory._subagent_catalog(wiring)
        assert isinstance(catalog, DynamicSubagentCatalog)

    def test_memory_factory_plain_without_file_store(self) -> None:
        from agent_runtime.context.memory.backends import ScopedMemoryBackendFactory

        factory = DefaultRuntimeDependenciesFactory._memory_backend_factory(None)
        assert isinstance(factory, ScopedMemoryBackendFactory)
        assert factory.backend_builder is None

    def test_memory_factory_file_backed_when_active(
        self, tmp_path, monkeypatch
    ) -> None:
        from agent_runtime.context.memory.backends import ScopedMemoryBackendFactory
        from runtime_adapters.file import FileMemoryBackendFactory

        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "file")
        monkeypatch.setenv("RUNTIME_FILE_STORE_ROOT", str(tmp_path / "store"))
        wiring = FileAgentStateWiring()
        factory = DefaultRuntimeDependenciesFactory._memory_backend_factory(wiring)
        assert isinstance(factory, ScopedMemoryBackendFactory)
        assert isinstance(factory.backend_builder, FileMemoryBackendFactory)
