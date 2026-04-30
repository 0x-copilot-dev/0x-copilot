"""Access policy for main-agent and subagent skill visibility."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, field_validator

from agent_runtime.skills.constants import Keys, Messages, Patterns
from agent_runtime.skills.sources import (
    ConfiguredSkill,
    SkillContract,
    SkillSource,
    SkillSourceScope,
)


class SkillAgentType(StrEnum):
    """Runtime agent classes that can receive configured skills."""

    MAIN_AGENT = "main_agent"
    SUBAGENT = "subagent"


class SkillAccessPolicy(SkillContract):
    """Least-privilege policy applied before skills are visible to an agent."""

    agent_type: SkillAgentType
    allowed_sources: frozenset[str] = Field(default_factory=frozenset)
    denied_skill_names: frozenset[str] = Field(default_factory=frozenset)
    allowed_tools: frozenset[str] = Field(default_factory=frozenset)

    @field_validator(Keys.Fields.ALLOWED_SOURCES, mode=Keys.Pydantic.BEFORE)
    @classmethod
    def _normalize_allowed_sources(cls, value: object) -> frozenset[str]:
        return frozenset(
            SkillPolicyNormalizer.normalize_source_path(item)
            for item in SkillPolicyNormalizer.coerce_iterable(value)
        )

    @field_validator(
        Keys.Fields.DENIED_SKILL_NAMES,
        Keys.Fields.ALLOWED_TOOLS,
        mode=Keys.Pydantic.BEFORE,
    )
    @classmethod
    def _normalize_slug_set(cls, value: object) -> frozenset[str]:
        return frozenset(
            SkillPolicyNormalizer.normalize_slug(item)
            for item in SkillPolicyNormalizer.coerce_iterable(value)
        )

    @classmethod
    def for_main_agent(
        cls,
        *,
        allowed_sources: object,
        denied_skill_names: object = (),
        allowed_tools: object = (),
    ) -> "SkillAccessPolicy":
        """Create an explicit main-agent policy for configured skill sources."""

        return cls(
            agent_type=SkillAgentType.MAIN_AGENT,
            allowed_sources=allowed_sources,
            denied_skill_names=denied_skill_names,
            allowed_tools=allowed_tools,
        )

    @classmethod
    def for_subagent(
        cls,
        *,
        allowed_sources: object = (),
        denied_skill_names: object = (),
        allowed_tools: object = (),
    ) -> "SkillAccessPolicy":
        """Create a subagent policy; by default custom subagents receive no skills."""

        return cls(
            agent_type=SkillAgentType.SUBAGENT,
            allowed_sources=allowed_sources,
            denied_skill_names=denied_skill_names,
            allowed_tools=allowed_tools,
        )

    def is_skill_allowed(self, skill: ConfiguredSkill) -> bool:
        """Return whether a discovered skill may be exposed to this agent policy."""

        return SkillAccessEvaluator.is_skill_allowed(self, skill)

    def filter_skill_directories(
        self,
        skills: tuple[ConfiguredSkill, ...],
    ) -> tuple[str, ...]:
        """Return distinct source directories that contain policy-visible skills."""

        return SkillAccessEvaluator.filter_skill_directories(self, skills)


class SkillAccessEvaluator:
    """Evaluator for skills access policy decisions."""

    @classmethod
    def is_skill_allowed(
        cls,
        policy: SkillAccessPolicy,
        skill: ConfiguredSkill,
    ) -> bool:
        if not cls.is_source_allowed(policy, skill.source):
            return False
        if skill.manifest.name in policy.denied_skill_names:
            return False
        return skill.manifest.allowed_tools.issubset(policy.allowed_tools)

    @classmethod
    def is_source_allowed(cls, policy: SkillAccessPolicy, source: SkillSource) -> bool:
        if str(source.path) not in policy.allowed_sources:
            return False

        required_scope = SkillSourceScope(policy.agent_type.value)
        return SkillSourceScope.SHARED in source.scope or required_scope in source.scope

    @classmethod
    def filter_skill_directories(
        cls,
        policy: SkillAccessPolicy,
        skills: tuple[ConfiguredSkill, ...],
    ) -> tuple[str, ...]:
        directories: list[str] = []
        seen: set[str] = set()
        for skill in skills:
            if not cls.is_skill_allowed(policy, skill):
                continue
            source_path = str(skill.source.path)
            if source_path in seen:
                continue
            seen.add(source_path)
            directories.append(source_path)
        return tuple(directories)


class SkillPolicyNormalizer:
    """Normalizers for skills access policy contracts."""

    @classmethod
    def normalize_source_path(cls, value: object) -> str:
        if not isinstance(value, str | Path):
            raise ValueError(Messages.Validation.SOURCE_PATH_STRING)
        return str(Path(value).expanduser().resolve(strict=False))

    @classmethod
    def normalize_slug(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError(Messages.Validation.STRING_POLICY_SLUG)
        normalized = value.strip().lower()
        if not normalized or not Patterns.SLUG.fullmatch(normalized):
            raise ValueError(Messages.Validation.STABLE_POLICY_SLUG)
        return normalized

    @classmethod
    def coerce_iterable(cls, value: object) -> tuple[object, ...]:
        if value is None:
            return ()
        if isinstance(value, str | Path):
            return (value,)
        try:
            return tuple(value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError(Messages.Validation.POLICY_VALUES_ITERABLE) from exc
