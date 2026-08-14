"""Typed hook points: the phase catalogue, their inputs, and their outcomes.

Every extension point in this runtime used to be a Python edit plus a redeploy.
This package is the one seam where in-process code can observe — and, at two
narrow points, *narrow* — what the agent does. It is an extension of the
existing middleware chain
(:mod:`agent_runtime.capabilities.middleware.runtime_tool_control`), not a
second interception layer beside it.

Non-widening is the design constraint, and it is expressed in the **types**
rather than in a convention:

* ``tool.execute.before`` has ``CONTINUE`` / ``REWRITE_ARGUMENTS`` / ``VETO``.
  There is deliberately no ``ALLOW``. A veto is one-way, so a later hook cannot
  clear an earlier one, and no hook can turn a runtime DENY into a pass.
  Arguments rewritten here still travel through every inner middleware and the
  Deep Agents permission layer — this seam is the OUTERMOST ``wrap_tool_call``
  wrapper (LangChain composes the first middleware outermost), so a rewritten
  path is still screened by ``HostPathToolMiddleware`` and still denied by the
  filesystem floor.
* ``tool.execute.after`` can only replace text that has already been produced
  by a tool that already ran. It cannot re-run anything and cannot change the
  call's recorded outcome.
* ``prompt.assemble`` can only APPEND, never replace, remove, or reorder; the
  appended block lands after the assembled system prompt (so it can never
  precede the policy fragment) inside an explicit untrusted-context delimiter.
* ``model.request.before``, ``policy.decide.after`` and the run lifecycle
  phases are observe-only: their declared outcome type is ``None`` and the
  dispatch function that serves them returns ``None`` by signature, so there is
  no code path from a handler's return value to a decision.

``model.request.before`` is observe-only on purpose. A writable model request
means writable ``tools`` and a writable ``system_message`` — advertising a tool
the model was not granted, or rewriting the instructions immediately before
dispatch. That is authority widening and prompt injection in one object. The
legitimate need it would have served (add some context) is served by
``prompt.assemble`` in an append-only, size-bounded, clearly-labelled form.

**There is deliberately no ``tool.definition`` hook.** OpenCode ships one
(``packages/plugin/src/index.ts:222-335``): it rewrites an existing tool's
description and JSON parameters *before the model ever sees them*. That is a
prompt-injection primitive with no audit trail — the operator reviews a tool
surface that is not the surface the model was shown, and nothing downstream can
tell that the description it acted on was authored by a plugin. We are
consciously not copying it. If a plugin needs the model to know something, it
appends visible, delimited, attributed context through ``prompt.assemble``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, NonNegativeInt, model_validator

from agent_runtime.execution.contracts import RuntimeContract

#: Hard ceiling on one ``prompt.assemble`` contribution and on the sum of all
#: contributions for a single model call. A hook cannot spend the context
#: window on the run's behalf.
MAX_APPENDED_CONTEXT_CHARS: int = 8_000
#: Hard ceiling on a rewritten tool result.
MAX_REWRITTEN_RESULT_CHARS: int = 200_000


class HookPhase(StrEnum):
    """The closed catalogue of hook points. Adding one is a reviewed change."""

    TOOL_EXECUTE_BEFORE = "tool.execute.before"
    TOOL_EXECUTE_AFTER = "tool.execute.after"
    MODEL_REQUEST_BEFORE = "model.request.before"
    PROMPT_ASSEMBLE = "prompt.assemble"
    POLICY_DECIDE_AFTER = "policy.decide.after"
    RUN_START = "run.start"
    RUN_END = "run.end"


# --------------------------------------------------------------------------
# Inputs. Every one is frozen, and every one is built from a COPY of the real
# call state, so a handler that mutates what it was handed changes nothing.
# --------------------------------------------------------------------------


class ToolExecuteBeforeInput(RuntimeContract):
    """One graph-visible tool call, before budget admission and dispatch."""

    tool_name: Annotated[str, Field(min_length=1, max_length=320)]
    tool_call_id: Annotated[str, Field(min_length=1, max_length=320)]
    execution_scope: Annotated[str, Field(min_length=1, max_length=320)]
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolExecuteAfterInput(RuntimeContract):
    """One completed tool call and the text that is about to reach the model."""

    tool_name: Annotated[str, Field(min_length=1, max_length=320)]
    tool_call_id: Annotated[str, Field(min_length=1, max_length=320)]
    execution_scope: Annotated[str, Field(min_length=1, max_length=320)]
    arguments: dict[str, Any] = Field(default_factory=dict)
    #: ``None`` when the tool returned structured (non-text) content. A rewrite
    #: of a ``None`` result is a contract violation, not a silent no-op.
    result_text: str | None = None
    succeeded: bool = True


class ModelRequestBeforeInput(RuntimeContract):
    """Content-free description of the request about to reach the provider."""

    model_identifier: Annotated[str, Field(min_length=1, max_length=320)]
    execution_scope: Annotated[str, Field(min_length=1, max_length=320)]
    message_count: NonNegativeInt = 0
    #: Opaque strings read from the tool objects themselves. Never parsed.
    tool_names: tuple[str, ...] = ()
    system_prompt_digest: Annotated[str, Field(min_length=1, max_length=64)]


class PromptAssembleInput(RuntimeContract):
    """The assembled system prompt's identity, offered for augmentation."""

    model_identifier: Annotated[str, Field(min_length=1, max_length=320)]
    execution_scope: Annotated[str, Field(min_length=1, max_length=320)]
    system_prompt_digest: Annotated[str, Field(min_length=1, max_length=64)]
    tool_names: tuple[str, ...] = ()


