"""Shell execution (``run_command``) — the contracts, the environment, the executor.

PRD: ``docs/plan/agent-execution/PRD-shell-execution.md``. This package is Phase
1's core: the model-facing schema and result, the deployment-trusted
configuration, the allowlist-built child environment, and the one subprocess
call site in ``agent_runtime``.

**The capability is OFF unless ``RUNTIME_ENABLE_SHELL_EXECUTION`` says otherwise**,
and nothing here registers a tool with any factory or catalog — binding,
the never-list, the PDP descriptor and the tool boundary are separate modules.
Landing before the wiring is deliberate and tracked, not an oversight.

**Every command asks.** There is no auto-approve path in this phase and
``irreversible: true`` rides every approval card, so no command is approvable in
one click. The one exception to "asks" is the direction that costs the command
rather than a click: a never-list hit returns a refusal with **no approval card
at all**, so there is nothing to click through.

**No OS-level sandboxing exists in this phase, and this package does not pretend
otherwise.** A command runs as the user, with the user's permissions and the
user's real ``HOME``, and the network is unrestricted. The bound working
directory constrains where a command *starts*, not where it can reach. Those
residual risks are documented in-product and deferred to Phase 2 (scratch
``HOME`` plus SPIKE-S1's per-platform confinement). Half-building a sandbox here
would be worse than not having one, because a partial confinement reads as a
boundary.

What each module owns:

``contracts``
    ``RunCommandInput`` / ``RunCommandResult`` and the executor's own IO. The
    place where "the approval card and the process can never disagree" is
    enforced.
``config``
    ``ShellExecutionConfig.from_env`` — everything the model is forbidden to
    influence, resolved once from the process environment, plus the per-run
    command budget.
``environment``
    The child environment, built by **allowlist**. Never ``os.environ``.
``executor``
    Spawn, bound, reap. Timeouts kill the process group, not just the child.
"""

from __future__ import annotations

from agent_runtime.capabilities.shell.config import (
    ShellCommandBudget,
    ShellExecutionConfig,
)
from agent_runtime.capabilities.shell.contracts import (
    RunCommandInput,
    RunCommandResult,
    ShellCommandCancelled,
    ShellContract,
    ShellExecutionOutcome,
    ShellExecutionRequest,
    ShellExecutionStatus,
    ShellRefusal,
    ShellRefusalReason,
    ShellRefusedError,
    ShellStatusGroups,
)
from agent_runtime.capabilities.shell.environment import (
    ShellEnvironment,
    ShellEnvironmentBuilder,
)
from agent_runtime.capabilities.shell.executor import ShellCommandExecutor

__all__ = [
    "RunCommandInput",
    "RunCommandResult",
    "ShellCommandBudget",
    "ShellCommandCancelled",
    "ShellCommandExecutor",
    "ShellContract",
    "ShellEnvironment",
    "ShellEnvironmentBuilder",
    "ShellExecutionConfig",
    "ShellExecutionOutcome",
    "ShellExecutionRequest",
    "ShellExecutionStatus",
    "ShellRefusal",
    "ShellRefusalReason",
    "ShellRefusedError",
    "ShellStatusGroups",
]
