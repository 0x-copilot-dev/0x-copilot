"""ContextVar-bound guard that bridges ToolBudgetMiddleware to the LangChain tool dispatch loop."""

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
    """Input-token estimate caps; 1 char ≈ 0.25 tokens, capped per call."""

    CHARS_PER_TOKEN = 4
    MAX_ESTIMATED_TOKENS = 100_000


class ToolBudgetGuard:
    """Per-run holder for the budget middleware, the call ledger, and the optional event producer."""

    def __init__(
        self,
        *,
        middleware: ToolBudgetMiddleware,
        ledger: "ToolCallLedger",
        run: RunRecord | None = None,
        event_producer: object | None = None,
    ) -> None:
        """Initialise the guard with a middleware, ledger, and optional event emitter."""
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
        # The actual token cost is recorded at settlement; estimate is pre-check only.
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
    """Cheap character-count-based input-token estimator for tool args."""

    @classmethod
    def estimate(cls, args: tuple[Any, ...], kwargs: dict[str, Any]) -> int:
        """Return an estimated input-token count for ``args``/``kwargs``, capped at ``MAX_ESTIMATED_TOKENS``."""
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
        """JSON serialisation fallback: convert to str."""
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
        """Sync gate: check budget, record the call, delegate to the inner tool."""
        guard = ToolBudgetGuard.active()
        if guard is None:
            return self.inner._run(*args, **kwargs)
        estimated = _Estimator.estimate(args, kwargs)
        decision = guard.check_admit(
            tool_name=self.name, estimated_input_tokens=estimated
        )
        if isinstance(decision, ToolBudgetReject):
            # Raise a typed error so the run handler terminates the run rather than
            # letting the model attempt to talk its way past the hard cap.
            raise BudgetExceeded(decision.safe_message)
        if isinstance(decision, ToolBudgetWarn):
            # Sync path: schedule warning emission on the running loop; fall back to log.
            self._schedule_warning(guard=guard, decision=decision)
        call_id = guard.record_started(
            tool_name=self.name, estimated_input_tokens=estimated
        )
        try:
            return self.inner._run(*args, **kwargs)
        finally:
            guard.record_settled(call_id=call_id, observed_input_tokens=estimated)

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        """Async gate: check budget, record the call, delegate to the inner tool."""
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
            # Defensive: treat any unknown decision variant as a hard reject so
            # future variants can never silently bypass the gate.
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
        """Schedule a budget-warning event on the running loop, or log if none exists."""
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
    """Registry decorator that wraps every ``BaseTool`` in a :class:`ToolBudgetGuardedTool`."""

    def __init__(self, *, inner: object) -> None:
        """Wrap ``inner`` registry; all ``BaseTool`` returns will be budget-guarded."""
        self._inner = inner

    def list_available_tools(self, context: object) -> tuple[object, ...]:
        """Return all tools from the inner registry, each wrapped with budget enforcement."""
        rendered = self._inner.list_available_tools(context)  # type: ignore[attr-defined]
        return tuple(self._wrap(tool) for tool in rendered)

    @staticmethod
    def _wrap(tool: object) -> object:
        """Wrap ``tool`` in a ``ToolBudgetGuardedTool``; return non-BaseTool entries unchanged."""
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
