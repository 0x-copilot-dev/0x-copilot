"""Graph-wide tool identity, budget accounting, and result bounding.

Deep Agents adds todo, filesystem, execute, and task tools after the caller's
tool list is assembled. A ``BaseTool`` decorator therefore cannot be the
authoritative model/tool boundary. This LangChain middleware runs around every
tool exposed by the completed graph, including framework-injected tools and the
same tools inside locally compiled Deep Agents subagents.

It does **not** decide what runs alongside what. This module used to open with
"serial-default execution", and that was accurate while it held a run-wide
exclusive permit around every graph-visible tool call. LangGraph schedules a
turn's tool calls itself — its async node gathers them, its sync node fans them
across a thread pool — and that scheduling is now what the runtime uses, bounded
by the framework's own ``max_concurrency``. What remains here is per call, and
the run-scoped state it touches (the lifecycle reducer, the budget ledger)
guards itself rather than borrowing mutual exclusion from a lock upstream.
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
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
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
from agent_runtime.capabilities.skills.tool_gate import SkillToolGate
from agent_runtime.capabilities.tools.tool_use_enforcement import PolicyBlockedTool
from agent_runtime.context.tool_result_admission import ToolResultCap
from agent_runtime.control_plane.context import (
    RunControlContext,
    RuntimeToolControlOutcome,
    RuntimeToolLifecycleReducer,
)
from agent_runtime.execution.tool_errors import BudgetExceeded
from agent_runtime.execution.tool_errors import ToolBudgetRejected
from agent_runtime.execution.tool_refusals import ToolRefusals
from agent_runtime.execution.tool_error_policy import DefaultToolErrorPolicy
from agent_runtime.execution.tool_surface import (
    DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES,
)
from agent_runtime.execution.call_identity import (
    RuntimeCallContext,
    RuntimeModelCallIdentity,
    RuntimeToolCallIdentity,
)
from agent_runtime.execution.run_steering import (
    RunSteeringContext,
    SteeringMessage,
)
from agent_runtime.hooks.contracts import (
    HookPhase,
    ModelRequestBeforeInput,
    PromptAssembleInput,
    ToolExecuteAfterInput,
    ToolExecuteBeforeInput,
)
from agent_runtime.hooks.dispatch import HookDispatch, ToolCallVerdict
from agent_runtime.observability.token_usage import (
    NormalizedTokenUsage,
    TokenUsageExtractorRegistry,
)
from agent_runtime.prompts.cache_fallback import (
    PromptCacheFallbackContext,
    PromptCacheFallbackHandoff,
)
from agent_runtime.prompts.runtime_binding import (
    PromptRuntimeBinding,
    PromptRuntimeResult,
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

    #: The execution scope of the graph the user is actually talking to. Named
    #: once here because two seams now branch on it — the scope resolver below
    #: and the steering drain — and a re-typed literal in either is a silent
    #: mis-route rather than a failure.
    SUPERVISOR_SCOPE: str = "supervisor"

    def __init__(
        self,
        *,
        excluded_tool_names: frozenset[str] = (DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES),
    ) -> None:
        # Legacy/test graphs may not have a verified run-control binding. Their
        # fallback stays instance-local; production uses the one run-scoped
        # reducer inherited by supervisor and local subagents.
        self._excluded_tool_names = frozenset(excluded_tool_names)
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
        return {
            "runtime_control_model_turn": model_turn,
            **self._steering_update(runtime),
        }

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """Synchronous compatibility adapter with identical observation."""

        provider_request = self._provider_visible_request(request)
        provider_request, binding, prompt_result, _ = self._prepare_prompt_for_call(
            provider_request
        )
        if binding is not None and prompt_result is not None:
            if binding.observation_publisher is not None:
                raise RuntimeError(
                    "durable prompt observations require the async model-call seam"
                )
            binding.observe(prompt_result)
        self._observe_final_tool_surface(provider_request)
        provider_request = self._apply_model_call_hooks(provider_request)
        handoff = self._cache_fallback_handoff(binding, prompt_result)
        with PromptCacheFallbackContext.bind(handoff):
            return handler(provider_request)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Apply reviewed exclusions, observe the surface, then delegate."""

        provider_request = self._provider_visible_request(request)
        (
            provider_request,
            binding,
            prompt_result,
            model_call_id,
        ) = self._prepare_prompt_for_call(provider_request)
        assembly = None
        if (
            binding is not None
            and prompt_result is not None
            and model_call_id is not None
        ):
            assembly = await binding.record_assembled(
                result=prompt_result,
                model_call_id=model_call_id,
            )
        self._observe_final_tool_surface(provider_request)
        provider_request = self._apply_model_call_hooks(provider_request)
        handoff = self._cache_fallback_handoff(binding, prompt_result)
        with PromptCacheFallbackContext.bind(handoff):
            response = await handler(provider_request)
        if binding is not None and prompt_result is not None and assembly is not None:
            await binding.record_cache(
                assembly=assembly,
                usage=_model_response_usage(
                    response,
                    provider=prompt_result.observation.provider,
                ),
                result=prompt_result,
            )
        return response

    @staticmethod
    def _cache_fallback_handoff(
        binding: PromptRuntimeBinding | None,
        result: PromptRuntimeResult | None,
    ) -> PromptCacheFallbackHandoff | None:
        if binding is None or not isinstance(result, PromptRuntimeResult):
            return None
        return PromptCacheFallbackHandoff(
            result=result,
            rejection_adapters=binding.cache_rejection_adapters,
        )

    def after_model(
        self,
        state: RuntimeControlState,
        runtime: object,
    ) -> None:
        """Reserved content-free post-model observation seam."""

        del state, runtime
        return None

    async def aafter_model(
        self,
        state: RuntimeControlState,
        runtime: object,
    ) -> None:
        """Reserved content-free post-model observation seam.

        Deliberately a no-op. This hook used to record a durable ordering for
        the turn's tool calls, which only ever mattered while the runtime itself
        decided which of them could overlap. LangGraph schedules the turn's tool
        node now, so there is no ordering here that is ours to author.
        """

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
            **self._steering_update(runtime),
        }

    @classmethod
    def _steering_update(cls, runtime: object) -> dict[str, Any]:
        """Deliver any waiting user steer as context for THIS model call.

        This hook is the only safe delivery boundary in the graph. It runs after
        the previous tool node has fully settled and before the next provider
        dispatch, so a steer that arrived halfway through a 30-second tool call
        waits in the mailbox instead of tearing that call down — interrupting an
        in-flight external effect is cancellation's job, and it already has one.

        Supervisor scope only. A subagent inherits this middleware and the run's
        context binding, so an unscoped drain would hand the user's course
        correction to whichever child happened to reach a model step first, and
        the supervisor — the one holding the plan the user is correcting — would
        never see it. It is also consume-once: the drain empties the mailbox, so
        the message rides the conversation state from here on rather than being
        re-appended at every subsequent turn.
        """

        if cls._execution_scope_for_runtime(runtime) != cls.SUPERVISOR_SCOPE:
            return {}
        steers: tuple[SteeringMessage, ...] = RunSteeringContext.drain()
        if not steers:
            return {}
        return {
            "messages": [
                HumanMessage(
                    content=steer.as_model_text(),
                    id=steer.steer_id,
                )
                for steer in steers
            ]
        }

    def _observe_final_tool_surface(self, request: ModelRequest[Any]) -> None:
        self._final_tool_surface = RuntimeToolSurfaceSnapshot.from_tools(
            request.tools or []
        )

    @classmethod
    def _apply_model_call_hooks(
        cls,
        request: ModelRequest[Any],
    ) -> ModelRequest[Any]:
        """Run ``model.request.before`` and ``prompt.assemble`` for one call.

        ``model.request.before`` is observe-only — ``HookDispatch.observe``
        returns ``None``, so there is nothing here to assign back onto the
        request. The only writable affordance is ``prompt.assemble``, and it can
        only APPEND: the returned block is concatenated after the assembled
        system prompt, so plugin bytes can never precede the policy fragment,
        and ``tools`` / ``messages`` / ``model_settings`` are never touched.

        Both run after F2 has produced the effective prompt, so an appended
        block is deliberately outside the sealed, cacheable assembly plan and
        outside its recorded digest.

        Every fact handed to a hook is read from ``request``, never from
        ``self._final_tool_surface``. Today those two agree: nothing awaits
        between the write and the read, so this is not a bug being fixed. It is
        the narrower dependency — a hook payload that is a pure function of the
        request cannot be desynchronized from it by any future edit that adds a
        suspension point, and the classmethod is what keeps that true.
        """

        wants_observe = HookDispatch.enabled(HookPhase.MODEL_REQUEST_BEFORE)
        wants_prompt = HookDispatch.enabled(HookPhase.PROMPT_ASSEMBLE)
        if not wants_observe and not wants_prompt:
            return request
        tool_names = tuple(
            str(getattr(tool, "name", "")).strip() for tool in (request.tools or ())
        )
        digest = cls._system_prompt_digest(request.system_message)
        execution_scope = cls._execution_scope_for_runtime(request.runtime)
        model_identifier = _model_family(request.model, fallback="unknown")
        if wants_observe:
            HookDispatch.observe(
                HookPhase.MODEL_REQUEST_BEFORE,
                ModelRequestBeforeInput(
                    model_identifier=model_identifier,
                    execution_scope=execution_scope,
                    message_count=len(request.messages or []),
                    tool_names=tool_names,
                    system_prompt_digest=digest,
                ),
            )
        if not wants_prompt:
            return request
        appended = HookDispatch.prompt_assemble(
            PromptAssembleInput(
                model_identifier=model_identifier,
                execution_scope=execution_scope,
                system_prompt_digest=digest,
                tool_names=tool_names,
            )
        )
        if not appended:
            return request
        system_message = cls._appended_system_message(request.system_message, appended)
        if system_message is None:
            return request
        return request.override(system_message=system_message)

    @staticmethod
    def _system_prompt_digest(system_message: SystemMessage | None) -> str:
        """Content-free identity for the system prompt handed to a hook."""

        content = getattr(system_message, "content", None)
        return canonical_json_sha256(
            {"system": content if isinstance(content, str) else ""}
        )

    @staticmethod
    def _appended_system_message(
        system_message: SystemMessage | None,
        appended: str,
    ) -> SystemMessage | None:
        """Return the system message with ``appended`` concatenated after it.

        ``None`` means "leave the request alone": a structured (non-text) system
        message is not something this seam rewrites blind.
        """

        if system_message is None:
            return SystemMessage(content=appended)
        content = system_message.content
        if not isinstance(content, str):
            return None
        return system_message.model_copy(update={"content": f"{content}\n\n{appended}"})

    @classmethod
    def _prepare_prompt_for_call(
        cls,
        request: ModelRequest[Any],
    ) -> tuple[
        ModelRequest[Any],
        PromptRuntimeBinding | None,
        PromptRuntimeResult | None,
        str | None,
    ]:
        """Prepare one F2 request plus its replay-stable observation identity."""

        binding = RunControlContext.prompt_runtime()
        if binding is None:
            return (request, None, None, None)
        state = request.state if isinstance(request.state, Mapping) else {}
        execution_scope = cls._execution_scope_for_runtime(request.runtime)
        result = binding.prepare(
            system_message=request.system_message,
            state=state,
            tools=request.tools or (),
            execution_scope=execution_scope,
            task_policy_progress=RunControlContext.task_policy_progress(),
            model_family=_model_family(request.model, fallback=binding.model_family),
        )
        identity = RuntimeModelCallIdentity.from_current(
            execution_scope=execution_scope,
            model_turn=max(cls._model_turn(request.state), 1),
        )
        model_call_id = identity.model_call_id if identity is not None else None
        if binding.observation_publisher is not None and model_call_id is None:
            raise RuntimeError(
                "durable prompt observations require a verified run binding"
            )
        if not result.observation.sent_assembled_prompt:
            return (request, binding, result, model_call_id)
        # ModelRequest.override follows an immutable replace contract. These
        # are the only model-call fields F2 owns; durable conversation messages,
        # runtime state, model routing, and provider settings remain untouched.
        return (
            request.override(
                system_message=result.system_message,
                tools=list(result.tools),
            ),
            binding,
            result,
            model_call_id,
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
        """Synchronously execute one graph-visible tool.

        No admission gate. LangGraph's synchronous ``ToolNode`` fans a turn's
        calls out across a thread pool, and the framework owns that scheduling;
        this seam adds identity, budget accounting, and lifecycle observation
        around whatever it hands over. The state those three touch carries its
        own locking (see :class:`RuntimeToolLifecycleReducer` and
        ``ToolBudgetGuard.admit_and_charge``) rather than relying on a run-wide
        lock to hold every call apart.
        """

        refusal = self._skill_ceiling_refusal(request)
        if refusal is not None:
            return refusal
        verdict, request = self._apply_tool_call_hooks(request)
        if verdict.vetoed:
            return self._hook_veto_message(request, verdict)
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
            result = self._observe_sync_tool_lifecycle(
                request=request,
                identity=identity,
                execute=execute,
            )
        return self._apply_tool_result_hooks(request, result)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: AsyncToolHandler,
    ) -> ToolHandlerResult:
        """Asynchronously execute one graph-visible tool.

        The framework decides what runs alongside what. LangGraph's ``ToolNode``
        already gathers a turn's tool calls into concurrent tasks, and this seam
        no longer takes a run-wide lock that collapsed that back to one call at a
        time. What it still does — bind the call identity, charge the budget,
        record the lifecycle — is per call and safe to run concurrently, because
        the shared state each of those touches guards itself.

        This is also what retires the delegation self-deadlock. ``task`` is a
        container: it awaits a whole child graph whose own tool calls arrive
        back at this very method, on the same run. While the permit existed and
        was non-reentrant, a parent held it across that await and its child
        queued on it forever, so every subagent that called any tool wedged the
        run until the 180s timeout. That was fixed by exempting ``task`` from
        the permit; with no permit at all the exemption has nothing to name, so
        the class of bug is gone rather than special-cased. Nesting is no longer
        a thing this seam has to reason about.
        """

        refusal = self._skill_ceiling_refusal(request)
        if refusal is not None:
            return refusal
        verdict, request = self._apply_tool_call_hooks(request)
        if verdict.vetoed:
            return self._hook_veto_message(request, verdict)
        identity = self._call_identity(request)
        with RuntimeCallContext.bind(identity):

            async def execute() -> ToolHandlerResult:
                if isinstance(request.tool, PolicyBlockedTool):
                    # User policy is the outer rejection gate. A blocked
                    # call never reaches budget admission.
                    await self._aobserve_upstream_policy_block(request)
                    return await handler(request)
                return await self._aexecute(request=request, handler=handler)

            result = await self._observe_async_tool_lifecycle(
                request=request,
                identity=identity,
                execute=execute,
            )
        return self._apply_tool_result_hooks(request, result)

    @staticmethod
    def _skill_ceiling_refusal(request: ToolCallRequest) -> ToolMessage | None:
        """Refuse a call outside the ``allowed_tools`` of this run's loaded Skills.

        ``None`` when the call clears the ceiling, which is every call in every
        run that has not loaded a restricting Skill — see
        :mod:`agent_runtime.capabilities.skills.tool_gate` for the rule and for
        why an author addresses an MCP tool by its namespaced model-surface
        name.

        **First, ahead of the hook seam**, and deliberately: the ceiling is a
        capability a Skill declared, so it must not be observable or
        influenceable by an extension point that runs inside it. The cost is
        that ``tool.execute.before`` never sees a call the ceiling refused —
        the same trade the hook seam itself already makes against budget
        admission, and stated here so a reader of the hook ledger knows why a
        refused call leaves no ``modified`` record.

        This is the one seam that sees framework-injected Deep Agents tools and
        the tool calls made *inside* locally compiled subagents, which is why
        the ceiling lives here and not on the caller's tool list: a ceiling
        applied where the tool list is assembled would miss both.
        """

        decision = SkillToolGate.evaluate(str(request.tool_call.get("name", "")))
        if decision.allowed:
            return None
        return ToolMessage(
            content=decision.reason,
            tool_call_id=str(request.tool_call.get("id", "")),
            name=str(request.tool_call.get("name", "")) or None,
            status="error",
        )

    @classmethod
    def _apply_tool_call_hooks(
        cls,
        request: ToolCallRequest,
    ) -> tuple[ToolCallVerdict, ToolCallRequest]:
        """Run ``tool.execute.before`` and fold its verdict into the request.

        A veto short-circuits before identity binding, budget admission, and
        dispatch, so a refused call costs the run nothing. An argument rewrite
        is applied through ``ToolCallRequest.override`` and then flows through
        every INNER middleware — this seam is the outermost ``wrap_tool_call``
        wrapper — so the host-path screen and the Deep Agents permission layer
        still see, and can still refuse, whatever a hook wrote.

        Known and deliberate: a vetoed call opens no
        :class:`RuntimeToolLifecycleReducer` entry, because it has no execution
        to have a lifecycle. The refusal is still observable twice over — the
        model gets an error ``ToolMessage``, and the veto itself is a
        ``modified`` record on the run's hook ledger — but a reader of the tool
        ledger alone will not see that the call was attempted. Opening a
        lifecycle entry only to settle it as refused would also make the
        reconciliation paths carry a case that never has an in-flight call.
        """

        if not HookDispatch.enabled(HookPhase.TOOL_EXECUTE_BEFORE):
            return (ToolCallVerdict(), request)
        facts = cls._hook_call_facts(request)
        if facts is None:
            return (ToolCallVerdict(), request)
        tool_name, tool_call_id, arguments = facts
        verdict = HookDispatch.tool_execute_before(
            ToolExecuteBeforeInput(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                execution_scope=cls._execution_scope(request),
                arguments=arguments,
            )
        )
        if verdict.vetoed or verdict.arguments is None:
            return (verdict, request)
        return (
            verdict,
            request.override(
                tool_call={**request.tool_call, "args": dict(verdict.arguments)}
            ),
        )

    @classmethod
    def _apply_tool_result_hooks(
        cls,
        request: ToolCallRequest,
        result: ToolHandlerResult,
    ) -> ToolHandlerResult:
        """Run ``tool.execute.after`` on the text about to reach the model.

        Deliberately placed AFTER lifecycle settlement: the recorded outcome
        (success / error / interrupt / command) is computed from what the tool
        actually returned, so rewriting the text cannot rewrite the run's
        record of what happened.
        """

        if not HookDispatch.enabled(HookPhase.TOOL_EXECUTE_AFTER):
            return result
        facts = cls._hook_call_facts(request)
        if facts is None:
            return result
        tool_name, tool_call_id, arguments = facts
        rewritten = HookDispatch.tool_execute_after(
            ToolExecuteAfterInput(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                execution_scope=cls._execution_scope(request),
                arguments=arguments,
                result_text=cls._model_visible_text(result, tool_call_id=tool_call_id),
                succeeded=_succeeded(result),
            )
        )
        if rewritten is None:
            return result
        return _map_tool_messages(
            result,
            lambda message: (
                message
                if message.tool_call_id != tool_call_id
                else message.model_copy(update={"content": rewritten})
            ),
        )

    @staticmethod
    def _model_visible_text(
        result: ToolHandlerResult,
        *,
        tool_call_id: str,
    ) -> str | None:
        """Return this call's model-visible text, or ``None`` if structured."""

        for message in _tool_messages(result):
            if message.tool_call_id != tool_call_id:
                continue
            return message.content if isinstance(message.content, str) else None
        return None

    @staticmethod
    def _hook_veto_message(
        request: ToolCallRequest,
        verdict: ToolCallVerdict,
    ) -> ToolMessage:
        """Surface a hook veto the way every other refusal is surfaced.

        An error ``ToolMessage`` rather than a raise: the run continues and the
        model can adapt, exactly as it does for a budget rejection or a
        policy-blocked tool.
        """

        return ToolMessage(
            content=(
                verdict.veto_reason or "This tool call was blocked by a runtime hook."
            ),
            tool_call_id=str(request.tool_call.get("id", "")),
            name=str(request.tool_call.get("name", "")) or None,
            status="error",
        )

    @staticmethod
    def _hook_call_facts(
        request: ToolCallRequest,
    ) -> tuple[str, str, dict[str, Any]] | None:
        """Return ``(tool_name, tool_call_id, arguments)`` or ``None``.

        ``None`` when either identifier is missing: that is a malformed call the
        existing seam already fails on, and hooks must not observe or influence
        the shape of that failure. The arguments are a COPY, so a handler that
        mutates what it was handed changes nothing — only an explicit
        ``REWRITE_ARGUMENTS`` outcome takes effect.
        """

        tool_call_id = str(request.tool_call.get("id", "")).strip()
        tool_name = str(request.tool_call.get("name", "")).strip()
        if not tool_call_id or not tool_name:
            return None
        raw_arguments = request.tool_call.get("args", {})
        arguments = (
            dict(raw_arguments)
            if isinstance(raw_arguments, Mapping)
            else {"input": raw_arguments}
        )
        return (tool_name, tool_call_id, arguments)

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

    @classmethod
    def _execution_scope_for_runtime(cls, runtime: object) -> str:
        config = getattr(runtime, "config", None)
        if not isinstance(config, Mapping):
            return cls.SUPERVISOR_SCOPE
        metadata = config.get("metadata")
        configurable = config.get("configurable")
        for container in (metadata, configurable):
            if not isinstance(container, Mapping):
                continue
            value = container.get(SUPERVISOR_TASK_CALL_ID_KEY)
            if isinstance(value, str) and value.strip():
                return f"subagent:{value.strip()}"
        return cls.SUPERVISOR_SCOPE

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
            # No budgets and no offload target still leaves the result bound —
            # see :func:`_admit_result`. The name is read straight off the call
            # rather than through ``_request_facts``, which raises on a missing
            # name: this path deliberately did no admission before, so it must
            # not acquire a new way to fail a run that used to succeed.
            return _admit_result(
                handler(request),
                guard=None,
                tool_name=str(request.tool_call.get("name", "")),
                call_id=None,
                tool_call_id=str(request.tool_call["id"]),
            )
        tool_name, arguments, estimated = _request_facts(request)
        try:
            intent = guard.admit_task_policy(
                tool_name=tool_name,
                args=(),
                kwargs=arguments,
            )
        except ToolBudgetRejected as exc:
            return _surface_rejection(exc, request=request)
        decision, call_id = guard.admit_and_charge(
            tool_name=tool_name,
            estimated_input_tokens=estimated,
        )
        if isinstance(decision, ToolBudgetReject):
            rejection = guard.rejection_error(decision)
            if isinstance(rejection, ToolBudgetRejected):
                return _surface_rejection(rejection, request=request)
            raise rejection
        if call_id is None or not isinstance(
            decision, (ToolBudgetAdmit, ToolBudgetWarn)
        ):
            raise BudgetExceeded("Tool call was not admitted by runtime middleware.")
        if isinstance(decision, ToolBudgetWarn):
            _schedule_warning(guard=guard, decision=decision)
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
            # See the synchronous path: the cap is not conditional on a guard.
            return _admit_result(
                await handler(request),
                guard=None,
                tool_name=str(request.tool_call.get("name", "")),
                call_id=None,
                tool_call_id=str(request.tool_call["id"]),
            )
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
        decision, call_id = guard.admit_and_charge(
            tool_name=tool_name,
            estimated_input_tokens=estimated,
        )
        if isinstance(decision, ToolBudgetReject):
            rejection = guard.rejection_error(decision)
            if isinstance(rejection, ToolBudgetRejected):
                return _surface_rejection(rejection, request=request)
            raise rejection
        if call_id is None or not isinstance(
            decision, (ToolBudgetAdmit, ToolBudgetWarn)
        ):
            raise BudgetExceeded("Tool call was not admitted by runtime middleware.")
        # Emitted after the charge, never inside it: the append awaits, and the
        # cap must not be readable by a sibling call while this one waits.
        if isinstance(decision, ToolBudgetWarn):
            await guard.emit_warning(decision=decision)
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


