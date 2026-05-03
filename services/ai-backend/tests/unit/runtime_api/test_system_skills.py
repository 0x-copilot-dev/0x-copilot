"""`/internal/v1/skills/system` projects filesystem skills into wire shape."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from agent_runtime.api.service import RuntimeApiService
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.system_skills import SystemSkillsProjector


class TestSystemSkillsEndpoint:
    """The runtime exposes its built-in skills so the facade can aggregate them."""

    def _client(self) -> TestClient:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_MAX_PARALLEL_TASKS": "1",
            }
        )
        service = RuntimeApiService(
            persistence=store, event_store=store, queue=store, settings=settings
        )
        return TestClient(RuntimeApiAppFactory.create_app(service))

    def test_returns_search_subagent_logs_in_settings_ui_shape(self) -> None:
        """A real filesystem skill should round-trip into a settings-UI payload.

        Asserts: `search-subagent-logs` appears, `source_type=system`, the SKILL.md
        body is in `markdown`, and the `enabled` toggle is forced to true so the
        UI cannot mistake an unset flag for "disabled".
        """

        response = self._client().get("/internal/v1/skills/system")
        assert response.status_code == 200
        body = response.json()
        assert "skills" in body
        by_name = {skill["name"]: skill for skill in body["skills"]}
        assert "search-subagent-logs" in by_name, (
            f"expected search-subagent-logs in {sorted(by_name)}"
        )

        skill = by_name["search-subagent-logs"]
        assert skill["skill_id"] == "system:search-subagent-logs"
        assert skill["source_type"] == "system"
        assert skill["enabled"] is True
        assert skill["version"] == 1
        assert "/subagents/" in skill["description"].lower()
        # Allowed tools must be the read-only filesystem ops, never write/edit —
        # this skill is just permission to inspect, not to mutate /subagents/.
        assert "ls" in skill["allowed_tools"]
        assert "read_file" in skill["allowed_tools"]
        assert "write_file" not in skill["allowed_tools"]
        assert "edit" not in skill["allowed_tools"]
        # Markdown body must be the actual SKILL.md content so the settings UI
        # can render the read-only viewer without a second fetch.
        assert skill["markdown"].lstrip().startswith("---")
        assert "search-subagent-logs" in skill["markdown"]


class TestSystemSkillsProjector:
    """Direct unit coverage for the projector — independent of FastAPI plumbing."""

    def test_empty_directory_returns_empty_list(self, tmp_path: Path) -> None:
        """Missing/empty skills directory must not crash the listing endpoint —
        there's no requirement that every deployment ships built-in skills."""

        projector = SystemSkillsProjector(root=tmp_path / "does-not-exist")
        result = projector.list_skills()
        assert result.skills == ()

    def test_projects_synthetic_skill_to_full_response_shape(
        self, tmp_path: Path
    ) -> None:
        """A SKILL.md with only minimal frontmatter still produces a valid
        response — no required field should be left unfilled."""

        skill_dir = tmp_path / "demo-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: demo-skill\n"
            "description: A test skill description.\n"
            "allowed_tools: [ls]\n"
            "---\n"
            "# Demo Skill\n",
            encoding="utf-8",
        )
        result = SystemSkillsProjector(root=tmp_path).list_skills()
        assert len(result.skills) == 1
        skill = result.skills[0]
        assert skill.name == "demo-skill"
        assert skill.skill_id == "system:demo-skill"
        assert skill.source_type == "system"
        assert skill.virtual_path == "/skills/system/demo-skill/SKILL.md"
        assert skill.allowed_tools == ("ls",)
        assert skill.markdown.startswith("---")
        # Both timestamps come from the file's mtime; without that, comparisons
        # against "now" should still hold.
        assert isinstance(skill.created_at, datetime)
        assert skill.created_at.tzinfo is timezone.utc
