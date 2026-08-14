"""Custom `task` tool that injects `supervisor_task_call_id` into subagent config.

Replaces `deepagents.middleware.subagents._build_task_tool` so the supervisor's
tool call_id is propagated into the subagent's RunnableConfig metadata. This
makes the subgraph→supervisor call_id linkage deterministic — the worker's
stream handlers read `supervisor_task_call_id` from chunk metadata instead of
guessing via a FIFO heuristic that breaks when ≥2 subagents are dispatched in
the same supervisor turn (e.g. a parallel research fleet).

Why a monkey-patch and not a fork:
- We want every other deepagents code path (subagent compilation, state
  filtering, result-extraction shape) to keep working as-is.
- The only behavioural delta we need is the config metadata.
- `_build_task_tool` is small enough that mirroring its shape is low-risk;
  if deepagents refactors it we'll see test failures and follow up.

Mirrored against deepagents' `_build_task_tool` as of the 1.x middleware
(signature: `(subagents, task_description, *, private_state_keys,
state_schema)`). Deltas from upstream are ONLY the
`supervisor_task_call_id` stamps in the subagent invocation config. The
parent's callbacks/tags/configurable reach the subagent ambiently via
langgraph's `ensure_config` per-key merge, so — like upstream — we do not
forward parent config keys explicitly (doing so double-counts under the
merge).

The function is registered in `agent_runtime/execution/factory.py` at
module-load time via `deepagents.middleware.subagents._build_task_tool = ...`.

Note: this file deliberately does **not** use `from __future__ import
annotations`. langchain's `StructuredTool` introspects `inspect.signature`
to find `ToolRuntime`-annotated parameters and inject them at call time;
PEP 563 string annotations break that detection (they look like the
literal string `"ToolRuntime"` and `issubclass` returns False).
"""

import asyncio
import dataclasses
import json
from collections.abc import Sequence
from typing import Any, Final, cast

from langchain.agents.structured_output import ResponseFormat
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.types import Command

# Re-import the pieces we mirror so we stay 1:1 with the upstream behavior
# except for the metadata injection.
from deepagents.middleware.subagents import (  # type: ignore[import-untyped]
    TASK_TOOL_DESCRIPTION,
    CompiledSubAgent,
    SubAgent,
    TaskToolSchema,
    _EXCLUDED_STATE_KEYS,
    _get_subagent_response_format,
    _subagent_tracing_context,
    create_sub_agent,
)
from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.contracts import OperationGatewayMode
from agent_runtime.capabilities.operations.internal_adapter import (
    InternalOperationAdapter,
)
from agent_runtime.capabilities.operations.probes import OperationShadowProbe
from agent_runtime.delegation.subagents.operation_identity import (
    SUBAGENT_DELEGATION_OPERATION_ID_KEY,
    SUBAGENT_PARENT_OPERATION_ID_KEY,
    SUBAGENT_ROOT_OPERATION_ID_KEY,
    SUPERVISOR_TASK_CALL_ID_KEY,
    SubagentOperationIdentityFactory,
)
from agent_runtime.surfaces_v2.ledger_models import Producer


TASK_TOOL_NAME: Final[str] = "task"
"""The model-facing name of the delegation tool this module builds.

Named rather than repeated because ``task`` is installed onto the model surface
by ``deepagents``' subagent middleware — this module replaces the library's
builder — so it never passes the factory's append list and cannot be declared
at a composition site. Its context-occupancy declaration therefore resolves this
constant by name (see
:mod:`agent_runtime.observability.context_installed_tools`), and a rename that
did not update both ends would otherwise orphan the declaration silently.
"""


