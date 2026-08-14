"""Route one program step through the graph's own tool-execution seam.

This module exists to make one property structural rather than remembered: a
batched step is enforced by **the same code** a directly-called tool is, not by
a second implementation that happens to look similar.

``RuntimeControlMiddleware.awrap_tool_call`` is that code. Every model-visible
tool call in a compiled graph goes through it, and on the way it binds the call
identity, admits the call through the tool-use policy controller, charges and
settles the per-run tool budget, records the task-policy outcome, and bounds the
result before it can enter model context
(:mod:`agent_runtime.context.tool_result_admission`). A step dispatched by
calling ``tool.ainvoke`` directly — which is what the seam the interpreter
bridge uses does — would skip every one of those. So this dispatcher does not
call the tool; it builds the same :class:`ToolCallRequest` the graph would and
hands it to the same middleware method, with a handler that finally invokes the
tool. Nothing about admission, budget or the result cap is re-implemented here,
which is why a batch cannot drift away from a direct call.

Two consequences worth stating out loud:

* The recorded step output is the **admitted** content, not the tool's raw
  return value. If the cap truncated or offloaded an oversized result, later
  steps reference what the model would have seen, not what the tool produced.
  Recording the raw value instead would make the cap decorative inside a batch.
* A step whose own pipeline demands human approval raises LangGraph's
  ``GraphBubbleUp`` from inside the handler. It is re-raised here untouched; the
  executor owns that decision, because it is the only thing that knows which
  steps already ran.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import uuid4

from langchain_core.messages import ToolMessage

from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeControlMiddleware,
)
from agent_runtime.delegation.subagents.operation_identity import (
    SUPERVISOR_TASK_CALL_ID_KEY,
)
from agent_runtime.execution.call_identity import RuntimeCallContext
from agent_runtime.execution.contracts import JsonValue, RuntimeContract

_SUBAGENT_SCOPE_PREFIX = "subagent:"
_SUPERVISOR_SCOPE = "supervisor"


class StepDispatchStatus(StrEnum):
    """How the shared tool seam settled one step.

    ``REFUSED`` and ``FAILED`` are kept apart because they mean different things
    to a model reading the result: refused means the runtime declined the call
    and **the tool never ran**, failed means the tool ran and raised.
    """

    COMPLETED = "completed"
    REFUSED = "refused"
    FAILED = "failed"


class StepDispatchOutcome(RuntimeContract):
    """One step's settled result as the shared seam left it."""

    status: StepDispatchStatus
    output: JsonValue = None
    safe_message: str | None = None


