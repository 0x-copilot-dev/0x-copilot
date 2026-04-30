from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_runtime.agent.streaming import LangGraphStreamNormalizer
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


class StreamingObservabilityTestMixin:
    class Values:
        TRACE_ID = "trace_123"
        TASK_ID = "task_123"
        CALL_ID = "call_123"
        TOOL_NAME = "doc_search"
        SUBAGENT_NAME = "researcher"
        SECRET = "super-secret"
        SAFE_MESSAGE = "Searching the knowledge base."

    def make_normalizer(self) -> LangGraphStreamNormalizer:
        return LangGraphStreamNormalizer()

    def main_update_chunk(self) -> dict[str, object]:
        return {
            "mode": "updates",
            "chunk": {
                "message": self.Values.SAFE_MESSAGE,
                "api_key": self.Values.SECRET,
            },
        }

    def subagent_progress_chunk(self) -> dict[str, object]:
        return {
            "mode": "custom",
            "ns": ("supervisor", f"subagent:{self.Values.SUBAGENT_NAME}"),
            "chunk": {
                "message": "Subagent is reading sources.",
                "parent_task_id": self.Values.TASK_ID,
            },
        }

    def tool_call_chunk(self) -> dict[str, object]:
        return {
            "mode": "messages",
            "chunk": {
                "tool_calls": (
                    {
                        "name": self.Values.TOOL_NAME,
                        "id": self.Values.CALL_ID,
                        "args": {
                            "query": "board plan",
                            "authorization": f"bearer {self.Values.SECRET}",
                        },
                    },
                ),
            },
        }

    def tool_result_chunk(self, content: str) -> dict[str, object]:
        return {
            "mode": "messages",
            "chunk": {
                "type": "tool_result",
                "name": self.Values.TOOL_NAME,
                "id": self.Values.CALL_ID,
                "content": content,
                "token": self.Values.SECRET,
            },
        }

    def lifecycle_chunk_without_task_metadata(self) -> dict[str, object]:
        return {
            "mode": "custom",
            "event_type": "lifecycle",
            "ns": ("supervisor", f"subagent:{self.Values.SUBAGENT_NAME}"),
            "chunk": {
                "status": "running",
                "summary": "Subagent started before supervisor metadata arrived.",
            },
        }
