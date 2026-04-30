from __future__ import annotations

from agent_runtime.agent.contracts import AgentRuntimeContext, RuntimeDependencies
from agent_runtime.agent.factory import RuntimeHarness, create_agent_runtime
from agent_runtime.skills.constants import Keys
from tests.unit.agent_runtime.agent.helpers import SkillsRuntimeFactoryTestMixin


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
