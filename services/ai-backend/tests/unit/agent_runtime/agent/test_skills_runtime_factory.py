from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.agent.contracts import AgentRuntimeContext, RuntimeDependencies
from agent_runtime.agent.factory import RuntimeHarness, create_agent_runtime
from agent_runtime.capabilities.skills.virtual import VirtualSkillCard
from agent_runtime.skills.constants import Keys
from tests.unit.agent_runtime.agent.helpers import SkillsRuntimeFactoryTestMixin


@dataclass
class FakeVirtualSkillRegistry:
    def list_available_skills(self, _context: AgentRuntimeContext) -> tuple[VirtualSkillCard, ...]:
        return (
            VirtualSkillCard(
                skill_id="skill_123",
                name="launch_risk_review",
                display_name="Launch Risk Review",
                description="Review launch risks.",
                virtual_path="/skills/org/org_456/user/user_123/launch_risk_review/SKILL.md",
                scope="user",
                source_type="user",
                version=1,
            ),
        )

    def load_skill_by_name(self, _name: str) -> object:
        return object()


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

    def test_factory_injects_virtual_skill_cards_and_loader_tool(
        self,
        runtime_context_admin: AgentRuntimeContext,
        fake_dependencies: RuntimeDependencies,
    ) -> None:
        builder = self.create_builder()
        dependencies = fake_dependencies.model_copy(
            update={"skill_registry": FakeVirtualSkillRegistry()}
        )

        harness = create_agent_runtime(
            context=runtime_context_admin,
            dependencies=dependencies,
            agent_builder=builder,
        )

        tool_names = {getattr(tool, "name", "") for tool in builder.calls[0]["tools"]}
        assert "load_skill" in tool_names
        assert "launch_risk_review" in builder.calls[0]["instructions"]
        assert "virtual registry" in builder.calls[0]["instructions"]
        assert len(harness.skill_cards) == 1
