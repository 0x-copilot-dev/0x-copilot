"""Typed helpers for LangGraph stream part metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agent_runtime.execution.contracts import JsonObject


@dataclass(frozen=True)
class StreamNamespace:
    """Parsed LangGraph v2 namespace metadata."""

    parts: tuple[str, ...]

    @classmethod
    def from_value(cls, value: object) -> "StreamNamespace":
        if isinstance(value, str):
            return cls((value,))
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            return cls(tuple(str(item) for item in value))
        return cls(())

    @property
    def subagent_task_id(self) -> str | None:
        for part in self.parts:
            if part.startswith("tools:"):
                return part.split(":", maxsplit=1)[1] or None
        return None

    @property
    def is_subagent(self) -> bool:
        return self.subagent_task_id is not None

    def metadata(self, stream_type: str) -> JsonObject:
        metadata: JsonObject = {"stream_type": stream_type}
        if self.parts:
            metadata["namespace"] = list(self.parts)
        return metadata


class StreamPartParser:
    """Parse the documented LangGraph v2 stream part envelope."""

    @classmethod
    def stream_part(cls, chunk: object) -> dict[str, object] | None:
        if not isinstance(chunk, Mapping):
            return None
        stream_type = chunk.get("type")
        if not isinstance(stream_type, str) or "data" not in chunk:
            return None
        return dict(chunk)

    @classmethod
    def stream_type(cls, part: Mapping[str, object]) -> str:
        return str(part["type"])

    @classmethod
    def namespace_for(cls, part: Mapping[str, object]) -> StreamNamespace:
        return StreamNamespace.from_value(part.get("ns", ()))
