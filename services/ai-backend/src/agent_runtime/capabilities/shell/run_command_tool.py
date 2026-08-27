"""The model-facing ``run_command`` tool boundary (§4.1, §7.2, §13).

A LangChain boundary and nothing else: it resolves the workspace label, hands
the call to the PEP (:mod:`agent_runtime.capabilities.shell.policy_gate`), and —
only if the PEP returned permission — builds the environment and calls the one
executor. It decides nothing about whether the command may run, reads no process
environment, chooses no shell, picks no cwd and sizes no timeout (all four are
deployment facts resolved in ``config`` and ``environment``), and opens no
socket — the policy snapshot it decides from was sealed onto the run context at
run-create (root ``CLAUDE.md``'s "never put a per-call HTTP hop on the tool
path").

THE ORDER, AND WHY EACH STEP IS WHERE IT IS
-------------------------------------------
1. **Schema validation** — LangChain validates into :class:`RunCommandInput`
   before this module sees anything, so NUL and the C0 range are already gone
   and the string the approval card renders is the string ``execve`` receives.
2. **Bind the workspace label.** A label, never a path, and never a fallback
   root (§4.2). This is also the §7.2 recheck, and its answer is threaded into
   the PDP as ``available`` rather than short-circuited here, so a grant
   withdrawn mid-run is denied by Stage 1 of the same decision every other call
   flows through. One gate, not two that have to agree.
3. **The timeout ceiling and the per-run budget**, both from deployment config
   the model cannot influence. Checked before the decision because refusing a
   call the deployment would not run anyway costs the human nothing; neither
   check can turn a refusal into permission.
4. **:meth:`ShellCommandPolicyGate.authorize`** — the never-list screen, then
   the PDP, then (on GATE) the approval card.
5. **Only then** the environment is built and the executor is called.

No arm of :meth:`RunCommandTool._authorized_call` spawns without a
``ShellAuthorization`` in hand, and that object has no constructor outside the
two post-decision arms of the PEP. That is the structural form of "no path
returns ALLOW without a decision".

Almost none of this is LangChain boilerplate: ``StructuredTool.from_function``
does the schema work, the only framework code is the six-line ``arun`` override,
and :class:`RunCommandTool` is the house adapter shape — a frozen dataclass of
run-scoped dependencies wrapped by a ``StructuredTool`` in a factory, as
``ListConnectedServersTool`` and ``SuggestMcpConnectorTool`` already are.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.shell.config import (
    ShellCommandBudget,
    ShellExecutionConfig,
)
from agent_runtime.capabilities.shell.contracts import (
    RunCommandInput,
    RunCommandResult,
    ShellCommandCancelled,
    ShellExecutionOutcome,
    ShellExecutionRequest,
    ShellExecutionStatus,
    ShellRefusal,
    ShellRefusalReason,
    ShellRefusedError,
)
from agent_runtime.capabilities.shell.descriptor import ShellCapability
from agent_runtime.capabilities.shell.environment import ShellEnvironmentBuilder
from agent_runtime.capabilities.shell.executor import ShellCommandExecutor
from agent_runtime.capabilities.shell.policy_gate import ShellCommandPolicyGate

logger = logging.getLogger(__name__)

#: The model-facing tool name. One constant, shared with the policy identity, so
#: a rename cannot leave ``builtin:shell:run_command`` pointing at nothing.
TOOL_NAME: Final = ShellCapability.OP

#: Deliberately short. Every character is resident on every model call, and the
#: four facts a model gets wrong about a shell — no state between calls, closed
#: stdin, no aliases, no undo — are stated once here and again in
#: ``SHELL_EXECUTE_GUIDANCE`` (§17), which is prose the model reads before it
#: ever composes a call.
TOOL_DESCRIPTION: Final = (
    "Run ONE shell command in a folder the user attached, and return its "
    "combined output and exit code. Every call is shown to the user for "
    "approval before it runs. No state carries between calls (no `cd` that "
    "persists, no shell functions or aliases), stdin is closed so interactive "
    "commands fail, and NOTHING a command changes can be undone by this app."
)


class _Note:
    """Model-facing sentences for the refusals this boundary itself makes.

    Authored constants for the same reason ``policy_gate._Note`` is: prose that
    reaches the model must not be interpolated from an exception or from
    deployment configuration. Each says whether retrying can succeed.
    """

    NO_WORKSPACE: Final = (
        "No attached folder is available to run a command in, so nothing was "
        "run. Ask the user to attach a folder and enable commands for it."
    )
    UNKNOWN_WORKSPACE: Final = (
        "There is no attached folder with that label, so nothing was run. "
        "Available: {labels}."
    )


@dataclass(frozen=True)
class BoundWorkspace:
    """One resolved place a command may run, chosen by the runtime.

    ``label`` is what the human sees on the card and what the model passes back;
    ``root`` and ``scratch_dir`` are host paths that never leave this process.
    Resolved from the run's granted roots, never from model text — which is why
    the label can be folded into a policy subject without a wildcard-injection
    question (see ``ShellCommandPolicyGate._policy_arguments``).
    """

    label: str
    root: Path
    scratch_dir: Path


@dataclass(frozen=True)
class WorkspaceBindingView:
    """The answer to "where may this call run", read at call time.

    Two fields because the two questions have different failure modes.
    ``labels`` is what is command-capable **now** — it drives the message that
    lets a model correct a wrong label in one turn, and an empty tuple is the
    §7.2 recheck failing. ``workspace`` is the requested label resolved, or
    ``None`` when it is not currently bindable.
    """

    labels: tuple[str, ...] = ()
    workspace: BoundWorkspace | None = None


class ShellWorkspaceBinding(Protocol):
    """Where a command may run (§7.1.2, §7.2, §7.3, §7.4).

    A port, not a class, because the answer is owned by the desktop grant lane
    and this package must not learn to read grants.

    THE TWO METHODS ANSWER TWO DIFFERENT QUESTIONS, ONE SEALED AND ONE LIVE, and
    the asymmetry is fail-closed in both directions. :meth:`sealed_labels` is the
    run-start seal (§7.4), so turning shell access ON mid-run cannot
    retro-authorize a run the user started without it; :meth:`resolve` is the
    call-time recheck (§7.2), so turning it OFF mid-run does take effect.

    Both are total. A binding that cannot answer returns the narrowing answer —
    no labels, no workspace — rather than raising into the tool path.
    """

    def sealed_labels(self) -> tuple[str, ...]:
        """Command-capable labels as of run start. Empty ⇒ no tool is built."""
        ...

    async def resolve(self, label: str | None) -> WorkspaceBindingView:
        """Re-read the grants and resolve one label.

        ``label=None`` means "the model did not name one": resolve the sole
        command-capable workspace when there is exactly one, and otherwise
        return the labels with no workspace, so the caller can refuse rather
        than choose a folder nobody named. A label that is no longer bindable
        yields ``workspace=None`` — never a fallback root.
        """
        ...


#: LangChain's per-call ``tool_call_id``, captured at :meth:`arun` and read by
#: the coroutine.
#:
#: **Not a hand-rolled ``InjectedToolCallId``**: measured against the installed
#: ``langchain_core``, ``BaseTool._parse_input`` raises ``ValueError`` whenever
#: ``tool_call_id is None``, so that field would refuse **any** invocation that
#: is not a full ``ToolCall`` envelope — contradicting
#: ``ShellCommandPolicyGate._approval_id``, whose ``str | None`` signature exists
#: so the lane stays total. ``arun`` capture is total; the field is not. (The two
#: likelier-sounding reasons are both false there: the field does not reach the
#: model, and a model cannot forge the id.)
_TOOL_CALL_ID: ContextVar[str | None] = ContextVar(
    "shell_run_command_tool_call_id", default=None
)


class _RunCommandTool(StructuredTool):
    """A ``StructuredTool`` that remembers which tool call it is serving.

    The approval id must be **deterministic across the park→resume replay** and
    **unique per call in a run** (``ShellCommandPolicyGate._approval_id``), and
    only LangChain knows an id satisfying both. Why it is read here rather than
    from an injected schema field is measured at :data:`_TOOL_CALL_ID`.
    """

    async def arun(  # type: ignore[override]
        self,
        tool_input: Any,
        *args: Any,
        tool_call_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Bind the call id for the duration of this invocation."""

        token = _TOOL_CALL_ID.set(tool_call_id)
        try:
            return await super().arun(
                tool_input, *args, tool_call_id=tool_call_id, **kwargs
            )
        finally:
            _TOOL_CALL_ID.reset(token)


