from __future__ import annotations

import asyncio

from enterprise_service_contracts.headers import ORG_HEADER, SERVICE_TOKEN_HEADER, USER_HEADER

from agent_runtime.capabilities.skills.middleware import LoadSkillTool
from agent_runtime.capabilities.skills.virtual import (
    BackendSkillServiceAuth,
    VirtualSkillBundle,
    VirtualSkillCard,
    VirtualSkillRegistry,
)
from agent_runtime.execution.contracts import AgentRuntimeContext


class FakeSkillProvider:
    list_calls = 0
    load_calls = 0

    def list_skill_cards(self) -> tuple[VirtualSkillCard, ...]:
        self.list_calls += 1
        return (
            VirtualSkillCard(
                skill_id="skill_123",
                name="incident_review",
                display_name="Incident Review",
                description="Review incidents.",
                virtual_path="/skills/org/org_123/user/user_123/incident_review/SKILL.md",
                scope="user",
                source_type="user",
                version=1,
            ),
        )

    def load_skill_by_name(self, name: str) -> VirtualSkillBundle:
        self.load_calls += 1
        return VirtualSkillBundle(
            skill_id="skill_123",
            name=name,
            display_name="Incident Review",
            description="Review incidents.",
            markdown="---\nname: incident-review\ndescription: Review incidents.\n---\n# Body",
            virtual_path="/skills/org/org_123/user/user_123/incident_review/SKILL.md",
            version=1,
        )


def test_load_skill_tool_returns_markdown_from_virtual_registry() -> None:
    provider = FakeSkillProvider()
    registry = VirtualSkillRegistry(providers=(provider,))
    tool = LoadSkillTool(registry=registry)

    first = asyncio.run(tool.ainvoke("incident_review"))
    second = asyncio.run(tool.ainvoke({"skill_name": "incident_review"}))

    assert first["ok"] is True
    assert first["markdown"].startswith("---")
    assert second["markdown"] == first["markdown"]
    assert provider.load_calls == 1


def test_backend_skill_service_auth_includes_trusted_scope_headers(
    monkeypatch,
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "service-token")

    headers = BackendSkillServiceAuth.headers(runtime_context_admin)

    assert headers[SERVICE_TOKEN_HEADER] == "service-token"
    assert headers[ORG_HEADER] == runtime_context_admin.org_id
    assert headers[USER_HEADER] == runtime_context_admin.user_id
