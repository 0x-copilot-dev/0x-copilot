"""Typed state aliases for LangGraph adapter edges."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeAlias

from agent_runtime.agent.contracts import AgentRuntimeContext, JsonScalar

RuntimeMessage: TypeAlias = Mapping[str, JsonScalar]
RuntimeMessages: TypeAlias = Sequence[RuntimeMessage]


class RuntimeMetadata(Mapping[str, str]):
    """Read-only metadata exposed to graph config and trace surfaces."""

    def __init__(self, context: AgentRuntimeContext) -> None:
        self._values = {
            "trace_id": context.trace_id,
            "user_id": context.user_id,
            "org_id": context.org_id,
        }

    def __getitem__(self, key: str) -> str:
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)