@dataclass(frozen=True)
class RunCommandTool:
    """Adapter wrapped by LangChain's ``StructuredTool`` in the factory.

    The run-scoped dependencies are **fields**, not parameters threaded through a
    closure — the shape the other builtins already use, and the reason the
    seven-dependency bundle is written once rather than at every call site.
    Frozen for the same reason they are: a tool that could be re-pointed at
    another workspace or budget mid-run would make the §7.4 seal a convention
    rather than a property.
    """

    config: ShellExecutionConfig
    binding: ShellWorkspaceBinding
    policy_gate: ShellCommandPolicyGate
    budget: ShellCommandBudget
    executor: ShellCommandExecutor
    environment: ShellEnvironmentBuilder
    env_source: Mapping[str, str] | None = None

    async def ainvoke(
        self,
        command: str,
        workspace: str | None = None,
        timeout_s: int | None = None,
    ) -> str:
        """One call, from validated arguments to a JSON result string.

        Every exit is a :class:`RunCommandResult`. :class:`ShellRefusedError` is
        caught exactly once — here — and projected, which is what lets every
        rule below raise at the point that makes the decision instead of
        returning a union some caller can forget to inspect.
        """

        try:
            return await self._authorized_call(command, workspace, timeout_s)
        except ShellRefusedError as refused:
            return self._json(refused.refusal.as_result())

    async def _authorized_call(
        self, command: str, workspace: str | None, timeout_s: int | None
    ) -> str:
        """The permitted path. Every refusal leaves by raising, never returning."""

        self.config.require_enabled()
        bound = await self._bind(workspace)
        resolved_timeout_s = self.config.resolve_timeout_s(timeout_s)
        # Read, not consumed: a command the human declines must not spend the
        # run's allowance. The claim happens after the decision.
        self.budget.require_available()
        authorization = await self.policy_gate.authorize(
            command=command,
            workspace_label=bound.label if bound is not None else (workspace or ""),
            available=bound is not None,
            tool_call_id=_TOOL_CALL_ID.get(),
        )
        if bound is None:  # pragma: no cover - Stage 1 denies before this
            # Unreachable by construction: ``available=False`` makes the PDP's
            # availability stage DENY, which raises above. Asserted rather than
            # assumed, because the alternative to this branch is spawning a
            # command with no bound directory.
            raise ShellRefusedError(
                ShellRefusal.unavailable(
                    ShellRefusalReason.WORKSPACE_UNAVAILABLE, _Note.NO_WORKSPACE
                )
            )

        logger.info(
            "shell.run_command.authorized basis=%s workspace=%s approval=%s",
            authorization.basis.value,
            bound.label,
            authorization.approval_id or "-",
        )
        self.budget.consume()
        request = ShellExecutionRequest(
            command=command,
            cwd=bound.root,
            timeout_s=resolved_timeout_s,
            env=self.environment.build(
                bound_root=bound.root,
                scratch_dir=bound.scratch_dir,
                source=self.env_source,
            ),
            shell_path=self.config.shell_path,
            output_cap_bytes=self.config.combined_output_preview_bytes,
            # Phase 1 keeps no spill file: overflow is counted and dropped, and
            # the truncation notice says so without offering a ref. §13's
            # scratch-backed spill needs a virtual path minted from the agent's
            # own scratch, which is the seam this argument becomes.
            spill_path=None,
        )
        try:
            outcome = await self.executor.run(request)
        except ShellCommandCancelled as cancelled:
            # The executor already killed the process group. The partial output
            # is answered rather than discarded (AC5.2): a user who cancels a
            # command still gets to see what it printed.
            outcome = cancelled.outcome
        return self._json(self._result(outcome, bound.label, resolved_timeout_s))

    async def _bind(self, requested: str | None) -> BoundWorkspace | None:
        """Resolve the label, or refuse — never fall back to another root.

        Returns ``None`` for "the workspace went away", which the caller threads
        into the PDP as ``available=False`` so that denial is the PDP's and not a
        second gate beside it. An unknown LABEL is different in kind: it is a
        malformed argument rather than a policy question, and it is refused here
        with the labels that do exist, so the model can correct itself in one
        turn instead of guessing.
        """

        view = await self.binding.resolve(requested)
        if not view.labels:
            raise ShellRefusedError(
                ShellRefusal.unavailable(
                    ShellRefusalReason.NO_WRITABLE_WORKSPACE, _Note.NO_WORKSPACE
                )
            )
        if requested is None:
            # Ambiguous, and picking for the model would run a command in a
            # folder nobody named.
            if len(view.labels) != 1:
                raise self._unknown_workspace(view.labels)
        elif requested not in view.labels:
            raise self._unknown_workspace(view.labels)
        return view.workspace

    @staticmethod
    def _unknown_workspace(labels: tuple[str, ...]) -> ShellRefusedError:
        """One refusal for both ways a label fails to name a workspace.

        "Named nothing, several on offer" and "named one that is not on offer"
        need the same thing from the model: the closed set, so it can correct
        itself in one turn.
        """

        return ShellRefusedError(
            ShellRefusal.refused(
                ShellRefusalReason.UNKNOWN_WORKSPACE,
                _Note.UNKNOWN_WORKSPACE.format(labels=", ".join(labels)),
            )
        )

    @staticmethod
    def _result(
        outcome: ShellExecutionOutcome, label: str, timeout_s: int
    ) -> RunCommandResult:
        """Project one execution outcome into the model-facing result.

        The workspace LABEL is attached here and the host path is not — the
        result, the event payload and the transcript all carry the label only.

        ``output_total_bytes`` is the one field that does not pass straight
        through: the outcome always counts the true total, while the result
        carries it only alongside ``truncated``, because there it is half of a
        truncation notice rather than a statistic.

        A timeout is the one status that has to SAY something. It reports
        ``exit_code=None``, so a model reading only the exit code cannot tell a
        timeout apart from a refusal — it has to be told, in prose, that the
        command was stopped and that a larger ``timeout_s`` is available (AC5.4).
        That sentence is :meth:`ShellCommandExecutor.timeout_note`, and this is
        its one caller.
        """

        return RunCommandResult(
            status=outcome.status,
            exit_code=outcome.exit_code,
            output=outcome.output,
            truncated=outcome.truncated,
            output_total_bytes=(
                outcome.output_total_bytes if outcome.truncated else None
            ),
            duration_ms=outcome.duration_ms,
            workspace=label,
            exit_note=(
                ShellCommandExecutor.timeout_note(timeout_s)
                if outcome.status is ShellExecutionStatus.TIMEOUT
                else None
            ),
        )

    @staticmethod
    def _json(result: RunCommandResult) -> str:
        """Serialise the result, dropping absent fields.

        ``exclude_none`` costs nothing in fidelity — every field it drops is
        genuinely absent — and a model reading ``exit_code: 0`` still sees it,
        because zero is not ``None``.
        """

        return result.model_dump_json(exclude_none=True)


