from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_runtime.skills.constants import Keys


class MissingToolRegistryMethod:
    pass


@dataclass
class CapturingAgentBuilder:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return {"agent": "fake"}


@dataclass
class FakeDeepAgentsModule:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create_deep_agent(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return {"agent": "fake"}


class SkillsRuntimeFactoryTestMixin:
    class Paths:
        SKILLS = Keys.DeepAgents.SKILLS

    def create_builder(self) -> CapturingAgentBuilder:
        return CapturingAgentBuilder()

    def expected_skill_directories(self) -> tuple[str, ...]:
        return (str(Path(self.Paths.SKILLS).resolve(strict=False)),)