def build_atlas_task_tool(
    subagents: Sequence[Any],
    task_description: str | None = None,
    *,
    private_state_keys: frozenset[str] = frozenset(),
    state_schema: type | None = None,
) -> StructuredTool:
    """Mirrors `deepagents._build_task_tool` but injects supervisor_task_call_id.

    Signature matches the upstream so the monkey-patch is drop-in.
    """

    def _compile_spec(
        spec: Any,
        *,
        response_format: ResponseFormat[Any] | type | dict[str, Any] | None = None,
    ) -> CompiledSubAgent:
        """Compile one raw spec or configure one provided runnable (upstream 1:1)."""
        if "runnable" in spec:
            if response_format is not None:
                msg = (
                    f'response_schema cannot be used with compiled subagent "{spec["name"]}"; '
                    "dynamic schemas require a raw SubAgent spec."
                )
                raise ValueError(msg)
            compiled = cast("CompiledSubAgent", spec)
            runnable = compiled["runnable"].with_config(
                {
                    "metadata": {"lc_agent_name": spec["name"]},
                    "run_name": spec["name"],
                }
            )
            return {
                "name": spec["name"],
                "description": spec["description"],
                "runnable": runnable,
            }
        return {
            "name": spec["name"],
            "description": spec["description"],
            "runnable": create_sub_agent(
                cast("SubAgent", spec),
                state_schema=state_schema,
                response_format=response_format,
            ),
        }

    compiled_subagents = [_compile_spec(spec) for spec in subagents]
    subagents_by_name = {spec["name"]: spec for spec in subagents}

    subagent_graphs: dict[str, Runnable] = {
        spec["name"]: spec["runnable"] for spec in compiled_subagents
    }
    subagent_description_str = "\n".join(
        f"- {s['name']}: {s['description']}" for s in compiled_subagents
    )

    if task_description is None:
        description = TASK_TOOL_DESCRIPTION.format(
            available_agents=subagent_description_str
        )
    elif "{available_agents}" in task_description:
        description = task_description.format(available_agents=subagent_description_str)
    else:
        description = task_description

    def _return_command_with_state_update(
        result: dict[str, Any], tool_call_id: str
    ) -> Command:
        if "messages" not in result:
            error_msg = (
                "CompiledSubAgent must return a state containing a 'messages' key. "
                "Custom StateGraphs used with CompiledSubAgent should include 'messages' "
                "in their state schema to communicate results back to the main agent."
            )
            raise ValueError(error_msg)

        state_update = {
            k: v for k, v in result.items() if k not in _EXCLUDED_STATE_KEYS
        }

        structured = result.get("structured_response")
        if structured is not None:
            if hasattr(structured, "model_dump_json"):
                content: str = structured.model_dump_json()
            elif dataclasses.is_dataclass(structured) and not isinstance(
                structured, type
            ):
                content = json.dumps(dataclasses.asdict(structured))
            else:
                content = json.dumps(structured)
        else:
            # Walk back to the last AIMessage with non-empty text (upstream
            # fix: Anthropic occasionally emits a trailing empty `end_turn`
            # AIMessage after a successful final tool call).
            content = ""
            for msg in reversed(result["messages"]):
                if isinstance(msg, AIMessage):
                    text = msg.text.rstrip() if msg.text else ""
                    if text:
                        content = text
                        break

        return Command(
            update={
                **state_update,
                "messages": [ToolMessage(content, tool_call_id=tool_call_id)],
            }
        )

    def _select_subagent(subagent_type: str, runtime: ToolRuntime) -> Runnable:
        """Return the runnable to use for this task invocation (upstream 1:1)."""
        response_format = _get_subagent_response_format(runtime)
        if response_format is not None:
            new_spec = _compile_spec(
                subagents_by_name[subagent_type],
                response_format=response_format,
            )
            return new_spec["runnable"]
        return subagent_graphs[subagent_type]

    def _validate_and_prepare_state(
        subagent_type: str,
        description: str,
        runtime: ToolRuntime,
    ) -> tuple[Runnable, dict[str, Any]]:
        subagent = _select_subagent(subagent_type, runtime)
        subagent_state = {
            k: v for k, v in runtime.state.items() if k not in _EXCLUDED_STATE_KEYS
        }
        subagent_state = {
            k: v for k, v in subagent_state.items() if k not in private_state_keys
        }
        subagent_state["messages"] = [HumanMessage(content=description)]
        return subagent, subagent_state

    def _build_subagent_config(runtime: ToolRuntime) -> RunnableConfig:
        """Minimal invocation config + the Atlas linkage stamps.

        Upstream passes only `{"configurable": {"ls_agent_type": "subagent"}}`
        — parent callbacks/tags/configurable/metadata propagate ambiently via
        langgraph's per-key `ensure_config` merge. Our two additions:
        - `configurable.supervisor_task_call_id` (defensive — second channel)
        - `metadata.supervisor_task_call_id` (primary — what the worker reads)
        """
        return build_subagent_invocation_config(runtime.tool_call_id)

    def _gateway_enforced() -> bool:
        context = OperationContext.active()
        return context is not None and context.mode is OperationGatewayMode.ENFORCE

    async def _invoke_authoritative(
        *,
        capability: str,
        op: str,
        arguments: dict[str, object],
        legacy: Any,
        safe_summary: str,
    ) -> object:
        """Execute one reviewed delegation operation through the gateway.

        A non-success disposition is an honest model-visible result.  Crucially,
        the captured legacy callable is never entered when the gateway blocks or
        stages work, so neither task nor dispatch can bypass its authority.
        """

        result = await InternalOperationAdapter(
            capability=capability,
            op=op,
        ).invoke(
            arguments=arguments,
            legacy=legacy,
            safe_summary=safe_summary,
        )
        return result.value if result.completed else result.safe_summary

    def task(
        description: str,
        subagent_type: str,
        runtime: ToolRuntime,
    ) -> str | Command:
        if _gateway_enforced():
            # A model run uses StructuredTool's coroutine.  A synchronous host
            # may still use the same authoritative path when it owns no running
            # event loop; inside a loop we fail closed rather than reviving the
            # old direct ``subagent.invoke`` branch.
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(
                    atask(
                        description=description,
                        subagent_type=subagent_type,
                        runtime=runtime,
                    )
                )
            raise RuntimeError(
                "Synchronous subagent delegation is unavailable inside an event loop; "
                "use the task coroutine."
            )
        if subagent_type not in subagent_graphs:
            allowed_types = ", ".join([f"`{k}`" for k in subagent_graphs])
            return (
                f"We cannot invoke subagent {subagent_type} because it does not exist, "
                f"the only allowed types are {allowed_types}"
            )
        if not runtime.tool_call_id:
            value_error_msg = "Tool call ID is required for subagent invocation"
            raise ValueError(value_error_msg)
        subagent, subagent_state = _validate_and_prepare_state(
            subagent_type, description, runtime
        )
        subagent_config = _build_subagent_config(runtime)
        with _subagent_tracing_context():
            result = subagent.invoke(subagent_state, subagent_config)
        return _return_command_with_state_update(result, runtime.tool_call_id)

    async def atask(
        description: str,
        subagent_type: str,
        runtime: ToolRuntime,
    ) -> str | Command:
        if subagent_type not in subagent_graphs:
            allowed_types = ", ".join([f"`{k}`" for k in subagent_graphs])
            return (
                f"We cannot invoke subagent {subagent_type} because it does not exist, "
                f"the only allowed types are {allowed_types}"
            )
        if not runtime.tool_call_id:
            value_error_msg = "Tool call ID is required for subagent invocation"
            raise ValueError(value_error_msg)
        subagent, subagent_state = _validate_and_prepare_state(
            subagent_type, description, runtime
        )
        subagent_config = _build_subagent_config(runtime)

        async def _invoke_subagent() -> object:
            with OperationContext.producer_scope(Producer.SUBAGENT):

                async def _dispatch() -> object:
                    with _subagent_tracing_context():
                        return await subagent.ainvoke(subagent_state, subagent_config)

                arguments = {
                    "description": description,
                    "subagent_type": subagent_type,
                }
                if _gateway_enforced():
                    return await _invoke_authoritative(
                        capability="subagent",
                        op="dispatch",
                        arguments=arguments,
                        legacy=_dispatch,
                        safe_summary="Completed subagent dispatch.",
                    )
                dispatched = await OperationShadowProbe.invoke_legacy(
                    capability="subagent",
                    op="dispatch",
                    arguments=arguments,
                    legacy=_dispatch,
                )
                await OperationShadowProbe.observe_model_result(dispatched)
                return dispatched

        task_arguments = {
            "description": description,
            "subagent_type": subagent_type,
        }
        if _gateway_enforced():
            result = await _invoke_authoritative(
                capability="builtin",
                op="task",
                arguments=task_arguments,
                legacy=_invoke_subagent,
                safe_summary="Completed subagent delegation.",
            )
        else:
            result = await OperationShadowProbe.invoke_legacy(
                capability="builtin",
                op="task",
                arguments=task_arguments,
                legacy=_invoke_subagent,
            )
        if not isinstance(result, dict):
            return str(result)
        return _return_command_with_state_update(result, runtime.tool_call_id)

    return StructuredTool.from_function(
        name=TASK_TOOL_NAME,
        func=task,
        coroutine=atask,
        description=description,
        infer_schema=False,
        args_schema=TaskToolSchema,
    )


