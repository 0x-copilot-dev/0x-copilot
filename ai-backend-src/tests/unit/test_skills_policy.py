from __future__ import annotations

from pathlib import Path

from enterprise_search_ai.skills.constants import Keys
from enterprise_search_ai.skills.manifest import SkillManifest
from enterprise_search_ai.skills.policy import SkillAccessEvaluator, SkillAccessPolicy
from enterprise_search_ai.skills.sources import (
    ConfiguredSkill,
    SkillSource,
    SkillSourceScope,
)


class SkillPolicyTestMixin:
    class Names:
        DOC_SEARCH = "doc_search"
        RESEARCH_PLAN = "research-plan"

    class Paths:
        RESEARCH_PLAN = "research-plan"
        SKILLS = Keys.DeepAgents.SKILLS

    class Descriptions:
        RESEARCH_PLAN = "Use when creating source-backed research plans."

    def configured_skill(
        self,
        tmp_path: Path,
        *,
        allowed_tools: set[str] | None = None,
    ) -> ConfiguredSkill:
        source = SkillSource(
            path=tmp_path / self.Paths.SKILLS,
            scope={SkillSourceScope.SHARED},
        )
        return ConfiguredSkill(
            manifest=SkillManifest(
                name=self.Names.RESEARCH_PLAN,
                description=self.Descriptions.RESEARCH_PLAN,
                allowed_tools=allowed_tools or set(),
            ),
            source=source,
            skill_directory=tmp_path / self.Paths.SKILLS / self.Paths.RESEARCH_PLAN,
        )

    def main_agent_policy(self, skill: ConfiguredSkill) -> SkillAccessPolicy:
        return SkillAccessPolicy.for_main_agent(
            allowed_sources={skill.source.path},
            allowed_tools={self.Names.DOC_SEARCH},
        )


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
        assert SkillAccessEvaluator.is_skill_allowed(missing_tool_policy, skill) is False