def _model_family(model: object, *, fallback: str) -> str:
    """Read a provider model identifier without serializing model state."""

    for attribute in ("model_name", "model"):
        value = getattr(model, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _model_response_usage(
    response: ModelResponse[Any],
    *,
    provider: str,
) -> NormalizedTokenUsage:
    """Normalize only usage metadata carried by the actual model response."""

    extractor = TokenUsageExtractorRegistry.for_provider(provider)
    merged: NormalizedTokenUsage | None = None
    for message in response.result:
        observed = extractor.extract(message)
        if observed is None:
            continue
        merged = observed if merged is None else merged.merge(observed)
    return merged or NormalizedTokenUsage()


def _surface_rejection(
    rejection: ToolBudgetRejected,
    *,
    request: ToolCallRequest,
) -> ToolMessage:
    """Hand a policy refusal back to the model, marked as a refusal.

    Two consumers read this message and they need different things:

    * **The model** reads ``content`` and ``status``. ``status="error"`` is the
      honest signal there — the call yielded no result, and LangChain has only
      ``success`` / ``error`` to say that with. Calling it ``success`` would
      tell the model it got data it never received.
    * **The client** reads the ``tool_result`` event the stream publishes from
      this message. There, ``error`` is a lie of a different kind: the cap
      declined the call by design, so nothing failed, and a card that says
      "Failed" teaches users that a working budget looks like a broken run.

    The typed marker is what lets the stream classifier tell the two apart
    without pattern-matching this message's prose from three modules away. See
    :mod:`agent_runtime.execution.tool_refusals`.
    """

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
        additional_kwargs=ToolRefusals.marker_for(rejection) or {},
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
    guard: ToolBudgetGuard | None,
    tool_name: str,
    call_id: str | None,
    tool_call_id: str,
) -> ToolHandlerResult:
    """Bound the model-visible content of one tool call, guard or no guard.

    ``guard`` is ``None`` on exactly one path: a run whose org configured no
    tool budgets, whose store offers no offload target, and which therefore
    builds no per-run guard at all. That path used to return the handler's
    result untouched, which made every result bound elsewhere in this module
    unreachable on the default web / postgres configuration. Routing it through
    the same function keeps the model-admission boundary at one place; only
    *what* bounds it differs, because a guard also carries the budget note and
    the offload adapter.

    Rewriting is conditional on something actually changing. Every branch below
    returns the *identical* object when admission left the content alone, which
    is what makes it safe to run this on the previously untouched no-guard path:
    a result that needed no bound is passed through byte-for-byte and reference-
    for-reference, so nothing downstream can tell this function ran.
    """

    def admit(message: ToolMessage) -> ToolMessage:
        if message.tool_call_id != tool_call_id:
            return message
        content = (
            ToolResultCap.apply(message.content)
            if guard is None
            else guard.admit_model_visible_result(
                message.content,
                tool_name=tool_name,
                call_id=call_id or "",
            )
        )
        if content is message.content:
            return message
        return message.model_copy(update={"content": content})

    return _map_tool_messages(result, admit)