@runtime_checkable
class ToolProgramStepDispatcher(Protocol):
    """The executor's only route to a tool.

    :class:`~agent_runtime.capabilities.tool_program.executor.ToolProgramExecutor`
    is constructed with one of these and nothing else that can reach a tool — no
    tool map, no connector client, no dispatcher of its own. Substituting a
    dispatcher is therefore the only way to change what a step can do, and the
    single production implementation below is the enforced one.
    """

    async def dispatch(
        self,
        *,
        step_id: str,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> StepDispatchOutcome: ...


class ProgramStepCallRuntime:
    """Re-presents the outer call's execution scope where the middleware reads it.

    The middleware resolves a call's execution scope from
    ``runtime.config["metadata"][SUPERVISOR_TASK_CALL_ID_KEY]``. A program step
    is dispatched from inside a tool node, where no LangGraph runtime object is
    in hand, so the scope is taken from the already-bound outer call identity
    and handed back in exactly the shape the middleware expects. Without this,
    every step a subagent batched would be accounted against the supervisor.
    """

    __slots__ = ("config",)

    def __init__(self, *, task_call_id: str) -> None:
        self.config = {"metadata": {SUPERVISOR_TASK_CALL_ID_KEY: task_call_id}}

    @classmethod
    def for_current_call(cls) -> "ProgramStepCallRuntime | None":
        """Return the runtime stub for the active scope, or ``None`` at supervisor."""

        identity = RuntimeCallContext.current()
        scope = identity.execution_scope if identity is not None else _SUPERVISOR_SCOPE
        if not scope.startswith(_SUBAGENT_SCOPE_PREFIX):
            return None
        task_call_id = scope[len(_SUBAGENT_SCOPE_PREFIX) :]
        if not task_call_id:
            return None
        return cls(task_call_id=task_call_id)


class MiddlewareStepDispatcher:
    """The production dispatcher: one step, one trip through the graph's seam.

    ``tools_by_name`` is the run's already scope-filtered, model-visible toolset.
    A name absent from it is refused before any middleware work, which is the
    same answer the graph gives a hallucinated tool name.
    """

    #: Content handed to the model when a tool raises. The middleware's own
    #: rejection path writes its own message; this one covers the tool itself.
    TOOL_FAILED_MESSAGE = "the tool did not complete"
    TOOL_UNAVAILABLE_MESSAGE = "the tool is not available to this run"

    def __init__(
        self,
        *,
        tools_by_name: Mapping[str, object],
        middleware: RuntimeControlMiddleware | None = None,
    ) -> None:
        self._tools = dict(tools_by_name)
        # Only used where no verified run is bound (isolated tests). In
        # production every step is named after the program call that asked for
        # it, which is what makes a replayed run derive the same step ids.
        self._fallback_prefix = f"tool-program:{uuid4().hex}"
        # A fresh instance is deliberate and safe: the middleware holds no
        # per-run state of its own. Its lifecycle reducer and budget guard are
        # both read from the run-scoped context at call time, so this instance
        # writes to the same ledger the graph's own instance does.
        self._middleware = middleware or RuntimeControlMiddleware()

    async def dispatch(
        self,
        *,
        step_id: str,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> StepDispatchOutcome:
        """Run one step through ``awrap_tool_call`` and read back its outcome."""

        tool = self._tools.get(tool_name)
        if tool is None or not callable(getattr(tool, "ainvoke", None)):
            return StepDispatchOutcome(
                status=StepDispatchStatus.REFUSED,
                safe_message=self.TOOL_UNAVAILABLE_MESSAGE,
            )
        call_id = self.call_id(step_id)
        request = self._request(
            tool=tool, tool_name=tool_name, arguments=arguments, call_id=call_id
        )
        attempt = _ToolAttempt(call_id=call_id, tool_name=tool_name)
        result = await self._middleware.awrap_tool_call(request, attempt.handle)
        return self._settle(result, attempt=attempt)

    def call_id(self, step_id: str) -> str:
        """Stable per-step call id: replay-safe and unique inside the program.

        Derived from the *outer* program call's own control id, which is itself
        derived from the run snapshot and the model's tool-call id — so a
        replayed run allocates a step the same identity it had the first time.
        """

        identity = RuntimeCallContext.current()
        prefix = (
            identity.control_call_id if identity is not None else self._fallback_prefix
        )
        return f"{prefix}:{step_id}"

    @staticmethod
    def _request(
        *,
        tool: object,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
        call_id: str,
    ) -> object:
        from langchain.agents.middleware.types import ToolCallRequest  # noqa: PLC0415

        identity = RuntimeCallContext.current()
        return ToolCallRequest(
            tool_call={
                "name": tool_name,
                "args": dict(arguments),
                "id": call_id,
                "type": "tool_call",
            },
            tool=tool,  # type: ignore[arg-type]
            # The middleware reads exactly one key out of state, and reads the
            # scope out of the runtime; both are taken from the outer call so a
            # step is accounted where the program itself is.
            state={
                "runtime_control_model_turn": (
                    identity.model_turn if identity is not None else 1
                )
            },
            runtime=ProgramStepCallRuntime.for_current_call(),  # type: ignore[arg-type]
        )

    def _settle(
        self, result: object, *, attempt: "_ToolAttempt"
    ) -> StepDispatchOutcome:
        """Read the middleware's ToolMessage back into a typed step outcome."""

        message = self._tool_message(result, call_id=attempt.call_id)
        if message is None:
            # The seam returned something that is not this call's ToolMessage
            # (a Command-shaped state update). A program step must produce a
            # referenceable value, and a state mutation is not one.
            return StepDispatchOutcome(
                status=StepDispatchStatus.REFUSED,
                safe_message="the tool does not return a value a program can use",
            )
        content = message.content
        if message.status == "error":
            return StepDispatchOutcome(
                status=(
                    StepDispatchStatus.FAILED
                    if attempt.admitted
                    else StepDispatchStatus.REFUSED
                ),
                safe_message=self._text(content),
            )
        return StepDispatchOutcome(
            status=StepDispatchStatus.COMPLETED,
            output=self._decoded(content),
        )

    @staticmethod
    def _tool_message(result: object, *, call_id: str) -> ToolMessage | None:
        if isinstance(result, ToolMessage):
            return result if result.tool_call_id == call_id else None
        if isinstance(result, list):
            for item in result:
                if isinstance(item, ToolMessage) and item.tool_call_id == call_id:
                    return item
        return None

    @classmethod
    def _text(cls, content: object) -> str:
        return content if isinstance(content, str) else json.dumps(content, default=str)

    @classmethod
    def _decoded(cls, content: object) -> JsonValue:
        """Record a step's output in the shape later steps can address.

        Tools in this runtime overwhelmingly return a serialized JSON document
        as a string. Recording that string verbatim would make every structural
        reference into it unresolvable, so a string that parses as a JSON object
        or array is recorded parsed. Anything else is recorded exactly as the
        admission boundary left it — no coercion, no reshaping.
        """

        if not isinstance(content, str):
            return content  # type: ignore[return-value]
        try:
            decoded = json.loads(content)
        except (TypeError, ValueError):
            return content
        return decoded if isinstance(decoded, (dict, list)) else content


class _ToolAttempt:
    """The handler the middleware wraps, plus whether it was ever reached.

    The distinction matters for honesty: the middleware turns a budget or
    policy refusal into an error ``ToolMessage`` that looks exactly like a tool
    that raised. Only the handler knows which happened — it is called if and
    only if the seam admitted the call — so it records that.
    """

    def __init__(self, *, call_id: str, tool_name: str) -> None:
        self.call_id = call_id
        self.tool_name = tool_name
        self.admitted = False

    async def handle(self, request: object) -> ToolMessage:
        """Invoke the authorized tool, never leaking its traceback."""

        tool = request.tool  # type: ignore[attr-defined]
        arguments = dict(request.tool_call.get("args", {}))  # type: ignore[attr-defined]
        self.admitted = True
        try:
            value = await tool.ainvoke(arguments)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a tool traceback never reaches a model
            return ToolMessage(
                content=MiddlewareStepDispatcher.TOOL_FAILED_MESSAGE,
                tool_call_id=self.call_id,
                name=self.tool_name,
                status="error",
            )
        return ToolMessage(
            content=(
                value if isinstance(value, str) else json.dumps(value, default=str)
            ),
            tool_call_id=self.call_id,
            name=self.tool_name,
        )


__all__ = (
    "MiddlewareStepDispatcher",
    "ProgramStepCallRuntime",
    "StepDispatchOutcome",
    "StepDispatchStatus",
    "ToolProgramStepDispatcher",
)
