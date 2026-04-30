from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_runtime.capabilities.skills.constants import Keys
from agent_runtime.execution.deep_agent_builder import DeepAgentBuildRequest


class MissingToolRegistryMethod:
    pass


@dataclass
class CapturingAgentBuilder:
    calls: list[DeepAgentBuildRequest] = field(default_factory=list)

    def __call__(self, request: DeepAgentBuildRequest) -> object:
        self.calls.append(request)
        return {"agent": "fake"}


@dataclass
class FakeDeepAgentsModule:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create_deep_agent(self, *, system_prompt: str, **kwargs: Any) -> object:
        self.calls.append({"system_prompt": system_prompt, **kwargs})
        return {"agent": "fake"}


class SkillsRuntimeFactoryTestMixin:
    class Paths:
        SKILLS = Keys.DeepAgents.SKILLS

    def create_builder(self) -> CapturingAgentBuilder:
        return CapturingAgentBuilder()

    def expected_skill_directories(self) -> tuple[str, ...]:
        return (str(Path(self.Paths.SKILLS).resolve(strict=False)),)


class StreamingObservabilityTestMixin:
    class Values:
        TRACE_ID = "trace_123"
        TASK_ID = "task_123"
        CALL_ID = "call_123"
        TOOL_NAME = "doc_search"
        SUBAGENT_NAME = "researcher"
        SECRET = "super-secret"
        SAFE_MESSAGE = "Searching the knowledge base."
