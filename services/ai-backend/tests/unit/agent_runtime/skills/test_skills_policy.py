from __future__ import annotations

from pathlib import Path

from agent_runtime.capabilities.skills.manifest import SkillManifest
from agent_runtime.capabilities.skills.policy import (
    SkillAccessEvaluator,
    SkillAccessPolicy,
)
from agent_runtime.capabilities.skills.sources import (
    ConfiguredSkill,
    SkillSource,
    SkillSourceScope,
)
from tests.unit.agent_runtime.skills.helpers import SkillPolicyTestMixin


class TestSkillPolicy(SkillPolicyTestMixin):
    def test_main_agent_policy_allows_configured_shared_source(
        self,
        tmp_path: Path,
    ) -> None:
        skill = self.configured_skill(
            tmp_path,
            allowed_tools={self.Names.DOC_SEARCH},
        )
        policy = self.main_agent_policy(skill)

        assert SkillAccessEvaluator.is_skill_allowed(policy, skill) is True
        assert SkillAccessEvaluator.filter_skill_directories(policy, (skill,)) == (
            str(skill.source.path),
        )

    def test_subagent_policy_does_not_inherit_skills_without_sources(
        self,
        tmp_path: Path,
    ) -> None:
        skill = self.configured_skill(tmp_path)
        policy = SkillAccessPolicy.for_subagent()

        assert SkillAccessEvaluator.is_skill_allowed(policy, skill) is False
        assert SkillAccessEvaluator.filter_skill_directories(policy, (skill,)) == ()

    def test_subagent_policy_rejects_main_agent_only_source(
        self,
        tmp_path: Path,
    ) -> None:
        source = SkillSource(
            path=tmp_path / self.Paths.SKILLS,
            scope={SkillSourceScope.MAIN_AGENT},
        )
        skill = ConfiguredSkill(
            manifest=SkillManifest(
                name=self.Names.RESEARCH_PLAN,
                description=self.Descriptions.RESEARCH_PLAN,
            ),
            source=source,
            skill_directory=tmp_path / self.Paths.SKILLS / self.Paths.RESEARCH_PLAN,
        )
        policy = SkillAccessPolicy.for_subagent(allowed_sources={source.path})

        assert SkillAccessEvaluator.is_skill_allowed(policy, skill) is False

    def test_skill_policy_enforces_denied_names_and_allowed_tools(
        self,
        tmp_path: Path,
    ) -> None:
        skill = self.configured_skill(
            tmp_path,
            allowed_tools={self.Names.DOC_SEARCH},
        )

        denied_policy = SkillAccessPolicy.for_main_agent(
            allowed_sources={skill.source.path},
            denied_skill_names={self.Names.RESEARCH_PLAN},
            allowed_tools={self.Names.DOC_SEARCH},
        )
        missing_tool_policy = SkillAccessPolicy.for_main_agent(
            allowed_sources={skill.source.path},
            allowed_tools=set(),
        )

        assert SkillAccessEvaluator.is_skill_allowed(denied_policy, skill) is False
        assert (
            SkillAccessEvaluator.is_skill_allowed(missing_tool_policy, skill) is False
        )