class RunCommandToolFactory:
    """Build the model-visible ``run_command`` tool, or return ``None``.

    Separate from :class:`RunCommandTool` because the two answer different
    questions: this one is "may a shell exist for this run at all", asked once;
    that one is "may this command run", asked per call.

    ``None`` is not an error path — it is the default posture. Three of §7.1's
    four prerequisites are checked here (the deployment flag, a command-capable
    workspace, and — through the binding, which only reports workspaces whose
    grant carries it — the per-workspace enablement the user set in Settings);
    the fourth, ``ENTERPRISE_DEPLOYMENT_PROFILE == single_user_desktop``, is the
    composition root's, because it is a fact about the deployment rather than
    about this run. Any one missing ⇒ no tool in the model's list ⇒
    ``NO_SHELL_EXECUTE_GUIDANCE`` ships instead (§17).

    Registration is checked twice on purpose: the tool is absent when the
    capability is off, AND ``ShellExecutionConfig.require_enabled`` fires inside
    the call, so a future second call path that reaches
    :meth:`RunCommandTool.ainvoke` without going through ``build`` still refuses.
    """

    @classmethod
    def build(
        cls,
        *,
        config: ShellExecutionConfig,
        binding: ShellWorkspaceBinding,
        policy_gate: ShellCommandPolicyGate,
        budget: ShellCommandBudget,
        executor: ShellCommandExecutor | None = None,
        environment: ShellEnvironmentBuilder | None = None,
        env_source: Mapping[str, str] | None = None,
    ) -> StructuredTool | None:
        """Return the tool when every run-scoped prerequisite holds."""

        if not config.enabled:
            return None
        if not binding.sealed_labels():
            # No workspace is command-capable for this run: the user attached
            # nothing writable, or attached folders but enabled commands on
            # none of them. Either way the model must not be told it has a
            # shell it cannot use.
            return None

        tool = RunCommandTool(
            config=config,
            binding=binding,
            policy_gate=policy_gate,
            budget=budget,
            executor=executor or ShellCommandExecutor(),
            environment=environment or ShellEnvironmentBuilder(),
            env_source=env_source,
        )
        return _RunCommandTool.from_function(
            coroutine=tool.ainvoke,
            name=TOOL_NAME,
            description=TOOL_DESCRIPTION,
            args_schema=RunCommandInput,
        )


__all__ = [
    "BoundWorkspace",
    "RunCommandTool",
    "RunCommandToolFactory",
    "ShellWorkspaceBinding",
    "WorkspaceBindingView",
    "TOOL_DESCRIPTION",
    "TOOL_NAME",
]
