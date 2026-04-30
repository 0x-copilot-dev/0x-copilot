"""Trace and correlation helpers for runtime observability."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import wraps
from importlib import import_module
import os
from typing import Any, Callable, TypeVar
from uuid import uuid4

from agent_runtime.observability.constants import Keys, Patterns

_F = TypeVar("_F", bound=Callable[..., Any])


class TraceNames:
    """Stable public names used for tracing and log correlation."""

    RUNTIME_CREATE_AGENT = "runtime.create_agent_runtime"
    RUNTIME_INVOKE = "runtime.invoke"
    RUNTIME_STREAM = "runtime.stream"
    TOOLS_LIST_CARDS = "tools.list_cards"
    TOOLS_LOAD_SPEC = "tools.load_spec"
    MCP_LIST_SERVERS = "mcp.list_servers"
    MCP_LOAD_SERVER = "mcp.load_server"
    SKILLS_DISCOVER_SOURCES = "skills.discover_sources"
    MEMORY_PREPARE_PAYLOAD = "memory.prepare_payload"
    SUBAGENTS_BUILD_HANDOFF = "subagents.build_handoff"
    SUBAGENTS_START_TASK = "subagents.start_task"
    STREAMS_NORMALIZE_CHUNK = "streams.normalize_chunk"


class TraceRunTypes:
    """LangSmith-compatible run type names for runtime boundaries."""

    CHAIN = "chain"
    TOOL = "tool"
    PARSER = "parser"
    RETRIEVER = "retriever"


@dataclass(frozen=True)
class TraceOptions:
    """Runtime tracing switches that keep LangSmith optional."""

    enabled: bool = False
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, object] | None = None

    @classmethod
    def from_environment(cls) -> "TraceOptions":
        return cls(
            enabled=os.getenv("LANGSMITH_TRACING", "").lower() in {"1", "true", "yes"},
            tags=("agent_runtime",),
        )


class TraceContext:
    """Resolve stable trace and correlation identifiers from runtime inputs."""

    @classmethod
    def trace_id_for(cls, *, raw_event: Mapping[str, object], context: object) -> str:
        """Prefer runtime context trace IDs, then raw event IDs, then generate one."""

        context_trace_id = getattr(context, Keys.Field.TRACE_ID, None)
        if isinstance(context_trace_id, str) and Patterns.ID.fullmatch(
            context_trace_id
        ):
            return context_trace_id

        raw_trace_id = raw_event.get(Keys.Raw.TRACE_ID)
        if isinstance(raw_trace_id, str) and Patterns.ID.fullmatch(
            raw_trace_id.strip()
        ):
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

    @classmethod
    def identity_hash(cls, value: object) -> str:
        """Return a stable non-PII hash for identity metadata."""

        from hashlib import sha256

        encoded = str(value).encode("utf-8", errors="replace")
        return sha256(encoded).hexdigest()[:16]

    @classmethod
    def langsmith_extra_for(
        cls, context: object, *, operation: str
    ) -> dict[str, object]:
        """Return safe dynamic metadata for optional LangSmith traces."""

        request_id = getattr(context, "request_id", None)
        run_id = getattr(context, "run_id", None)
        trace_id = getattr(context, "trace_id", None)
        parent_trace_id = getattr(context, "parent_trace_id", None)
        metadata = {
            "operation": operation,
            "request_id": request_id,
            "run_id": run_id,
            "trace_id": trace_id,
            "parent_trace_id": parent_trace_id,
            "user_id_hash": cls.identity_hash(getattr(context, "user_id", "")),
            "org_id_hash": cls.identity_hash(getattr(context, "org_id", "")),
        }
        return {
            "tags": ("agent_runtime", f"run:{run_id}")
            if run_id
            else ("agent_runtime",),
            "metadata": {
                key: value for key, value in metadata.items() if value is not None
            },
        }


class RuntimeTracer:
    """Small adapter around LangSmith tracing that is a no-op unless enabled."""

    def __init__(self, options: TraceOptions | None = None) -> None:
        self.options = options or TraceOptions.from_environment()

    def traceable(
        self,
        *,
        name: str,
        run_type: str = TraceRunTypes.CHAIN,
        tags: tuple[str, ...] = (),
        metadata: Mapping[str, object] | None = None,
    ) -> Callable[[_F], _F]:
        """Return a LangSmith decorator when available, otherwise return identity."""

        def decorator(func: _F) -> _F:
            if not self.options.enabled:
                return func
            traceable = self._load_traceable()
            if traceable is None:
                return func
            configured_tags = tuple(self.options.tags) + tuple(tags)
            configured_metadata = {
                **dict(self.options.metadata or {}),
                **dict(metadata or {}),
            }
            return traceable(
                name=name,
                run_type=run_type,
                tags=list(configured_tags),
                metadata=configured_metadata,
            )(func)

        return decorator

    @classmethod
    def _load_traceable(cls) -> Callable[..., Callable[[_F], _F]] | None:
        try:
            langsmith = import_module("langsmith")
        except Exception:
            return None
        traceable = getattr(langsmith, "traceable", None)
        if not callable(traceable):
            return None
        return traceable


def traced(
    *,
    name: str,
    run_type: str = TraceRunTypes.CHAIN,
    tags: tuple[str, ...] = (),
    metadata: Mapping[str, object] | None = None,
) -> Callable[[_F], _F]:
    """Decorate a runtime boundary with optional LangSmith tracing."""

    tracer = RuntimeTracer()

    def decorator(func: _F) -> _F:
        traced_func = tracer.traceable(
            name=name,
            run_type=run_type,
            tags=tags,
            metadata=metadata,
        )(func)

        @wraps(func)
        def wrapper(*args: object, **kwargs: object) -> object:
            return traced_func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
