from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from agent_runtime.execution.contracts import AgentRuntimeContext


@dataclass
class FakeToolRegistry:
    tools: Sequence[object] = ("doc_search",)
    seen_contexts: list[AgentRuntimeContext] = field(default_factory=list)

    def list_available_tools(self, context: object) -> Sequence[object]:
        self.seen_contexts.append(context)  # type: ignore[arg-type]
        return self.tools


@dataclass
class FakeMcpRegistry:
    servers: Sequence[object] = ("drive_mcp",)
    seen_contexts: list[AgentRuntimeContext] = field(default_factory=list)

    async def list_available_servers(self, context: object) -> Sequence[object]:
        self.seen_contexts.append(context)  # type: ignore[arg-type]
        return self.servers


@dataclass
class FakeMemoryBackendFactory:
    backend: object = "memory"
    seen_contexts: list[AgentRuntimeContext] = field(default_factory=list)

    def create(self, context: object) -> object:
        self.seen_contexts.append(context)  # type: ignore[arg-type]
        return self.backend


@dataclass
class FakeSubagentCatalog:
    subagents: Sequence[object] = ("researcher",)
    seen_contexts: list[AgentRuntimeContext] = field(default_factory=list)

    def list_available_subagents(self, context: object) -> Sequence[object]:
        self.seen_contexts.append(context)  # type: ignore[arg-type]
        return self.subagents
