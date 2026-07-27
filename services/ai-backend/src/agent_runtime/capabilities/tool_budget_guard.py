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
from agent_runtime.capabilities.task_policy import (
    RequestFingerprint,
    ToolOperationOutcome,
    ToolPolicyRejected,
    ToolUseController,
    ToolUseDisposition,
    ToolUseIntent,
)
from agent_runtime.capabilities.tool_result_notes import ToolResultNote
from agent_runtime.context.tool_result_admission import ToolResultAdmissionAdapter
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.tool_errors import BudgetExceeded, ToolBudgetRejected
from runtime_api.schemas import RuntimeApiEventType, RunRecord

if TYPE_CHECKING:  # pragma: no cover — typing-only; runtime import is lazy.
    from runtime_worker.tool_call_ledger import ToolCallLedger


_LOGGER = logging.getLogger(__name__)

# Top-level key for the remaining-calls note on a dict-shaped tool result.
# Distinct from the citation hint's key so both can ride on one result.
_TOOL_BUDGET_NOTE_KEY = "_tool_budget_note"


class _Limits:
    """Input-token estimate caps; 1 char ≈ 0.25 tokens, capped per call."""

    CHARS_PER_TOKEN = 4
    MAX_ESTIMATED_TOKENS = 100_000

    MAX_SURFACED_REJECTIONS = 6
    """Hard-cap refusals handed to the model before the run is failed.

    Refusing a call costs nothing — the inner tool never runs — so the
    only thing this bounds is a model that answers every refusal with
    another tool call instead of finalizing. Six is deliberately loose:
    a single turn can fan out several parallel calls and have all of
    them refused, and the model still deserves a clean turn afterwards
    to write its answer. It stays well inside LangGraph's default
    recursion limit, so a looping model hits this legible error rather
    than an opaque ``GraphRecursionError``.
    """


