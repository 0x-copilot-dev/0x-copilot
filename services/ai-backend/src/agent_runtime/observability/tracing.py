"""Trace and correlation helpers for runtime observability."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from agent_runtime.observability.constants import Keys, Patterns


class TraceContext:
    """Resolve stable trace and correlation identifiers from runtime inputs."""

    @classmethod
    def trace_id_for(cls, *, raw_event: Mapping[str, object], context: object) -> str:
        """Prefer runtime context trace IDs, then raw event IDs, then generate one."""

        context_trace_id = getattr(context, Keys.Field.TRACE_ID, None)
        if isinstance(context_trace_id, str) and Patterns.ID.fullmatch(context_trace_id):
            return context_trace_id

        raw_trace_id = raw_event.get(Keys.Raw.TRACE_ID)
        if isinstance(raw_trace_id, str) and Patterns.ID.fullmatch(raw_trace_id.strip()):
            return raw_trace_id.strip()

        metadata = raw_event.get(Keys.Raw.METADATA)
        if isinstance(metadata, Mapping):
            metadata_trace_id = metadata.get(Keys.Raw.TRACE_ID)
            if isinstance(metadata_trace_id, str) and Patterns.ID.fullmatch(
                metadata_trace_id.strip()
            ):
                return metadata_trace_id.strip()

        return uuid4().hex

    @classmethod
    def event_id(cls) -> str:
        """Return a stable unique event identifier."""

        return uuid4().hex
