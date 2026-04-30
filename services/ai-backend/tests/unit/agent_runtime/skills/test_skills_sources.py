from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.capabilities.skills.manifest import SkillErrorCode, SkillManifestError
from agent_runtime.capabilities.skills.sources import (
    SkillSource,
    SkillSourceConfig,
    SkillSourceScope,
)
from tests.unit.agent_runtime.skills.helpers import SkillSourcesTestMixin


class TestSkillSources(SkillSourcesTestMixin):
    def test_discover_configured_skills_applies_source_precedence(
        self,
        tmp_path: Path,
    ) -> None:
        low_precedence = tmp_path / self.Paths.LOW
        high_precedence = tmp_path / self.Paths.HIGH
        self.write_skill(
            low_precedence / self.Names.RESEARCH_PLAN,
            name=self.Names.RESEARCH_PLAN,
            description=self.Descriptions.LOW_PRECEDENCE,
        )
        self.write_skill(
            high_precedence / self.Names.RESEARCH_PLAN,
            name=self.Names.RESEARCH_PLAN,
            description=self.Descriptions.HIGH_PRECEDENCE,
        )

        config = SkillSourceConfig(
            sources=(
                SkillSource(path=high_precedence, precedence=20),
                SkillSource(path=low_precedence, precedence=10),
            )
        )

        skills = self.discover(config)

        assert len(skills) == 1
        assert skills[0].manifest.name == self.Names.RESEARCH_PLAN
        assert skills[0].manifest.description == self.Descriptions.HIGH_PRECEDENCE
        assert skills[0].source.path == high_precedence.resolve()

    def test_skill_directories_for_deep_agent_orders_sources_for_last_wins(
        self,
        tmp_path: Path,
    ) -> None:
        first = tmp_path / self.Paths.FIRST
        second = tmp_path / self.Paths.SECOND

        config = SkillSourceConfig(
            sources=(
                SkillSource(path=second, precedence=20, scope={SkillSourceScope.SUBAGENT}),
                SkillSource(path=first, precedence=10, scope={SkillSourceScope.MAIN_AGENT}),
            )
        )

        assert self.directories_for_deep_agent(config) == (
            str(first.resolve()),
            str(second.resolve()),
        )

    def test_discover_configured_skills_rejects_unreadable_source(
        self,
        tmp_path: Path,
    ) -> None:
        config = SkillSourceConfig(
            sources=(
                SkillSource(path=tmp_path / self.Paths.MISSING, precedence=10),
            )
        )

        with pytest.raises(SkillManifestError) as exc_info:
            self.discover(config)

        assert exc_info.value.code == SkillErrorCode.SOURCE_NOT_READABLE

    def test_disabled_skill_source_config_exposes_no_directories(
        self,
        tmp_path: Path,
    ) -> None:
        config = SkillSourceConfig(
            roots=(str(tmp_path / self.Paths.SKILLS),),
            enabled=False,
        )

        assert self.directories_for_deep_agent(config) == ()
        assert self.discover(config) == ()
