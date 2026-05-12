"""Typed helpers for LangGraph stream part metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agent_runtime.execution.contracts import JsonObject

# Stable contract: our `atlas_task_tool` writes this key into each
# subagent's RunnableConfig metadata. LangGraph v2 streaming propagates
# that metadata into the second element of `chunk["data"]` for
# `type=messages` chunks (`(message, metadata)`), and as a top-level
# `metadata` field for some other modes. The worker reads it via
# `StreamPartParser.supervisor_task_call_id_for(chunk)` and uses it to
# correlate a subgraph_task_id with its supervisor call_id
# deterministically — no FIFO heuristic.
SUPERVISOR_TASK_CALL_ID_KEY = "supervisor_task_call_id"


@dataclass(frozen=True)
class StreamNamespace:
    """Parsed LangGraph v2 namespace metadata."""

    parts: tuple[str, ...]

    @classmethod
    def from_value(cls, value: object) -> "StreamNamespace":
        """Construct a ``StreamNamespace`` from a string, sequence, or unknown value."""
        if isinstance(value, str):
            return cls((value,))
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            return cls(tuple(str(item) for item in value))
        return cls(())

    @property
    def subagent_task_id(self) -> str | None:
        """Return the task ID encoded in a ``tools:<id>`` namespace part, or ``None``."""
        for part in self.parts:
            if part.startswith("tools:"):
                return part.split(":", maxsplit=1)[1] or None
        return None

    @property
    def is_subagent(self) -> bool:
        """Return ``True`` when this namespace belongs to a subagent invocation."""
        return self.subagent_task_id is not None

    def metadata(self, stream_type: str) -> JsonObject:
        """Build the event metadata dict carrying stream_type and the namespace parts."""
        metadata: JsonObject = {"stream_type": stream_type}
        if self.parts:
            metadata["namespace"] = list(self.parts)
        return metadata


class StreamPartParser:
    """Typed accessors for the LangGraph v2 stream part envelope (type, data, ns, metadata)."""

    @classmethod
    def stream_part(cls, chunk: object) -> dict[str, object] | None:
        """Return ``chunk`` as a plain dict if it is a valid LangGraph stream part, else ``None``."""
        if not isinstance(chunk, Mapping):
            return None
        stream_type = chunk.get("type")
        if not isinstance(stream_type, str) or "data" not in chunk:
            return None
        return dict(chunk)

    @classmethod
    def stream_type(cls, part: Mapping[str, object]) -> str:
        """Return the ``type`` string from a parsed stream part."""
        return str(part["type"])

    @classmethod
    def namespace_for(cls, part: Mapping[str, object]) -> StreamNamespace:
        """Parse and return the ``StreamNamespace`` for the given stream part."""
        return StreamNamespace.from_value(part.get("ns", ()))

    @classmethod
    def supervisor_task_call_id_for(cls, part: Mapping[str, object]) -> str | None:
        """Return the supervisor task call_id injected into chunk metadata by the task tool, or ``None``."""
        # messages-mode tuple: data = (message, metadata)
        data = part.get("data")
        if isinstance(data, tuple) and len(data) >= 2:
            metadata_candidate = data[1]
            if isinstance(metadata_candidate, Mapping):
                value = metadata_candidate.get(SUPERVISOR_TASK_CALL_ID_KEY)
                if isinstance(value, str) and value:
                    return value
        # Fall-through: top-level chunk metadata (other stream modes).
        top_metadata = part.get("metadata")
        if isinstance(top_metadata, Mapping):
            value = top_metadata.get(SUPERVISOR_TASK_CALL_ID_KEY)
            if isinstance(value, str) and value:
                return value
        return None
