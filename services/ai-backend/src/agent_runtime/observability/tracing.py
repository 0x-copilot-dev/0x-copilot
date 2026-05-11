"""Trace and correlation helpers for runtime observability.

P13 step 3 — LangSmith decision: **kept, opt-in via the
``LANGSMITH_TRACING`` env var**. The four ``RuntimeTracer.traced(...)``
call sites in ``agent_runtime.execution.runtime`` plus utility helpers
in :class:`TraceContext` (``event_id``, ``identity_hash``) are active.
When ``LANGSMITH_TRACING`` is unset (the default), the decorator is a
true no-op — the import of ``langsmith`` itself is lazy inside
:meth:`RuntimeTracer._load_traceable`, so a deploy without the package
installed is fine. Distributed tracing across processes lives in OTel
(see :class:`agent_runtime.observability.queue_propagation`); LangSmith
remains the LLM-internal trace surface for teams that consume the
LangSmith UI.

If a future audit shows ``LANGSMITH_TRACING`` is unset in all
environments AND the four decorator call sites have no value to
remaining consumers, this module can be retired in one PR. The
:meth:`TraceContext.event_id` / :meth:`identity_hash` helpers would
move to a small ``identity.py`` helper at that point — they are
otherwise independent of LangSmith.
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