class PolicyDecideAfterInput(RuntimeContract):
    """A tool-use policy decision that has ALREADY been made. Observe only.

    Carried as plain strings rather than the gate's enums so this package stays
    free of capability imports — and so a handler cannot construct a decision
    object the gate would accept.
    """

    tool_name: Annotated[str, Field(min_length=1, max_length=320)]
    action: Annotated[str, Field(min_length=1, max_length=64)]
    policy_kind: str | None = Field(default=None, max_length=64)
    mode: str | None = Field(default=None, max_length=64)


class RunLifecycleInput(RuntimeContract):
    """Run start / run end. Observe only."""

    run_id: Annotated[str, Field(min_length=1, max_length=320)]
    conversation_id: Annotated[str, Field(min_length=1, max_length=320)]
    org_id: Annotated[str, Field(min_length=1, max_length=320)]
    phase: HookPhase
    #: Terminal status on ``run.end``; ``None`` on ``run.start``.
    status: str | None = Field(default=None, max_length=64)


# --------------------------------------------------------------------------
# Outcomes. Closed enums with validated shapes; none of them can widen.
# --------------------------------------------------------------------------


class ToolExecuteBeforeAction(StrEnum):
    """What a ``tool.execute.before`` handler asked for.

    There is no ``ALLOW``: permitting a call is not something a hook does, it
    is what happens when no hook objects.
    """

    CONTINUE = "continue"
    REWRITE_ARGUMENTS = "rewrite_arguments"
    VETO = "veto"


class ToolExecuteBeforeOutcome(RuntimeContract):
    """Constrained return of a ``tool.execute.before`` handler."""

    action: ToolExecuteBeforeAction = ToolExecuteBeforeAction.CONTINUE
    arguments: dict[str, Any] | None = None
    veto_reason: str | None = Field(default=None, max_length=400)

    @model_validator(mode="after")
    def _shape_matches_action(self) -> "ToolExecuteBeforeOutcome":
        if self.action is ToolExecuteBeforeAction.REWRITE_ARGUMENTS:
            if self.arguments is None:
                raise ValueError("rewrite_arguments requires arguments")
            if self.veto_reason is not None:
                raise ValueError("rewrite_arguments must not carry a veto reason")
        elif self.action is ToolExecuteBeforeAction.VETO:
            if not (self.veto_reason or "").strip():
                raise ValueError("veto requires a reason")
            if self.arguments is not None:
                raise ValueError("veto must not carry arguments")
        elif self.arguments is not None or self.veto_reason is not None:
            raise ValueError("continue must not carry arguments or a veto reason")
        return self


