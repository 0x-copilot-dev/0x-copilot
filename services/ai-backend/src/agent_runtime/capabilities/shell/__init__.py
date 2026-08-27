"""Shell execution (``run_command``) — the contracts, the environment, the executor.

PRD: ``docs/plan/agent-execution/PRD-shell-execution.md``. This package is Phase
1's core: the model-facing schema and result, the deployment-trusted
configuration, the allowlist-built child environment, and the one subprocess
call site in ``agent_runtime``.

**The capability is OFF unless ``RUNTIME_ENABLE_SHELL_EXECUTION`` says otherwise.**

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

There is deliberately no re-export block and no per-module recap here: every
consumer imports from the submodule that authors the symbol, so both would be a
second place to edit that no reader reaches and nothing keeps honest.
"""

from __future__ import annotations
