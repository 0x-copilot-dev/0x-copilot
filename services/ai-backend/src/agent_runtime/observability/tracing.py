"""Trace and correlation helpers for runtime observability.

Provides opt-in LangSmith tracing via ``LANGSMITH_TRACING`` (default off; the
``langsmith`` import is lazy so missing the package is fine) and utility helpers
on :class:`TraceContext` (``event_id``, ``identity_hash``). Distributed
cross-process tracing is handled by OTel and
:class:`~agent_runtime.observability.queue_propagation.QueueTracePropagator`;
LangSmith is the per-LLM-call trace surface for teams using the LangSmith UI.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
import os
from typing import Any, Callable, TypeVar
from uuid import uuid4

_F = TypeVar("_F", bound=Callable[..., Any])


class TraceNames:
    """Stable public names used for tracing and log correlation."""

    RUNTIME_INVOKE = "runtime.invoke"


class TraceRunTypes:
    """LangSmith-compatible run type names for runtime boundaries."""

    CHAIN = "chain"
    TOOL = "tool"


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
    def event_id(cls) -> str:
        """Return a stable unique event identifier."""

        return uuid4().hex

    @classmethod
    def identity_hash(cls, value: object) -> str:
        """Return a stable non-PII hash for identity metadata."""

        from hashlib import sha256

        encoded = str(value).encode("utf-8", errors="replace")
        return sha256(encoded).hexdigest()[:16]


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

    @classmethod
    def traced(
        cls,
        *,
        name: str,
        run_type: str = TraceRunTypes.CHAIN,
        tags: tuple[str, ...] = (),
        metadata: Mapping[str, object] | None = None,
    ) -> Callable[[_F], _F]:
        """Decorate a runtime boundary with optional LangSmith tracing."""

        return cls().traceable(
            name=name, run_type=run_type, tags=tags, metadata=metadata
        )
