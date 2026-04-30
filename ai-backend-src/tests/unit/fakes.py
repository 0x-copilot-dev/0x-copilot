from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from enterprise_search_ai.agent.contracts import (
    AgentRuntimeContext,
    StreamEvent,
    StreamEventSource,
    StreamEventType,
)


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

    def list_available_servers(self, context: object) -> Sequence[object]:
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


@dataclass
class FakeStreamNormalizer:
    seen_contexts: list[AgentRuntimeContext] = field(default_factory=list)

    def normalize(self, raw_event: Mapping[str, object], context: object) -> Sequence[object]:
        self.seen_contexts.append(context)  # type: ignore[arg-type]
        return (
            StreamEvent(
                source=StreamEventSource.RUNTIME,
                event_type=StreamEventType.PROGRESS,
                trace_id=context.trace_id,  # type: ignore[attr-defined]
                payload={"message": str(raw_event.get("message", "ok"))},
            ),
        )
