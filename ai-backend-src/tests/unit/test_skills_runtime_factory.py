from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from enterprise_search_ai.agent.contracts import AgentRuntimeContext, RuntimeDependencies
from enterprise_search_ai.agent.factory import RuntimeHarness, create_agent_runtime
from enterprise_search_ai.skills.constants import Keys


class SkillsRuntimeFactoryTestMixin:
    class Paths:
        SKILLS = Keys.DeepAgents.SKILLS

    @dataclass
    class CapturingAgentBuilder:
        calls: list[dict[str, Any]] = field(default_factory=list)

        def __call__(self, **kwargs: Any) -> object:
            self.calls.append(kwargs)
            return {"agent": "fake"}

    def create_builder(self) -> CapturingAgentBuilder:
        return self.CapturingAgentBuilder()

    def expected_skill_directories(self) -> tuple[str, ...]:
        return (str(Path(self.Paths.SKILLS).resolve(strict=False)),)


class TestSkillsRuntimeFactory(SkillsRuntimeFactoryTestMixin):
    def test_factory_passes_skill_directories_to_agent_builder(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        builder = self.create_builder()

        harness = create_agent_runtime(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            agent_builder=builder,
        )

        assert isinstance(harness, RuntimeHarness)
        assert harness.skill_directories == self.expected_skill_directories()
        assert builder.calls[0][Keys.DeepAgents.SKILLS] == (
            self.expected_skill_directories()
        )
