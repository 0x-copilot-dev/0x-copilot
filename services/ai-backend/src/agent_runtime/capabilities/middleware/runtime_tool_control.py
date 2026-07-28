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
from dataclasses import dataclass
from dataclasses import replace
from typing import Annotated, Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    AgentState,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt
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
from agent_runtime.control_plane.context import (
    RunControlContext,
    RunSerialAdmission,
    RuntimeToolControlOutcome,
    RuntimeToolLifecycleReducer,
)
from agent_runtime.execution.tool_errors import BudgetExceeded
from agent_runtime.execution.tool_errors import ToolBudgetRejected
from agent_runtime.execution.tool_error_policy import DefaultToolErrorPolicy
from agent_runtime.execution.tool_surface import (
    DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES,
)
from agent_runtime.execution.call_identity import (
    RuntimeCallContext,
    RuntimeToolCallIdentity,
)
from agent_runtime.delegation.subagents.operation_identity import (
    SUPERVISOR_TASK_CALL_ID_KEY,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256


ToolHandlerItem = ToolMessage | Command[Any]
ToolHandlerResult = ToolHandlerItem | list[ToolHandlerItem]
ToolHandler = Callable[[ToolCallRequest], ToolHandlerResult]
AsyncToolHandler = Callable[
    [ToolCallRequest],
    Awaitable[ToolHandlerResult],
]


class RuntimeModelTurnReducer:
    """Checkpoint-safe monotonic reducer for the current model turn."""

    @staticmethod
    def reduce(current: int, update: int) -> int:
        """Keep the greatest observed turn across replayed node updates."""

        return max(current, update)


class RuntimeControlState(AgentState, total=False):
    """Private checkpointed state contributed by the runtime middleware."""

    runtime_control_model_turn: Annotated[
        int,
        RuntimeModelTurnReducer.reduce,
        PrivateStateAttr,
    ]


@dataclass(frozen=True)
class RuntimeToolSurfaceSnapshot:
    """Content-free canary observation of the final model-visible tools."""

    tool_names: tuple[str, ...]
    surface_digest: str

    @classmethod
    def from_tools(cls, tools: Sequence[object]) -> "RuntimeToolSurfaceSnapshot":
        """Enumerate the exact post-assembly tool names in model order."""

        names = tuple(str(getattr(tool, "name", "")).strip() for tool in tools)
        if any(not name for name in names):
            raise RuntimeError("final model-visible tool surface has an unnamed tool")
        if len(names) != len(set(names)):
            raise RuntimeError("final model-visible tool surface has duplicate names")
        return cls(
            tool_names=names,
            surface_digest=canonical_json_sha256({"tool_names": list(names)}),
        )


class RuntimeControlMiddleware(AgentMiddleware):
    """Canonical model/tool seam for one compiled Deep Agents graph.

    The async lifecycle hooks are intentionally present even where a later
    F-series step owns the behavior. This pins one supported composition point
    for prompt/model planning, tool-group planning, graph-wide tool control,
    and final observations without patching LangGraph nodes.
    """

    name = "0xCopilotRuntimeControlMiddleware"
    state_schema = RuntimeControlState

    def __init__(
        self,
        *,
        excluded_tool_names: frozenset[str] = (DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES),
    ) -> None:
        # Legacy/test graphs may not have a verified run-control binding. Their
        # fallback stays instance-local; production uses the one run-scoped
        # admission object inherited by supervisor and local subagents.
        self._excluded_tool_names = frozenset(excluded_tool_names)
        self._fallback_serial_admission = RunSerialAdmission()
        self._fallback_lifecycle_reducer = RuntimeToolLifecycleReducer()
        self._final_tool_surface: RuntimeToolSurfaceSnapshot | None = None

    @property
    def final_tool_surface(self) -> RuntimeToolSurfaceSnapshot | None:
        """Return the latest post-assembly tool-surface canary observation."""

        return self._final_tool_surface

    def before_model(
        self,
        state: RuntimeControlState,
        runtime: object,
    ) -> dict[str, Any]:
        """Synchronous compatibility adapter for the canonical async hook."""

        return self._before_model_update(state, runtime)

    async def abefore_model(
        self,
        state: RuntimeControlState,
        runtime: object,
    ) -> dict[str, Any]:
        """Advance one checkpointed model turn before provider dispatch."""

        model_turn = self._model_turn(state) + 1
        guard = ToolBudgetGuard.active()
        admit_model_turn = (
            getattr(guard, "aadmit_model_turn", None) if guard is not None else None
        )
        if callable(admit_model_turn):
            await admit_model_turn(
                model_turn=model_turn,
                execution_scope=self._execution_scope_for_runtime(runtime),
            )
        return {"runtime_control_model_turn": model_turn}

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """Synchronous compatibility adapter with identical observation."""

        provider_request = self._provider_visible_request(request)
        self._observe_final_tool_surface(provider_request)
        return handler(provider_request)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Apply reviewed exclusions, observe the surface, then delegate."""

        provider_request = self._provider_visible_request(request)
        self._observe_final_tool_surface(provider_request)
        return await handler(provider_request)

    def after_model(
        self,
        state: RuntimeControlState,
        runtime: object,
    ) -> None:
        """Synchronous compatibility adapter for the reserved seam."""

        del state, runtime
        return None

    async def aafter_model(
        self,
        state: RuntimeControlState,
        runtime: object,
    ) -> None:
        """Reserved supported seam for Step 6 tool-group planning."""

        del state, runtime
        return None

    def after_agent(
        self,
        state: RuntimeControlState,
        runtime: object,
    ) -> None:
        """Synchronous compatibility adapter for completion observation."""

        del state, runtime
        return None

    async def aafter_agent(
        self,
        state: RuntimeControlState,
        runtime: object,
    ) -> None:
        """Reserved content-free completion observation seam."""

        del state, runtime
        return None

    def _before_model_update(
        self,
        state: RuntimeControlState,
        runtime: object,
    ) -> dict[str, Any]:
        model_turn = self._model_turn(state) + 1
        guard = ToolBudgetGuard.active()
        admit_model_turn = (
            getattr(guard, "admit_model_turn", None) if guard is not None else None
        )
        if callable(admit_model_turn):
            admit_model_turn(
                model_turn=model_turn,
                execution_scope=self._execution_scope_for_runtime(runtime),
            )
        return {
            "runtime_control_model_turn": model_turn,
        }

    def _observe_final_tool_surface(self, request: ModelRequest[Any]) -> None:
        self._final_tool_surface = RuntimeToolSurfaceSnapshot.from_tools(
            request.tools or []
        )

    def _provider_visible_request(
        self,
        request: ModelRequest[Any],
    ) -> ModelRequest[Any]:
        """Apply the reviewed profile exclusion at the supported model seam."""

        tools = list(request.tools or [])
        provider_tools = [
            tool
            for tool in tools
            if str(getattr(tool, "name", "")).strip() not in self._excluded_tool_names
        ]
        if len(provider_tools) == len(tools):
            return request
        return request.override(tools=provider_tools)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: ToolHandler,
    ) -> ToolHandlerResult:
        """Synchronously execute one graph-visible tool under the common gate."""

        admission = (
            RunControlContext.serial_admission() or self._fallback_serial_admission
        )
        with admission.sync_permit():
            identity = self._call_identity(request)
            with RuntimeCallContext.bind(identity):
                execute = (
                    (
                        lambda: self._execute_policy_blocked(
                            request=request,
                            handler=handler,
                        )
                    )
                    if isinstance(request.tool, PolicyBlockedTool)
                    else (lambda: self._execute(request=request, handler=handler))
                )
                return self._observe_sync_tool_lifecycle(
                    request=request,
                    identity=identity,
                    execute=execute,
                )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: AsyncToolHandler,
    ) -> ToolHandlerResult:
        """Asynchronously execute one graph-visible tool under the common gate."""

        admission = (
            RunControlContext.serial_admission() or self._fallback_serial_admission
        )
        async with admission.async_permit():
            identity = self._call_identity(request)
            with RuntimeCallContext.bind(identity):

                async def execute() -> ToolHandlerResult:
                    if isinstance(request.tool, PolicyBlockedTool):
                        # User policy is the outer rejection gate. A blocked
                        # call never reaches budget admission.
                        await self._aobserve_upstream_policy_block(request)
                        return await handler(request)
                    return await self._aexecute(request=request, handler=handler)

                return await self._observe_async_tool_lifecycle(
                    request=request,
                    identity=identity,
                    execute=execute,
                )

    def _observe_sync_tool_lifecycle(
        self,
        *,
        request: ToolCallRequest,
        identity: RuntimeToolCallIdentity | None,
        execute: Callable[[], ToolHandlerResult],
    ) -> ToolHandlerResult:
        reducer, attempt_id = self._open_lifecycle(request, identity)
        try:
            result = execute()
        except GraphInterrupt:
            self._settle_lifecycle(
                reducer,
                identity=identity,
                attempt_id=attempt_id,
                outcome=RuntimeToolControlOutcome.INTERRUPT,
            )
            raise
        except asyncio.CancelledError:
            self._settle_lifecycle(
                reducer,
                identity=identity,
                attempt_id=attempt_id,
                outcome=RuntimeToolControlOutcome.CANCELLED,
            )
            raise
        except BaseException:
            self._settle_lifecycle(
                reducer,
                identity=identity,
                attempt_id=attempt_id,
                outcome=RuntimeToolControlOutcome.ERROR,
            )
            raise
        self._settle_lifecycle(
            reducer,
            identity=identity,
            attempt_id=attempt_id,
            outcome=self._result_outcome(
                result,
                policy_blocked=isinstance(
                    request.tool,
                    PolicyBlockedTool,
                ),
            ),
        )
        return result

    async def _observe_async_tool_lifecycle(
        self,
        *,
        request: ToolCallRequest,
        identity: RuntimeToolCallIdentity | None,
        execute: Callable[[], Awaitable[ToolHandlerResult]],
    ) -> ToolHandlerResult:
        reducer, attempt_id = self._open_lifecycle(request, identity)
        try:
            result = await execute()
        except GraphInterrupt:
            self._settle_lifecycle(
                reducer,
                identity=identity,
                attempt_id=attempt_id,
                outcome=RuntimeToolControlOutcome.INTERRUPT,
            )
            raise
        except asyncio.CancelledError:
            self._settle_lifecycle(
                reducer,
                identity=identity,
                attempt_id=attempt_id,
                outcome=RuntimeToolControlOutcome.CANCELLED,
            )
            raise
        except BaseException:
            self._settle_lifecycle(
                reducer,
                identity=identity,
                attempt_id=attempt_id,
                outcome=RuntimeToolControlOutcome.ERROR,
            )
            raise
        self._settle_lifecycle(
            reducer,
            identity=identity,
            attempt_id=attempt_id,
            outcome=self._result_outcome(
                result,
                policy_blocked=isinstance(
                    request.tool,
                    PolicyBlockedTool,
                ),
            ),
        )
        return result

    def _open_lifecycle(
        self,
        request: ToolCallRequest,
        identity: RuntimeToolCallIdentity | None,
    ) -> tuple[RuntimeToolLifecycleReducer | None, str | None]:
        if identity is None:
            return (None, None)
        reducer = (
            RunControlContext.lifecycle_reducer() or self._fallback_lifecycle_reducer
        )
        attempt_id = self._attempt_id(request)
        reducer.observe_open(
            control_call_id=identity.control_call_id,
            attempt_id=attempt_id,
        )
        return (reducer, attempt_id)

    @staticmethod
    def _settle_lifecycle(
        reducer: RuntimeToolLifecycleReducer | None,
        *,
        identity: RuntimeToolCallIdentity | None,
        attempt_id: str | None,
        outcome: RuntimeToolControlOutcome,
    ) -> None:
        if reducer is None or identity is None or attempt_id is None:
            return
        reducer.observe_terminal(
            control_call_id=identity.control_call_id,
            attempt_id=attempt_id,
            operation_id=identity.operation_id,
            execution_scope=identity.execution_scope,
            outcome=outcome,
        )

    @classmethod
    def _attempt_id(cls, request: ToolCallRequest) -> str:
        execution_info = getattr(request.runtime, "execution_info", None)
        facts: dict[str, object] = {}
        if execution_info is not None:
            for key in (
                "checkpoint_id",
                "checkpoint_ns",
                "task_id",
                "node_attempt",
            ):
                value = getattr(execution_info, key, None)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    facts[key] = value
        if not facts:
            config = getattr(request.runtime, "config", None)
            if isinstance(config, Mapping):
                configurable = config.get("configurable")
                metadata = config.get("metadata")
                for key in ("checkpoint_id", "checkpoint_ns"):
                    if isinstance(configurable, Mapping):
                        value = configurable.get(key)
                        if isinstance(value, str) and value:
                            facts[key] = value
                for key in (
                    "langgraph_checkpoint_ns",
                    "langgraph_step",
                    "langgraph_task_idx",
                ):
                    if isinstance(metadata, Mapping):
                        value = metadata.get(key)
                        if isinstance(value, (str, int)) and not isinstance(
                            value,
                            bool,
                        ):
                            facts[key] = value
        if not facts:
            facts["compatibility_attempt"] = 1
        return "runtime-attempt:" + canonical_json_sha256(
            {"framework_execution": facts}
        )

    @staticmethod
    def _result_outcome(
        result: ToolHandlerResult,
        *,
        policy_blocked: bool,
    ) -> RuntimeToolControlOutcome:
        if policy_blocked:
            return RuntimeToolControlOutcome.ERROR
        if isinstance(result, Command) or (
            isinstance(result, list)
            and any(isinstance(item, Command) for item in result)
        ):
            return RuntimeToolControlOutcome.COMMAND
        if not _succeeded(result):
            return RuntimeToolControlOutcome.ERROR
        return RuntimeToolControlOutcome.SUCCESS

    @classmethod
    def _call_identity(
        cls,
        request: ToolCallRequest,
    ) -> RuntimeToolCallIdentity | None:
        tool_call_id = str(request.tool_call.get("id", "")).strip()
        if not tool_call_id:
            raise BudgetExceeded("Tool call is missing its model call id.")
        return RuntimeToolCallIdentity.from_current(
            execution_scope=cls._execution_scope(request),
            model_turn=max(cls._model_turn(request.state), 1),
            model_tool_call_id=tool_call_id,
        )

    @staticmethod
    def _execution_scope(request: ToolCallRequest) -> str:
        return RuntimeControlMiddleware._execution_scope_for_runtime(request.runtime)

    @staticmethod
    def _execution_scope_for_runtime(runtime: object) -> str:
        config = getattr(runtime, "config", None)
        if not isinstance(config, Mapping):
            return "supervisor"
        metadata = config.get("metadata")
        configurable = config.get("configurable")
        for container in (metadata, configurable):
            if not isinstance(container, Mapping):
                continue
            value = container.get(SUPERVISOR_TASK_CALL_ID_KEY)
            if isinstance(value, str) and value.strip():
                return f"subagent:{value.strip()}"
        return "supervisor"

    @staticmethod
    def _observe_upstream_policy_block(request: ToolCallRequest) -> None:
        guard = ToolBudgetGuard.active()
        if guard is None:
            return
        observer = getattr(guard, "observe_upstream_policy_block", None)
        if not callable(observer):
            return
        tool_name, arguments, _ = _request_facts(request)
        observer(
            tool_name=tool_name,
            args=(),
            kwargs=arguments,
        )

    @staticmethod
    async def _aobserve_upstream_policy_block(request: ToolCallRequest) -> None:
        guard = ToolBudgetGuard.active()
        if guard is None:
            return
        observer = getattr(guard, "aobserve_upstream_policy_block", None)
        if not callable(observer):
            RuntimeControlMiddleware._observe_upstream_policy_block(request)
            return
        tool_name, arguments, _ = _request_facts(request)
        await observer(
            tool_name=tool_name,
            args=(),
            kwargs=arguments,
        )

    @classmethod
    def _execute_policy_blocked(
        cls,
        *,
        request: ToolCallRequest,
        handler: ToolHandler,
    ) -> ToolHandlerResult:
        cls._observe_upstream_policy_block(request)
        return handler(request)

    @staticmethod
    def _model_turn(state: object) -> int:
        if isinstance(state, Mapping):
            value = state.get("runtime_control_model_turn", 0)
        else:
            value = getattr(state, "runtime_control_model_turn", 0)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

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
                result=result,
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
            intent = await ToolBudgetGuard.aadmit_task_policy_for_async_dispatch(
                guard,
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
            await ToolBudgetGuard.arecord_task_policy_outcome_for_async_dispatch(
                guard,
                intent=intent,
                succeeded=False,
                error_class=type(exc).__name__,
            )
            raise
        else:
            succeeded = _succeeded(result)
            await ToolBudgetGuard.arecord_task_policy_outcome_for_async_dispatch(
                guard,
                intent=intent,
                succeeded=succeeded,
                error_class=None if succeeded else "ToolMessageError",
                result=result,
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


# Compatibility alias retained while legacy wrappers and external tests run in
# shadow parity. New composition code uses ``RuntimeControlMiddleware``.
RuntimeToolControlMiddleware = RuntimeControlMiddleware


__all__ = (
    "RuntimeControlMiddleware",
    "RuntimeControlState",
    "RuntimeModelTurnReducer",
    "RuntimeToolControlMiddleware",
    "RuntimeToolSurfaceSnapshot",
)
