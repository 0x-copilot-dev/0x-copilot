"""Run-scoped composition for the model-visible ``run_command`` tool (§7.1, §7.4).

Mirrors :mod:`runtime_worker.sandbox_composition`: it resolves prerequisites and
returns ``None`` when any is missing. ``None`` is the default posture, not an
error — off that path the model's tool list is byte-identical to today's and
``NO_SHELL_EXECUTE_GUIDANCE`` ships instead (§17).

FOUR INDEPENDENT PREREQUISITES, ANY MISSING ⇒ NO TOOL (§7.1)
------------------------------------------------------------
1. ``RUNTIME_ENABLE_SHELL_EXECUTION`` — the deployment flag, read once from the
   process environment by :meth:`ShellExecutionConfig.from_env`.
2. ``ENTERPRISE_DEPLOYMENT_PROFILE == "single_user_desktop"`` — the same gate
   ``sandbox_composition`` uses. A command runs on the machine the app is
   installed on; there is no such machine in a hosted deployment.
3. At least one attached workspace that is **writable** AND carries the
   per-workspace shell enablement the user set in Settings (§7.3). Both, and
   they are separate authorities: file access and command execution are not
   supersets of one another.
4. A never-list. Not in the PRD's list, and required here anyway: a command
   lane whose §9.3 screen is missing has no floor at all, and the honest
   response to "the screen is not wired" is no capability rather than an
   unscreened one. See :func:`_resolve_never_list`.

THE SEAL, AND THE RECHECK (§7.2, §7.4)
--------------------------------------
The command-capable labels are read ONCE here, at run start, and sealed. A
workspace the user enables mid-run does not appear in this run (AC10.3) — the
same rule the bypass posture follows, so a Settings change mid-flight cannot
retro-authorize a run the user started under a different one. Turning it OFF
mid-run *is* honoured, because :meth:`BrokerWorkspaceBinding.resolve` re-reads
the live snapshot on every call and intersects it with the seal. The asymmetry
is deliberate and fail-closed in both directions.

WHAT THIS MODULE DOES NOT DO
----------------------------
It fetches no policy. The ``execute`` axis rides the same run-create snapshot
the read / write / destructive modes already ride, sealed onto
``AgentRuntimeContext.user_policies_json``, and the PEP reads it from there —
zero HTTP hops are added to the tool path (root ``CLAUDE.md``).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Final

from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.shell.config import (
    ShellCommandBudget,
    ShellExecutionConfig,
)
from agent_runtime.capabilities.shell.policy_gate import (
    CommandNeverList,
    ShellCommandPolicyGate,
)
from agent_runtime.capabilities.shell.run_command_tool import (
    BoundWorkspace,
    RunCommandToolFactory,
    WorkspaceBindingView,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.surfaces_v2.gate import ToolAccessGate

logger = logging.getLogger(__name__)

_DESKTOP_PROFILE: Final = "single_user_desktop"
_DEPLOYMENT_PROFILE_ENV: Final = "ENTERPRISE_DEPLOYMENT_PROFILE"

#: The concrete screen this composition binds. Named as a constant because the
#: failure mode of a missing screen is SILENT — the capability simply does not
#: appear — so the contract has to be greppable from both sides.
_NEVER_LIST_TYPE: Final = "CommandNeverList"

#: Returns the run's active workspace mounts. Async because it is a live read of
#: the desktop grant snapshot, which is what makes the §7.2 recheck real rather
#: than a re-inspection of something cached at run start.
MountsProvider = Callable[[], Awaitable[Sequence[object]]]


class BrokerWorkspaceBinding:
    """Where a command may run, sealed at run start and re-read per call.

    Implements ``ShellWorkspaceBinding`` structurally. The two questions it
    answers are deliberately different reads:

    * :meth:`sealed_labels` — the run-start seal (§7.4). Registration asks this,
      and only this, so the tool's existence is decided by the posture the user
      started the run under.
    * :meth:`resolve` — the live grant snapshot, intersected with the seal
      (§7.2). A grant detached, made read-only, or shell-disabled mid-run drops
      out of the intersection and the call is denied by the PDP's availability
      stage; a workspace enabled mid-run cannot enter, because it is not in the
      seal.

    Total by construction. A broker that is unreachable, a snapshot that fails
    to decode, an unusable root — every one of them yields no labels, which
    reads as "unavailable", never as "allowed".
    """

    __slots__ = ("_sealed", "_provider", "_scratch_dir")

    def __init__(
        self,
        *,
        sealed_labels: Sequence[str],
        mounts_provider: MountsProvider,
        scratch_dir: Path,
    ) -> None:
        self._sealed = tuple(sealed_labels)
        self._provider = mounts_provider
        self._scratch_dir = scratch_dir

    def sealed_labels(self) -> tuple[str, ...]:
        """The command-capable labels as of run start."""

        return self._sealed

    async def resolve(self, label: str | None) -> WorkspaceBindingView:
        """Re-read the grants, intersect with the seal, resolve one label."""

        live = {
            workspace.label: workspace
            for workspace in await self._command_capable()
            if workspace.label in self._sealed
        }
        # Ordered by the seal so the labels a model is shown do not reshuffle
        # between calls just because the broker reordered its snapshot.
        labels = tuple(name for name in self._sealed if name in live)
        if label is None:
            return WorkspaceBindingView(
                labels=labels,
                workspace=live[labels[0]] if len(labels) == 1 else None,
            )
        return WorkspaceBindingView(labels=labels, workspace=live.get(label))

    async def _command_capable(self) -> tuple[BoundWorkspace, ...]:
        """Live mounts that may run a command, as bound workspaces."""

        try:
            mounts = await self._provider()
        except Exception:  # noqa: BLE001 - a failed read must narrow, not raise
            # WARNING, not debug: this branch disables the command capability
            # for the rest of the run, and an invisible outage here reads to the
            # user as "the agent forgot it can run commands".
            logger.warning(
                "shell.binding.grants_unavailable "
                "(run_command is UNAVAILABLE for this run)",
                exc_info=True,
            )
            return ()
        return command_capable_workspaces(mounts, scratch_dir=self._scratch_dir)


def command_capable_workspaces(
    mounts: Sequence[object], *, scratch_dir: Path
) -> tuple[BoundWorkspace, ...]:
    """Project workspace mounts onto the ones a command may run in.

    THREE conditions, each independent and each able to say no on its own:

    * a usable host root — a mount whose root the classifier will not resolve is
      dropped rather than guessed at, the same rule
      ``WorkspaceMountTable.granted_roots`` follows;
    * **writable** — a read-only grant sites no command, because a command is
      not a read and this layer cannot tell which is which; and
    * **shell-enabled** — the per-workspace flag the user set (§7.3), which is
      ``False`` on every mount built by any caller that predates it.

    The mount NAME is the label, not ``mount.label``: names are unique by
    construction within a run (``WorkspaceMountTable`` dedupes them) while two
    grants can carry the same human label, and a label that resolves to two
    folders is a card that cannot say where the command will run.
    """

    workspaces: list[BoundWorkspace] = []
    for mount in mounts:
        host_root = getattr(mount, "host_root", None)
        if not host_root:
            continue
        if getattr(mount, "mode", "read_only") == "read_only":
            continue
        if not getattr(mount, "shell_enabled", False):
            continue
        classified = mount.classified_host_root()  # type: ignore[attr-defined]
        if not classified.is_host:
            continue
        workspaces.append(
            BoundWorkspace(
                label=str(mount.name),  # type: ignore[attr-defined]
                root=Path(classified.canonical),
                scratch_dir=scratch_dir,
            )
        )
    return tuple(workspaces)


class ShellWorkerBundle:
    """Compose ``run_command`` for one run, or return ``None``.

    Constructed by the run handler, which owns the broker lane and the run's
    approval gate. Nothing here is a test switch: every argument is a real
    authority, and a missing one produces no tool rather than a weaker one.
    """

    @classmethod
    async def compose(
        cls,
        *,
        runtime_context: AgentRuntimeContext,
        conversation_id: str,
        gate: ToolAccessGate | None,
        mounts_provider: MountsProvider,
        env: Mapping[str, str] | None = None,
        never_list: CommandNeverList | None = None,
    ) -> StructuredTool | None:
        """Resolve every prerequisite and build the tool, or return ``None``."""

        source = env if env is not None else os.environ
        if (source.get(_DEPLOYMENT_PROFILE_ENV) or "").strip() != _DESKTOP_PROFILE:
            return None
        config = ShellExecutionConfig.from_env(source)
        if not config.enabled:
            return None
        screen = never_list or _resolve_never_list()
        if screen is None:
            return None
        try:
            sealed = await mounts_provider()
        except Exception:  # noqa: BLE001 - no grants read ⇒ no capability
            logger.warning("shell.compose.grants_unavailable", exc_info=True)
            return None
        scratch_dir = _scratch_dir(conversation_id, env=source)
        workspaces = command_capable_workspaces(sealed, scratch_dir=scratch_dir)
        if not workspaces:
            # Attached folders may exist; none of them is writable AND
            # shell-enabled. The model must not be told it has a shell it has
            # nowhere to use.
            return None
        binding = BrokerWorkspaceBinding(
            sealed_labels=tuple(workspace.label for workspace in workspaces),
            mounts_provider=mounts_provider,
            scratch_dir=scratch_dir,
        )
        return RunCommandToolFactory.build(
            config=config,
            binding=binding,
            policy_gate=ShellCommandPolicyGate(
                runtime_context=runtime_context,
                never_list=screen,
                gate=gate,
            ),
            budget=ShellCommandBudget(config.max_commands_per_run),
            env_source=source,
        )


def _resolve_never_list() -> CommandNeverList | None:
    """Load the shipped never-list, or ``None`` when it is not wired.

    FAIL-CLOSED, AND DELIBERATELY QUIET IN ONLY ONE DIRECTION. The §9.3 screen
    is the one control that refuses a command with no card to click past, so a
    runtime that cannot load it must not offer the capability at all. Returning
    ``None`` here removes the tool; the alternative — building the PEP with a
    permissive stand-in — would ship an unscreened shell that looked identical
    from the outside, which is the worst of the available outcomes.

    Imported inside the function rather than at module scope so an image where
    the never-list is absent still loads this composition root and logs which
    half is missing, instead of failing the whole worker import.
    """

    try:
        from agent_runtime.capabilities.shell.never_list import (  # noqa: PLC0415
            CommandNeverList as ShippedNeverList,
        )
    except ImportError:
        logger.warning(
            "shell.compose.never_list_absent module=%s type=%s "
            "(run_command is DISABLED: a command lane without its §9.3 screen "
            "is not shipped)",
            "agent_runtime.capabilities.shell.never_list",
            _NEVER_LIST_TYPE,
        )
        return None
    return ShippedNeverList()


def _scratch_dir(conversation_id: str, *, env: Mapping[str, str]) -> Path:
    """This chat's scratch directory — the child's ``TMPDIR`` (§11.3).

    The agent's own writable area, not the workspace: a command's temporary
    files land where the agent's other ephemera land, under
    ``$COPILOT_HOME/.tmp/<conversation_id>/``, and are swept by the same
    retention path. Falls back to the scratch root when the conversation id will
    not make a safe path segment — a real directory is required, because an
    unset ``TMPDIR`` sends the child to the system temp with the user's own
    permissions.

    The conversation id is passed in rather than read off the run context,
    because ``AgentRuntimeContext`` does not carry one — the worker's run
    command does, and inventing a second spelling of the id here would put a
    command's temporary files somewhere the retention sweep does not look.
    """

    from agent_runtime.capabilities.desktop.agent_scratch import (  # noqa: PLC0415
        ScratchIdError,
        agent_scratch_root,
    )

    root = agent_scratch_root(env)
    try:
        return root.conversation(conversation_id).provision().path
    except (ScratchIdError, OSError, RuntimeError):
        logger.warning("shell.compose.scratch_unavailable", exc_info=True)
        return root.path


__all__ = [
    "BrokerWorkspaceBinding",
    "MountsProvider",
    "ShellWorkerBundle",
    "command_capable_workspaces",
]
