"""Graph-wide tool admission, result bounding, and serial-default execution.

Deep Agents adds todo, filesystem, execute, and task tools after the caller's
tool list is assembled. A ``BaseTool`` decorator therefore cannot be the
authoritative model/tool boundary. This LangChain middleware runs around every
tool exposed by the completed graph, including framework-injected tools and the
same tools inside locally compiled Deep Agents subagents.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from threading import Lock
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from agent_runtime.capabilities.tool_budget_guard import (
    ToolBudgetGuard,
    estimate_tool_input_tokens,
)
from agent_runtime.capabilities.tool_budget_middleware import (
    ToolBudgetAdmit,
    ToolBudgetReject,
    ToolBudgetWarn,
)
from agent_runtime.capabilities.tools.tool_use_enforcement import PolicyBlockedTool
from agent_runtime.execution.tool_errors import BudgetExceeded
from agent_runtime.execution.tool_errors import ToolBudgetRejected
from agent_runtime.execution.tool_error_policy import DefaultToolErrorPolicy


ToolHandlerItem = ToolMessage | Command[Any]
ToolHandlerResult = ToolHandlerItem | list[ToolHandlerItem]
ToolHandler = Callable[[ToolCallRequest], ToolHandlerResult]
AsyncToolHandler = Callable[
    [ToolCallRequest],
    Awaitable[ToolHandlerResult],
]


class RuntimeToolControlMiddleware(AgentMiddleware):
    """Enforce F4/F5 controls and conservative F6 ordering on the final graph."""

    name = "0xCopilotRuntimeToolControlMiddleware"

    def __init__(self) -> None:
        # One middleware instance is materialized per compiled agent. LangChain
        # may submit siblings from one model response concurrently; these locks
        # make the default schedule serial until a persisted F6 permit says
        # otherwise. The async and sync paths are intentionally separate.
        self._async_serial_gate = asyncio.Lock()
        self._sync_serial_gate = Lock()

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: ToolHandler,
    ) -> ToolHandlerResult:
        """Synchronously execute one graph-visible tool under the common gate."""

        with self._sync_serial_gate:
            if isinstance(request.tool, PolicyBlockedTool):
                # User policy is the outer admission boundary. A blocked call
                # returns its fixed safe message without consuming run budget.
                return handler(request)
            return self._execute(request=request, handler=handler)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: AsyncToolHandler,
    ) -> ToolHandlerResult:
        """Asynchronously execute one graph-visible tool under the common gate."""

        async with self._async_serial_gate:
            if isinstance(request.tool, PolicyBlockedTool):
                # Keep parity with the synchronous path and the normative
                # policy → budget → execution ordering.
                return await handler(request)
            return await self._aexecute(request=request, handler=handler)

    @staticmethod
    def _execute(
        *,
        request: ToolCallRequest,
        handler: ToolHandler,
    ) -> ToolHandlerResult:
        guard = ToolBudgetGuard.active()
        if guard is None:
            return handler(request)
        tool_name, arguments, estimated = _request_facts(request)
        try:
            intent = guard.admit_task_policy(
                tool_name=tool_name,
                args=(),
                kwargs=arguments,
            )
        except ToolBudgetRejected as exc:
            return _surface_rejection(exc, request=request)
        decision = guard.check_admit(
            tool_name=tool_name,
            estimated_input_tokens=estimated,
        )
        if isinstance(decision, ToolBudgetReject):
            rejection = guard.rejection_error(decision)
            if isinstance(rejection, ToolBudgetRejected):
                return _surface_rejection(rejection, request=request)
            raise rejection
        if isinstance(decision, ToolBudgetWarn):
            _schedule_warning(guard=guard, decision=decision)
        if not isinstance(decision, (ToolBudgetAdmit, ToolBudgetWarn)):
            raise BudgetExceeded("Tool call was not admitted by runtime middleware.")
        call_id = guard.record_started(
            tool_name=tool_name,
            estimated_input_tokens=estimated,
        )
        try:
            result = handler(request)
        except BaseException as exc:
            guard.record_task_policy_outcome(
                intent=intent,
                succeeded=False,
                error_class=type(exc).__name__,
            )
            raise
        else:
            succeeded = _succeeded(result)
            guard.record_task_policy_outcome(
                intent=intent,
                succeeded=succeeded,
                error_class=None if succeeded else "ToolMessageError",
            )
        finally:
            guard.record_settled(
                call_id=call_id,
                observed_input_tokens=estimated,
            )
        return _admit_result(
            result,
            guard=guard,
            tool_name=tool_name,
            call_id=call_id,
            tool_call_id=str(request.tool_call["id"]),
        )

    @staticmethod
    async def _aexecute(
        *,
        request: ToolCallRequest,
        handler: AsyncToolHandler,
    ) -> ToolHandlerResult:
        guard = ToolBudgetGuard.active()
        if guard is None:
            return await handler(request)
        tool_name, arguments, estimated = _request_facts(request)
        try:
            intent = guard.admit_task_policy(
                tool_name=tool_name,
                args=(),
                kwargs=arguments,
            )
        except ToolBudgetRejected as exc:
            return _surface_rejection(exc, request=request)
        decision = guard.check_admit(
            tool_name=tool_name,
            estimated_input_tokens=estimated,
        )
        if isinstance(decision, ToolBudgetReject):
            rejection = guard.rejection_error(decision)
            if isinstance(rejection, ToolBudgetRejected):
                return _surface_rejection(rejection, request=request)
            raise rejection
        if isinstance(decision, ToolBudgetWarn):
            await guard.emit_warning(decision=decision)
        if not isinstance(decision, (ToolBudgetAdmit, ToolBudgetWarn)):
            raise BudgetExceeded("Tool call was not admitted by runtime middleware.")
        call_id = guard.record_started(
            tool_name=tool_name,
            estimated_input_tokens=estimated,
        )
        try:
            result = await handler(request)
        except BaseException as exc:
            guard.record_task_policy_outcome(
                intent=intent,
                succeeded=False,
                error_class=type(exc).__name__,
            )
            raise
        else:
            succeeded = _succeeded(result)
            guard.record_task_policy_outcome(
                intent=intent,
                succeeded=succeeded,
                error_class=None if succeeded else "ToolMessageError",
            )
        finally:
            guard.record_settled(
                call_id=call_id,
                observed_input_tokens=estimated,
            )
        return _admit_result(
            result,
            guard=guard,
            tool_name=tool_name,
            call_id=call_id,
            tool_call_id=str(request.tool_call["id"]),
        )


def _request_facts(request: ToolCallRequest) -> tuple[str, dict[str, Any], int]:
    tool_name = str(request.tool_call.get("name", "")).strip()
    if not tool_name:
        raise BudgetExceeded("Tool call is missing its registered tool name.")
    raw_arguments = request.tool_call.get("args", {})
    arguments = (
        dict(raw_arguments)
        if isinstance(raw_arguments, Mapping)
        else {"input": raw_arguments}
    )
    return (tool_name, arguments, estimate_tool_input_tokens(arguments))


def _surface_rejection(
    rejection: ToolBudgetRejected,
    *,
    request: ToolCallRequest,
) -> ToolMessage:
    tool = request.tool
    content = (
        DefaultToolErrorPolicy().classify(rejection, tool=tool).to_llm_message_content()
        if tool is not None
        else rejection.safe_summary
    )
    return ToolMessage(
        content=content,
        tool_call_id=str(request.tool_call["id"]),
        name=str(request.tool_call.get("name", "")) or None,
        status="error",
    )


def _schedule_warning(*, guard: ToolBudgetGuard, decision: ToolBudgetWarn) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(guard.emit_warning(decision=decision))


def _succeeded(result: ToolHandlerResult) -> bool:
    messages = _tool_messages(result)
    return not messages or all(message.status != "error" for message in messages)


def _admit_result(
    result: ToolHandlerResult,
    *,
    guard: ToolBudgetGuard,
    tool_name: str,
    call_id: str,
    tool_call_id: str,
) -> ToolHandlerResult:
    def admit(message: ToolMessage) -> ToolMessage:
        if message.tool_call_id != tool_call_id:
            return message
        content = guard.admit_model_visible_result(
            message.content,
            tool_name=tool_name,
            call_id=call_id,
        )
        return message.model_copy(update={"content": content})

    if isinstance(result, list):
        return [
            _admit_result(
                item,
                guard=guard,
                tool_name=tool_name,
                call_id=call_id,
                tool_call_id=tool_call_id,
            )
            for item in result
        ]
    if isinstance(result, ToolMessage):
        return admit(result)
    update = result.update
    if not isinstance(update, Mapping) or "messages" not in update:
        return result
    messages = update["messages"]
    if isinstance(messages, ToolMessage):
        admitted_messages: ToolMessage | list[object] = admit(messages)
    elif isinstance(messages, Sequence) and not isinstance(
        messages,
        (str, bytes, bytearray),
    ):
        admitted_messages = [
            admit(message) if isinstance(message, ToolMessage) else message
            for message in messages
        ]
    else:
        return result
    return replace(
        result,
        update={
            **update,
            "messages": admitted_messages,
        },
    )


def _tool_messages(result: ToolHandlerResult) -> tuple[ToolMessage, ...]:
    if isinstance(result, list):
        return tuple(message for item in result for message in _tool_messages(item))
    if isinstance(result, ToolMessage):
        return (result,)
    update = result.update
    if not isinstance(update, Mapping):
        return ()
    messages = update.get("messages")
    if isinstance(messages, ToolMessage):
        return (messages,)
    if isinstance(messages, Sequence) and not isinstance(
        messages,
        (str, bytes, bytearray),
    ):
        return tuple(
            message for message in messages if isinstance(message, ToolMessage)
        )
    return ()


__all__ = ("RuntimeToolControlMiddleware",)
