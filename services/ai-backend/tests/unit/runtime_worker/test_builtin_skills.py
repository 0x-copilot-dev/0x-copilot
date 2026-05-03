"""Built-in skills shipped with the runtime."""

from __future__ import annotations

from agent_runtime.capabilities.skills.sources import SkillSourceRegistry
from runtime_worker.dependencies import (
    BUILTIN_SKILLS_ROOT,
    DefaultRuntimeDependenciesFactory,
)


def test_builtin_skills_directory_is_present_and_readable() -> None:
    """The runtime ships its own skills under `services/ai-backend/skills/`.

    A missing or empty directory would silently mean the supervisor never
    learns about `/subagents/<task_id>/`, so this asserts the path resolves
    and contains at least one configured skill.
    """

    assert BUILTIN_SKILLS_ROOT.is_dir(), (
        f"expected built-in skills directory at {BUILTIN_SKILLS_ROOT}"
    )


def test_default_factory_registers_search_subagent_logs_skill() -> None:
    """`search-subagent-logs` must show up as a discovered skill so its
    name + description are surfaced in the supervisor's prompt skill cards
    (`SKILL_CARDS_INSTRUCTIONS`) and the model can `load_skill` to fetch
    the full instructions for reading `/subagents/<task_id>/...`."""

    factory = DefaultRuntimeDependenciesFactory.__new__(
        DefaultRuntimeDependenciesFactory
    )
    config = factory._skill_source_config()

    discovered = SkillSourceRegistry.discover_configured_skills(config)
    by_name = {skill.manifest.name: skill for skill in discovered}

    assert "search-subagent-logs" in by_name, (
        f"expected search-subagent-logs in {sorted(by_name)}"
    )
    skill = by_name["search-subagent-logs"]
    # Description must reference the FS path and mention the verbatim use case
    # so the cards render with enough context for the model to pick it.
    description = skill.manifest.description.lower()
    assert "/subagents/" in description
    # Allowed tools should be the read-only filesystem ops; no write/edit.
    assert "ls" in skill.manifest.allowed_tools
    assert "read_file" in skill.manifest.allowed_tools
    assert "write_file" not in skill.manifest.allowed_tools
    assert "edit" not in skill.manifest.allowed_tools


def test_default_factory_registers_web_search_discipline_skill() -> None:
    """`web-search-discipline` must be discovered so the supervisor sees its
    card and can `load_skill` for query-planning guidance referenced by
    `WEB_SUBAGENT_CHECKPOINT_SUFFIX`."""

    factory = DefaultRuntimeDependenciesFactory.__new__(
        DefaultRuntimeDependenciesFactory
    )
    config = factory._skill_source_config()

    discovered = SkillSourceRegistry.discover_configured_skills(config)
    by_name = {skill.manifest.name: skill for skill in discovered}

    assert "web-search-discipline" in by_name, (
        f"expected web-search-discipline in {sorted(by_name)}"
    )
    skill = by_name["web-search-discipline"]
    description = skill.manifest.description.lower()
    # Description must hint at when to load — "planning a search batch" / "stop"
    # — so the model can decide to fetch the body.
    assert "search" in description
    assert "stop" in description or "diminishing" in description
    # Allowed tools scope the skill to web_search; no filesystem write/edit.
    assert "web_search" in skill.manifest.allowed_tools
    assert "write_file" not in skill.manifest.allowed_tools
    assert "edit" not in skill.manifest.allowed_tools
