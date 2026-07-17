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

import dataclasses
import json
from collections.abc import Sequence
from typing import Any, cast

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


# Stable contract: anything emitted from a subagent's runtime carries this
# key in its RunnableConfig.metadata. The worker's StreamUpdateProcessor
# reads it to register a deterministic (subgraph_task_id → supervisor_call_id)
# link the first time it sees a chunk from that subgraph; subsequent events
# from the same subgraph hit the cache.
SUPERVISOR_TASK_CALL_ID_KEY = "supervisor_task_call_id"


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
        tool_call_id = runtime.tool_call_id
        return {
            "configurable": {
                "ls_agent_type": "subagent",
                SUPERVISOR_TASK_CALL_ID_KEY: tool_call_id,
            },
            "metadata": {
                SUPERVISOR_TASK_CALL_ID_KEY: tool_call_id,
            },
        }

    def task(
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
        with _subagent_tracing_context():
            result = await subagent.ainvoke(subagent_state, subagent_config)
        return _return_command_with_state_update(result, runtime.tool_call_id)

    return StructuredTool.from_function(
        name="task",
        func=task,
        coroutine=atask,
        description=description,
        infer_schema=False,
        args_schema=TaskToolSchema,
    )


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
