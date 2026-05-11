"""Single decision point for how to handle a tool execution failure.

Default behavior: every uncaught exception from a tool becomes a
sanitized message handed back to the LLM as a ``ToolMessage`` so the
agent can reason about it. Typed :class:`RunFatalToolError` subclasses
opt specific failures into terminating the run instead — budget,
auth, policy violations.

Wired into the tool execution layer via
:class:`ToolErrorPolicyTool`; the run handler's exception handler
recognizes the ``FAIL_RUN`` outcome and routes it to
:class:`RunTerminationCoordinator`.
"""

from __future__ import annotations

import json
import traceback
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from langchain_core.tools import BaseTool

from agent_runtime.execution.tool_error_sanitizer import (
    ErrorHintExtractor,
    ErrorSanitizer,
)
from agent_runtime.execution.tool_errors import RunFatalToolError


class ToolErrorOutcome(StrEnum):
    """Routing decision for a caught tool error."""

    SURFACE_TO_LLM = "surface_to_llm"
    """Return the sanitized error as the tool's result so the LLM sees it."""

    FAIL_RUN = "fail_run"
    """Typed policy violation — the run should terminate."""


@dataclass(frozen=True)
class ToolErrorClassification:
    """A policy decision plus the safe / structured payloads it produced."""

    outcome: ToolErrorOutcome
    error_class: str
    sanitized_message: str
    structured_hints: Mapping[str, Any] = field(default_factory=dict)
    # Full pre-sanitization traceback for the backend audit log. Never
    # returned to the LLM, never serialized into a ``ToolMessage``.
    audit_trace: str | None = None

    def to_llm_message_content(self) -> str:
        """Build the string content for the model-visible ``ToolMessage``.

        Format: ``<error_class>: <sanitized_message>`` plus a compact
        JSON ``Hints:`` block when there are structured hints. The LLM
        gets enough to fix its call without seeing internals.
        """

        body = f"{self.error_class}: {self.sanitized_message}"
        if not self.structured_hints:
            return body
        hints = json.dumps(self.structured_hints, default=str, sort_keys=True)
        return f"{body}\nHints: {hints}"


@runtime_checkable
class ToolErrorPolicy(Protocol):
    """Classify an exception into a :class:`ToolErrorClassification`."""

    def classify(
        self, exc: BaseException, *, tool: BaseTool
    ) -> ToolErrorClassification: ...


class DefaultToolErrorPolicy:
    """The shipped policy.

    Routing rule, in order:
      1. :class:`RunFatalToolError` (any subclass) → ``FAIL_RUN``.
      2. Anything else → ``SURFACE_TO_LLM`` with sanitized message and
         structured hints attached.

    Cancellation, keyboard interrupts, and system exits are NEVER
    classified — the caller is expected to re-raise them before
    invoking the policy. The policy intentionally has no opinion on
    cancellation.
    """

    def classify(
        self, exc: BaseException, *, tool: BaseTool
    ) -> ToolErrorClassification:
        if isinstance(exc, RunFatalToolError):
            return ToolErrorClassification(
                outcome=ToolErrorOutcome.FAIL_RUN,
                error_class=type(exc).__name__,
                sanitized_message=exc.safe_summary,
                structured_hints={},
                audit_trace=self._safe_traceback(exc),
            )
        return ToolErrorClassification(
            outcome=ToolErrorOutcome.SURFACE_TO_LLM,
            error_class=type(exc).__name__,
            sanitized_message=ErrorSanitizer.sanitize(exc),
            structured_hints=ErrorHintExtractor.extract(exc),
            audit_trace=self._safe_traceback(exc),
        )

    @staticmethod
    def _safe_traceback(exc: BaseException) -> str | None:
        try:
            return "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
        except Exception:  # noqa: BLE001 — best effort
            return None


__all__ = (
    "DefaultToolErrorPolicy",
    "ToolErrorClassification",
    "ToolErrorOutcome",
    "ToolErrorPolicy",
)