def build_subagent_invocation_config(
    supervisor_task_call_id: str | None,
) -> RunnableConfig:
    """Build config metadata that pins a child graph to its parent operation.

    The helper is deliberately separate from the upstream task-tool mirror so
    tests can verify the correlation contract without depending on LangChain's
    private ``ToolRuntime`` construction.
    """

    link = (
        SubagentOperationIdentityFactory.for_active_context(
            supervisor_task_call_id=supervisor_task_call_id
        )
        if supervisor_task_call_id
        else None
    )
    correlation_metadata: dict[str, object] = {
        SUPERVISOR_TASK_CALL_ID_KEY: supervisor_task_call_id,
    }
    correlation_configurable: dict[str, object] = {
        "ls_agent_type": "subagent",
        SUPERVISOR_TASK_CALL_ID_KEY: supervisor_task_call_id,
    }
    if link is not None:
        # These are deterministic functions of trusted run identity and
        # tool-call id.  Downstream stream/usage consumers can therefore
        # rebuild the same parent/child relationship after a reconnect
        # without a FIFO assignment or mutable in-process map.
        correlation_metadata.update(
            {
                SUBAGENT_DELEGATION_OPERATION_ID_KEY: link.delegation_operation_id,
                SUBAGENT_PARENT_OPERATION_ID_KEY: link.delegation_operation_id,
                SUBAGENT_ROOT_OPERATION_ID_KEY: link.child_root_operation_id,
            }
        )
        correlation_configurable.update(correlation_metadata)
    return {
        "configurable": correlation_configurable,
        "metadata": correlation_metadata,
    }


def install_atlas_task_tool() -> None:
    """Monkey-patch deepagents to use our task-tool builder.

    Idempotent. Called once at factory.py module-load time.
    """

    from deepagents.middleware import subagents as _ds  # noqa: PLC0415

    # Marker so we don't double-patch in test setups that re-import.
    if getattr(_ds, "_atlas_task_tool_installed", False):
        return
    _ds._build_task_tool = build_atlas_task_tool  # type: ignore[attr-defined]
    _ds._atlas_task_tool_installed = True  # type: ignore[attr-defined]