def _map_tool_messages(
    result: ToolHandlerResult,
    transform: Callable[[ToolMessage], ToolMessage],
) -> ToolHandlerResult:
    """Rebuild ``result`` with ``transform`` applied to every ``ToolMessage``.

    The single traversal for every model-visible rewrite this seam performs —
    budget-driven result admission and the ``tool.execute.after`` hook both go
    through it, so a ``Command`` payload is unwrapped identically for both.
    """

    if isinstance(result, list):
        mapped_items = [_map_tool_messages(item, transform) for item in result]
        # Identity preservation is deliberate: a result the transform did not
        # touch must come back as the SAME object, so the paths that were
        # previously untouched stay reference-for-reference.
        if all(new is old for new, old in zip(mapped_items, result, strict=True)):
            return result
        return mapped_items
    if isinstance(result, ToolMessage):
        return transform(result)
    update = result.update
    if not isinstance(update, Mapping) or "messages" not in update:
        return result
    messages = update["messages"]
    if isinstance(messages, ToolMessage):
        mapped: ToolMessage | list[object] = transform(messages)
        unchanged = mapped is messages
    elif isinstance(messages, Sequence) and not isinstance(
        messages,
        (str, bytes, bytearray),
    ):
        mapped_list = [
            transform(message) if isinstance(message, ToolMessage) else message
            for message in messages
        ]
        mapped = mapped_list
        unchanged = all(
            new is old for new, old in zip(mapped_list, messages, strict=True)
        )
    else:
        return result
    if unchanged:
        return result
    return replace(result, update={**update, "messages": mapped})


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