class ToolExecuteAfterAction(StrEnum):
    """What a ``tool.execute.after`` handler asked for."""

    CONTINUE = "continue"
    REWRITE_RESULT = "rewrite_result"


class ToolExecuteAfterOutcome(RuntimeContract):
    """Constrained return of a ``tool.execute.after`` handler."""

    action: ToolExecuteAfterAction = ToolExecuteAfterAction.CONTINUE
    result_text: str | None = Field(default=None, max_length=MAX_REWRITTEN_RESULT_CHARS)

    @model_validator(mode="after")
    def _shape_matches_action(self) -> "ToolExecuteAfterOutcome":
        if self.action is ToolExecuteAfterAction.REWRITE_RESULT:
            if self.result_text is None:
                raise ValueError("rewrite_result requires result_text")
        elif self.result_text is not None:
            raise ValueError("continue must not carry result_text")
        return self


class PromptAssembleAction(StrEnum):
    """What a ``prompt.assemble`` handler asked for. Append is the only verb."""

    CONTINUE = "continue"
    APPEND_CONTEXT = "append_context"


class PromptAssembleOutcome(RuntimeContract):
    """Constrained return of a ``prompt.assemble`` handler."""

    action: PromptAssembleAction = PromptAssembleAction.CONTINUE
    appended_context: str | None = Field(
        default=None,
        max_length=MAX_APPENDED_CONTEXT_CHARS,
    )

    @model_validator(mode="after")
    def _shape_matches_action(self) -> "PromptAssembleOutcome":
        if self.action is PromptAssembleAction.APPEND_CONTEXT:
            if not (self.appended_context or "").strip():
                raise ValueError("append_context requires non-blank context")
        elif self.appended_context is not None:
            raise ValueError("continue must not carry appended context")
        return self


#: The single closed table mapping a phase to the type its handlers may return.
#: ``None`` means observe-only. The dispatcher validates against this table, so
#: a handler registered on an observe-only phase cannot return a decision
#: object of any kind and have it read.
PHASE_OUTCOME_TYPES: dict[HookPhase, type[RuntimeContract] | None] = {
    HookPhase.TOOL_EXECUTE_BEFORE: ToolExecuteBeforeOutcome,
    HookPhase.TOOL_EXECUTE_AFTER: ToolExecuteAfterOutcome,
    HookPhase.PROMPT_ASSEMBLE: PromptAssembleOutcome,
    HookPhase.MODEL_REQUEST_BEFORE: None,
    HookPhase.POLICY_DECIDE_AFTER: None,
    HookPhase.RUN_START: None,
    HookPhase.RUN_END: None,
}


class HookInvocationStatus(StrEnum):
    """Terminal classification of one hook invocation."""

    OK = "ok"
    FAILED = "failed"
    CONTRACT_VIOLATION = "contract_violation"


class HookInvocationRecord(RuntimeContract):
    """One observable hook invocation: who, which phase, how long, did it act."""

    hook_name: Annotated[str, Field(min_length=1, max_length=160)]
    phase: HookPhase
    duration_us: NonNegativeInt = 0
    modified: bool = False
    status: HookInvocationStatus = HookInvocationStatus.OK
    #: Exception class name only — never the message, which can carry payload.
    error_class: str | None = Field(default=None, max_length=160)


__all__ = [
    "MAX_APPENDED_CONTEXT_CHARS",
    "MAX_REWRITTEN_RESULT_CHARS",
    "PHASE_OUTCOME_TYPES",
    "HookInvocationRecord",
    "HookInvocationStatus",
    "HookPhase",
    "ModelRequestBeforeInput",
    "PolicyDecideAfterInput",
    "PromptAssembleAction",
    "PromptAssembleInput",
    "PromptAssembleOutcome",
    "RunLifecycleInput",
    "ToolExecuteAfterAction",
    "ToolExecuteAfterInput",
    "ToolExecuteAfterOutcome",
    "ToolExecuteBeforeAction",
    "ToolExecuteBeforeInput",
    "ToolExecuteBeforeOutcome",
]