class ToolBudgetGuard:
    """Per-run holder for the budget middleware, the call ledger, and the optional event producer."""

    def __init__(
        self,
        *,
        middleware: ToolBudgetMiddleware,
        ledger: "ToolCallLedger",
        run: RunRecord | None = None,
        event_producer: object | None = None,
        max_surfaced_rejections: int | None = None,
        task_policy_controller: ToolUseController | None = None,
        task_request_fingerprint: RequestFingerprint | None = None,
        tool_result_admission: ToolResultAdmissionAdapter | None = None,
    ) -> None:
        """Initialise the guard with a middleware, ledger, and optional event emitter."""
        self._middleware = middleware
        self._ledger = ledger
        self._run = run
        self._event_producer = event_producer
        # Resolved here rather than as a parameter default: a default is
        # bound once at import, so the limit could not be adjusted for a
        # single run (or a single test) after this module loaded.
        self._max_surfaced_rejections = (
            _Limits.MAX_SURFACED_REJECTIONS
            if max_surfaced_rejections is None
            else max_surfaced_rejections
        )
        self._surfaced_rejections = 0
        if (task_policy_controller is None) != (task_request_fingerprint is None):
            raise ValueError(
                "task_policy_controller and task_request_fingerprint must be provided together"
            )
        self._task_policy_controller = task_policy_controller
        self._task_request_fingerprint = task_request_fingerprint
        self._tool_result_admission = tool_result_admission

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

    def rejection_error(self, decision: ToolBudgetReject) -> Exception:
        """Return the exception to raise for a hard-cap rejection.

        The cap has already been enforced by the time this is called —
        the inner tool is not going to run either way. What is still in
        question is whether the *run* survives.

        The first :attr:`_Limits.MAX_SURFACED_REJECTIONS` refusals return
        a non-fatal :class:`ToolBudgetRejected`, which the error policy
        hands to the model as a tool result so it can finalize with the
        work it has already completed. Killing the run here instead
        would discard every tool result the run had accumulated while
        enforcing exactly the same spend — strictly worse for the user
        and contrary to the refusal message's own instruction to
        "finalize now".

        Past that allowance the model is answering refusals with more
        calls rather than an answer, so the guard escalates to the
        fatal :class:`BudgetExceeded` to stop the loop.
        """

        self._surfaced_rejections += 1
        exhausted = self._surfaced_rejections > self._max_surfaced_rejections
        _LOGGER.info(
            "tool_budget_rejected_fatal" if exhausted else "tool_budget_rejected",
            extra={
                "metadata": {
                    "tool_name": decision.tool_name or decision.budget.tool_name,
                    "kind": decision.kind,
                    "current": decision.current,
                    "limit": decision.limit,
                    "surfaced_rejections": self._surfaced_rejections,
                    "max_surfaced_rejections": self._max_surfaced_rejections,
                }
            },
        )
        if exhausted:
            return BudgetExceeded(decision.safe_message)
        return ToolBudgetRejected(decision.safe_message)

    def record_started(self, *, tool_name: str, estimated_input_tokens: int) -> str:
        """Open a ledger entry for an admitted call. Returns the call id."""

        call_id = uuid4().hex
        self._ledger.started(
            call_id,
            tool_name=tool_name,
            # Budget accounting counts this entry and NOT the one the
            # stream orchestrator opens for the same call under the
            # provider's own call id. Both writers share this ledger; if
            # both were counted every configured cap would be halved.
            budget_scoped=True,
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

    def usage_note(self, *, tool_name: str) -> str | None:
        """Return the remaining-calls note for ``tool_name``, or ``None``.

        ``None`` when no budget governs the tool, or while there is still
        comfortable headroom — the model only needs this once the tail is
        in sight, and a note on every result is context spent for nothing.
        """

        usage = self._middleware.usage(ledger=self._ledger, tool_name=tool_name)
        if usage is None or not usage.should_notify:
            return None
        return usage.render_note()

    def admit_tool_result(self, result: object, *, call_id: str) -> object:
        """Bound one successful result before LangChain creates a ToolMessage.

        The adapter is optional so existing runtime configurations preserve
        their exact return types and bytes.  Once configured, its failure is
        intentionally allowed to propagate: returning the raw result after an
        offload failure would violate the model-admission boundary.
        """

        adapter = self._tool_result_admission
        if adapter is None:
            return result
        return adapter.admit(
            result,
            trace_id=call_id,
            projection_key=self._run.run_id if self._run is not None else None,
        ).model_content

    def admit_task_policy(
        self,
        *,
        tool_name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> ToolUseIntent | None:
        """Admit an operation through the optional F4 duplicate controller.

        The hard tool-budget check still runs independently after this method.
        Any controller or canonicalization failure fails open to those existing
        hard gates; an optional quality signal must never become a new runtime
        availability dependency.
        """

        controller = self._task_policy_controller
        fingerprinter = self._task_request_fingerprint
        if controller is None or fingerprinter is None:
            return None
        try:
            fingerprint = fingerprinter.for_request(
                capability_id=tool_name,
                arguments={"args": list(args), "kwargs": kwargs},
            )
            intent = ToolUseIntent(
                operation_id=f"tool-policy-{uuid4().hex}",
                capability_id=tool_name,
                canonical_request_fingerprint=fingerprint,
            )
            feedback = controller.before_operation(intent)
        except Exception:  # noqa: BLE001 - optional controller fails open.
            _LOGGER.warning("task_policy_before_operation_failed", exc_info=True)
            return None
        if feedback.disposition is not ToolUseDisposition.CONTINUE:
            raise ToolPolicyRejected(self._task_policy_message(feedback.disposition))
        return intent

    def record_task_policy_outcome(
        self,
        *,
        intent: ToolUseIntent | None,
        succeeded: bool,
        error_class: str | None = None,
    ) -> None:
        """Best-effort fold of observable completion facts into F4 state."""

        controller = self._task_policy_controller
        if controller is None or intent is None:
            return
        try:
            controller.after_operation(
                ToolOperationOutcome(
                    operation_id=intent.operation_id,
                    capability_id=intent.capability_id,
                    succeeded=succeeded,
                    error_class=error_class,
                )
            )
        except Exception:  # noqa: BLE001 - must not alter a tool outcome.
            _LOGGER.warning("task_policy_after_operation_failed", exc_info=True)

    @staticmethod
    def _task_policy_message(disposition: ToolUseDisposition) -> str:
        if disposition is ToolUseDisposition.STOP:
            return (
                "This tool call is blocked by the active task policy. Do not repeat "
                "the same request; answer with the evidence already gathered and "
                "state what remains uncertain."
            )
        return (
            "This tool call duplicates prior work. Revise the plan or change the "
            "request before calling a tool again."
        )

    async def emit_warning(self, *, decision: ToolBudgetWarn) -> None:
        """Best-effort BUDGET_WARNING emission."""

        producer = self._event_producer
        run = self._run
        # The requested tool, falling back to the budget's own name for
        # decisions built before ``tool_name`` was threaded through.
        tool_name = decision.tool_name or decision.budget.tool_name
        if (
            producer is None
            or run is None
            or not callable(getattr(producer, "append_api_event", None))
        ):
            _LOGGER.info(
                "tool_budget_warn",
                extra={
                    "metadata": {
                        "tool_name": tool_name,
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
                    "tool_name": tool_name,
                    "kind": decision.kind,
                    "current": decision.current,
                    "limit": decision.limit,
                    "enforcement": decision.budget.enforcement.value,
                },
                summary=(
                    f"Tool '{tool_name}' near {decision.kind} cap "
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
        policy_intent = guard.admit_task_policy(
            tool_name=self.name, args=args, kwargs=kwargs
        )
        estimated = _Estimator.estimate(args, kwargs)
        decision = guard.check_admit(
            tool_name=self.name, estimated_input_tokens=estimated
        )
        if isinstance(decision, ToolBudgetReject):
            # The inner tool is short-circuited either way — the cap is
            # enforced here. The guard decides only whether the refusal
            # is surfaced to the model or ends the run.
            raise guard.rejection_error(decision)
        if isinstance(decision, ToolBudgetWarn):
            # Sync path: schedule warning emission on the running loop; fall back to log.
            self._schedule_warning(guard=guard, decision=decision)
        call_id = guard.record_started(
            tool_name=self.name, estimated_input_tokens=estimated
        )
        try:
            result = self.inner._run(*args, **kwargs)
        except BaseException as exc:
            guard.record_task_policy_outcome(
                intent=policy_intent,
                succeeded=False,
                error_class=type(exc).__name__,
            )
            raise
        else:
            guard.record_task_policy_outcome(intent=policy_intent, succeeded=True)
        finally:
            guard.record_settled(call_id=call_id, observed_input_tokens=estimated)
        return self._model_visible_result(result, guard=guard, call_id=call_id)

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        """Async gate: check budget, record the call, delegate to the inner tool."""
        guard = ToolBudgetGuard.active()
        if guard is None:
            return await self.inner._arun(*args, **kwargs)
        policy_intent = guard.admit_task_policy(
            tool_name=self.name, args=args, kwargs=kwargs
        )
        estimated = _Estimator.estimate(args, kwargs)
        decision = guard.check_admit(
            tool_name=self.name, estimated_input_tokens=estimated
        )
        if isinstance(decision, ToolBudgetReject):
            raise guard.rejection_error(decision)
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
            result = await self.inner._arun(*args, **kwargs)
        except BaseException as exc:
            guard.record_task_policy_outcome(
                intent=policy_intent,
                succeeded=False,
                error_class=type(exc).__name__,
            )
            raise
        else:
            guard.record_task_policy_outcome(intent=policy_intent, succeeded=True)
        finally:
            guard.record_settled(call_id=call_id, observed_input_tokens=estimated)
        # Settled first, so the count the model reads includes this call.
        return self._model_visible_result(result, guard=guard, call_id=call_id)

    def _model_visible_result(
        self,
        result: object,
        *,
        guard: ToolBudgetGuard,
        call_id: str,
    ) -> object:
        """Annotate, then bound, the exact value returned to LangChain."""

        annotated = self._with_usage_note(result, guard=guard)
        return guard.admit_tool_result(annotated, call_id=call_id)

    def _with_usage_note(self, result: object, *, guard: ToolBudgetGuard) -> object:
        """Append the remaining-calls note to ``result`` when one is due.

        Best-effort: annotating a result must never fail the tool call that
        already succeeded, so any failure here returns the result untouched.
        """

        try:
            note = guard.usage_note(tool_name=self.name)
            if note is None:
                return result
            return ToolResultNote.append(
                result, note=note, dict_key=_TOOL_BUDGET_NOTE_KEY
            )
        except Exception:  # noqa: BLE001 — an annotation is never worth a failure
            _LOGGER.warning("tool_budget_note_failed", exc_info=True)
            return result

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
        return guard_model_tools(rendered)

    @staticmethod
    def _wrap(tool: object) -> object:
        """Wrap ``tool`` in a ``ToolBudgetGuardedTool``; return non-BaseTool entries unchanged."""
        if not isinstance(tool, BaseTool):
            return tool
        if ToolBudgetGuardedRegistry._contains_guard(tool):
            return tool
        return ToolBudgetGuardedTool(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            inner=tool,
        )

    @staticmethod
    def _contains_guard(tool: BaseTool) -> bool:
        """Return whether a wrapper chain already contains the budget gate.

        Cross-cutting tool decorators compose through an ``inner`` attribute.
        Registry tools arrive as ``ErrorPolicy(Budget(tool))`` while tools
        injected later by the factory may arrive undecorated. Looking only at
        the outermost type therefore double-wrapped registry tools and placed a
        new budget gate outside their error policy. A hard rejection from that
        outer gate escaped the graph instead of becoming a model-visible tool
        result.

        Walk the wrapper chain defensively so the complete-surface pass remains
        idempotent regardless of which other decorators are already present.
        """

        current: object = tool
        seen: set[int] = set()
        while isinstance(current, BaseTool) and id(current) not in seen:
            if isinstance(current, ToolBudgetGuardedTool):
                return True
            seen.add(id(current))
            current = getattr(current, "inner", None)
        return False


def guard_model_tools(tools: tuple[object, ...] | list[object]) -> tuple[object, ...]:
    """Wrap a complete model-visible tool surface exactly once.

    Registries are only one source of tools. The factory appends MCP, skills,
    prior-result, question, and desktop capability tools afterwards; calling
    this function after final assembly is therefore the only topology that can
    truthfully guarantee one hard budget gate per model-visible tool.
    """

    return tuple(ToolBudgetGuardedRegistry._wrap(tool) for tool in tools)


# Optional callable signature for callers that want to build a guard
# from a budget-loading function (e.g. the run handler).
ToolBudgetSnapshotLoader = Callable[[str], Awaitable[list[object]]]


__all__ = (
    "ToolBudgetGuard",
    "ToolBudgetGuardedRegistry",
    "ToolBudgetGuardedTool",
    "guard_model_tools",
)
