from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.capabilities.skills.constants import Keys
from agent_runtime.capabilities.skills.manifest import (
    SkillErrorCode,
    SkillManifest,
    SkillManifestError,
    SkillManifestParser,
    SkillManifestReader,
)
from agent_runtime.capabilities.skills.policy import SkillAccessPolicy
from agent_runtime.capabilities.skills.sources import (
    ConfiguredSkill,
    SkillSource,
    SkillSourceConfig,
    SkillSourceRegistry,
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


class SkillManifestTestMixin:
    class Samples:
        VALID = """---
name: Research-Plan
description: Use when creating source-backed executive research plans.
license: MIT
compatibility:
  - deepagents
allowed_tools: [doc_search]
metadata:
  owner: ai-platform
---
# Research Plan
"""
        MISSING_DESCRIPTION = """---
name: research-plan
---
# Research Plan
"""
        MALFORMED = """---
name research-plan
---
# Research Plan
"""
        UNSAFE_ASSET = """---
name: unsafe-skill
description: Use when testing unsafe asset references.
---
Read [outside](../secret.txt).
"""
        MISSING_ASSET = """---
name: missing-asset-skill
description: Use when testing missing asset references.
---
Read [template](assets/template.md).
"""

    class Expected:
        NAME = "research-plan"
        DESCRIPTION = "Use when creating source-backed executive research plans."
        LICENSE = "MIT"
        COMPATIBILITY = frozenset({"deepagents"})
        ALLOWED_TOOLS = frozenset({"doc_search"})
        METADATA = {"owner": "ai-platform"}

    def parse(self, markdown: str):
        return SkillManifestParser.parse(markdown)

    def read(self, skill_dir: Path):
        return SkillManifestReader.read(skill_dir)

    def write_skill(self, skill_dir: Path, markdown: str) -> None:
        skill_dir.mkdir()
        (skill_dir / Keys.Files.SKILL_MD).write_text(
            markdown,
            encoding=Keys.Encoding.UTF_8,
        )

    def assert_skill_error(
        self,
        exc_info: pytest.ExceptionInfo[SkillManifestError],
        code: SkillErrorCode,
    ) -> None:
        assert exc_info.value.code == code


class SkillSourcesTestMixin:
    class Names:
        RESEARCH_PLAN = "research-plan"

    class Paths:
        FIRST = "first"
        HIGH = "high"
        LOW = "low"
        MISSING = "does-not-exist"
        SECOND = "second"
        SKILLS = Keys.DeepAgents.SKILLS

    class Descriptions:
        HIGH_PRECEDENCE = "Use when creating the high precedence research plan."
        LOW_PRECEDENCE = "Use when creating the low precedence research plan."

    def discover(self, config: SkillSourceConfig):
        return SkillSourceRegistry.discover_configured_skills(config)

    def directories_for_deep_agent(self, config: SkillSourceConfig) -> tuple[str, ...]:
        return SkillSourceRegistry.skill_directories_for_deep_agent(config)

    def write_skill(self, skill_dir: Path, *, name: str, description: str) -> None:
        skill_dir.mkdir(parents=True)
        (skill_dir / Keys.Files.SKILL_MD).write_text(
            f"""---
name: {name}
description: {description}
---
# {name}
""",
            encoding=Keys.Encoding.UTF_8,
        )
