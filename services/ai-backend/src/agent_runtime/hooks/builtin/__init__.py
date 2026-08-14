"""The hooks the runtime registers on itself.

Everything under :mod:`agent_runtime.hooks` above this package is the *seam*:
typed phases, a dispatcher, a registry. This subpackage is the seam's first
in-product **consumer**, and the dependency runs one way only — a builtin
imports the seam, the seam never imports a builtin.

There is exactly one builtin today, and it is installed from a single
chokepoint (``RuntimeRunHandler.__init__``) so every path that can execute a
run — the worker process, the API's in-process worker, a test harness that
constructs the handler — gets it without three separate wiring edits.
"""

from agent_runtime.hooks.builtin.tool_observability import (
    HOOK_NAME,
    ToolCallObservation,
    ToolCallObservationContext,
    ToolCallObservationLedger,
    ToolCallObservationSummary,
    install_builtin_hooks,
)

__all__ = [
    "HOOK_NAME",
    "ToolCallObservation",
    "ToolCallObservationContext",
    "ToolCallObservationLedger",
    "ToolCallObservationSummary",
    "install_builtin_hooks",
]
