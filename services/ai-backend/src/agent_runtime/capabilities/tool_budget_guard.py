"""Per-run tool-budget guard wiring (B8 — completes the spec).

Bridges :class:`ToolBudgetMiddleware` to the LangChain tool dispatch
loop. The runtime worker constructs one :class:`ToolBudgetGuard` per
run, binds it on a :mod:`contextvars` slot, and registers each
model-visible tool wrapped in :class:`ToolBudgetGuardedTool` — the wrapper
consults the active guard before delegating to the inner tool.

The wrapper renders three outcomes:

- :class:`ToolBudgetAdmit` — call delegates to the inner tool. The
  guard records the observed token cost on completion so the per-run
  cap is enforced across consecutive calls.
- :class:`ToolBudgetWarn` — soft cap; the wrapper emits a
  ``BUDGET_WARNING`` event (best-effort) and admits the call.
- :class:`ToolBudgetReject` — hard cap; the inner tool is **not**
  invoked. The wrapper returns the middleware's safe public message as
  the tool result so the model sees "you've used your web_search
  budget" and can adapt.

When no guard is bound (no :class:`CitationLedger`-equivalent context),
the wrapper is a passthrough — calls are not recorded, no events fire.
This keeps unit tests of inner tools unchanged.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from langchain_core.tools import BaseTool
from pydantic import ConfigDict

from agent_runtime.capabilities.tool_budget_middleware import (
    ToolBudgetAdmit,
    ToolBudgetDecision,
    ToolBudgetMiddleware,
    ToolBudgetReject,
    ToolBudgetWarn,
)
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.tool_errors import BudgetExceeded
from runtime_api.schemas import RuntimeApiEventType, RunRecord

if TYPE_CHECKING:  # pragma: no cover — typing-only; runtime import is lazy.
    from runtime_worker.tool_call_ledger import ToolCallLedger


_LOGGER = logging.getLogger(__name__)


class _Limits:
    """Token-cost estimate caps.

    Estimating the input token cost for an arbitrary tool call without
    encoding-model knowledge is approximate by design. We use a simple
    1-character ≈ 0.25-token heuristic, capped so a pathological args
    blob can't overflow the per-run cap on a single call. Production
    tools that care about exact accounting can call
    :meth:`ToolCallLedger.charge` themselves with measured counts.
    """

    CHARS_PER_TOKEN = 4
    MAX_ESTIMATED_TOKENS = 100_000


class ToolBudgetGuard:
    """Per-run holder for :class:`ToolBudgetMiddleware` + :class:`ToolCallLedger`.

    The guard owns:

    - the immutable middleware (built from the budget snapshot at run
      start),
    - the mutable :class:`ToolCallLedger` (in-flight + completed calls),
    - an optional :class:`RuntimeEventProducer` used to emit
      ``BUDGET_WARNING`` events on soft caps.

    When the producer is ``None`` (e.g. unit tests of the wrapper alone)
    warnings are logged instead of emitted. The wrapper still admits.
    """

    def __init__(
        self,
        *,
        middleware: ToolBudgetMiddleware,
        ledger: "ToolCallLedger",
        run: RunRecord | None = None,
        event_producer: object | None = None,
    ) -> None:
        self._middleware = middleware
        self._ledger = ledger
        self._run = run
        self._event_producer = event_producer

    @classmethod
    def bind_for_run(cls, guard: "ToolBudgetGuard") -> object:
        """Set the active guard; return the previous token for restoration."""

        return _GUARD_CTX.set(guard)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Restore the previous guard token. Safe to call with the bind result."""

        _GUARD_CTX.reset(token)  # type: ignore[arg-type]

    @classmethod
    def active(cls) -> "ToolBudgetGuard | None":
        """Return the active guard or ``None``."""

        return _GUARD_CTX.get(None)

    def check_admit(
        self, *, tool_name: str, estimated_input_tokens: int
    ) -> ToolBudgetDecision:
        """Resolve the per-tool budget against the live ledger."""

        return self._middleware.check_admit(
            ledger=self._ledger,
            tool_name=tool_name,
            estimated_input_tokens=estimated_input_tokens,
        )

    def record_started(self, *, tool_name: str, estimated_input_tokens: int) -> str:
        """Open a ledger entry for an admitted call. Returns the call id."""

        call_id = uuid4().hex
        self._ledger.started(
            call_id,
            tool_name=tool_name,
        )
        # The ledger's per-call ``input_tokens`` slot is filled at
        # completion via ``record_settled``; the estimate is used only
        # for the per-call cap pre-check.
        del estimated_input_tokens
        return call_id

    def record_settled(
        self,
        *,
        call_id: str,
        observed_input_tokens: int,
    ) -> None:
        """Close a ledger entry with the observed input-token cost."""

        self._ledger.record_input_tokens(call_id, observed_input_tokens)
        self._ledger.observed_settled(call_id)

    async def emit_warning(self, *, decision: ToolBudgetWarn) -> None:
        """Best-effort BUDGET_WARNING emission."""

        producer = self._event_producer
        run = self._run
        if (
            producer is None
            or run is None
            or not callable(getattr(producer, "append_api_event", None))
        ):
            _LOGGER.info(
                "tool_budget_warn",
                extra={
                    "metadata": {
                        "tool_name": decision.budget.tool_name,
                        "kind": decision.kind,
                        "current": decision.current,
                        "limit": decision.limit,
                    }
                },
            )
            return
        try:
            await producer.append_api_event(
                run=run,
                source=StreamEventSource.SYSTEM,
                event_type=RuntimeApiEventType.BUDGET_WARNING,
                payload={
                    "tool_name": decision.budget.tool_name,
                    "kind": decision.kind,
                    "current": decision.current,
                    "limit": decision.limit,
                    "enforcement": decision.budget.enforcement.value,
                },
                summary=(
                    f"Tool '{decision.budget.tool_name}' near {decision.kind} cap "
                    f"({decision.current}/{decision.limit})."
                ),
            )
        except Exception:  # pragma: no cover — best-effort logging
            _LOGGER.warning(
                "tool_budget_warning_emit_failed",
                exc_info=True,
            )


_GUARD_CTX: ContextVar[ToolBudgetGuard | None] = ContextVar(
    "tool_budget_guard",
    default=None,
)


class _Estimator:
    """Cheap input-token estimate for tool args.

    Encapsulated so production code paths can swap in a tighter
    estimator (e.g. tiktoken) without touching the wrapper.
    """

    @classmethod
    def estimate(cls, args: tuple[Any, ...], kwargs: dict[str, Any]) -> int:
        try:
            payload = json.dumps([args, kwargs], default=cls._fallback)
        except (TypeError, ValueError):
            payload = repr((args, kwargs))
        return min(
            len(payload) // _Limits.CHARS_PER_TOKEN,
            _Limits.MAX_ESTIMATED_TOKENS,
        )

    @staticmethod
    def _fallback(value: object) -> str:
        return str(value)


class ToolBudgetGuardedTool(BaseTool):
    """LangChain ``BaseTool`` wrapper that gates calls through the active guard.

    Inner tool's ``name`` / ``description`` / ``args_schema`` are
    propagated so the model sees an identical surface. Only the
    invocation path differs.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseTool

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        guard = ToolBudgetGuard.active()
        if guard is None:
            return self.inner._run(*args, **kwargs)
        estimated = _Estimator.estimate(args, kwargs)
        decision = guard.check_admit(
            tool_name=self.name, estimated_input_tokens=estimated
        )
        if isinstance(decision, ToolBudgetReject):
            # HARD-cap rejection. Raise a typed RunFatalToolError so the
            # run handler terminates the run via RunTerminationCoordinator
            # instead of letting the model talk its way past the cap.
            raise BudgetExceeded(decision.safe_message)
        if isinstance(decision, ToolBudgetWarn):
            # Sync path: schedule the warning emission on the running
            # loop if there is one; fall back to a synchronous log.
            self._schedule_warning(guard=guard, decision=decision)
        call_id = guard.record_started(
            tool_name=self.name, estimated_input_tokens=estimated
        )
        try:
            return self.inner._run(*args, **kwargs)
        finally:
            guard.record_settled(call_id=call_id, observed_input_tokens=estimated)

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        guard = ToolBudgetGuard.active()
        if guard is None:
            return await self.inner._arun(*args, **kwargs)
        estimated = _Estimator.estimate(args, kwargs)
        decision = guard.check_admit(
            tool_name=self.name, estimated_input_tokens=estimated
        )
        if isinstance(decision, ToolBudgetReject):
            raise BudgetExceeded(decision.safe_message)
        if isinstance(decision, ToolBudgetWarn):
            await guard.emit_warning(decision=decision)
        if not isinstance(decision, (ToolBudgetAdmit, ToolBudgetWarn)):
            # Defensive: an unknown decision shape would otherwise
            # silently admit. Treat as a hard reject so unknown variants
            # can never bypass the gate.
            raise BudgetExceeded("Tool call was not admitted by the budget middleware.")
        call_id = guard.record_started(
            tool_name=self.name, estimated_input_tokens=estimated
        )
        try:
            return await self.inner._arun(*args, **kwargs)
        finally:
            guard.record_settled(call_id=call_id, observed_input_tokens=estimated)

    @staticmethod
    def _schedule_warning(*, guard: ToolBudgetGuard, decision: ToolBudgetWarn) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _LOGGER.info(
                "tool_budget_warn_sync",
                extra={
                    "metadata": {
                        "tool_name": decision.budget.tool_name,
                        "kind": decision.kind,
                        "current": decision.current,
                        "limit": decision.limit,
                    }
                },
            )
            return
        loop.create_task(guard.emit_warning(decision=decision))


class ToolBudgetGuardedRegistry:
    """Wrap a tool registry so every returned tool is budget-guarded.

    Mirrors the :class:`agent_runtime.capabilities.auth_gate` decorator
    pattern: the registry-of-tools port is passed through unchanged,
    only the ``list_available_tools`` output is rewritten to wrap each
    BaseTool in :class:`ToolBudgetGuardedTool`. Tools that aren't
    LangChain ``BaseTool`` instances (e.g. internal adapter objects)
    pass through untouched — the guard only applies to the model-visible
    LangChain layer.
    """

    def __init__(self, *, inner: object) -> None:
        self._inner = inner

    def list_available_tools(self, context: object) -> tuple[object, ...]:
        rendered = self._inner.list_available_tools(context)  # type: ignore[attr-defined]
        return tuple(self._wrap(tool) for tool in rendered)

    @staticmethod
    def _wrap(tool: object) -> object:
        if not isinstance(tool, BaseTool):
            return tool
        if isinstance(tool, ToolBudgetGuardedTool):
            return tool
        return ToolBudgetGuardedTool(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            inner=tool,
        )


# Optional callable signature for callers that want to build a guard
# from a budget-loading function (e.g. the run handler).
ToolBudgetSnapshotLoader = Callable[[str], Awaitable[list[object]]]


__all__ = (
    "ToolBudgetGuard",
    "ToolBudgetGuardedRegistry",
    "ToolBudgetGuardedTool",
)
