"""Typed tool exceptions that the runtime treats as run-fatal.

A tool that raises :class:`RunFatalToolError` (or any subclass) ends the
run via :meth:`RunTerminationCoordinator.terminate`. Every other
exception is routed by :class:`DefaultToolErrorPolicy` to
``SURFACE_TO_LLM``: the error text is sanitized, structured hints are
extracted, and the result is handed back to the agent as a
``ToolMessage`` so the LLM can reason about it (retry with corrected
args, switch tools, give up).

Subclass when:
- the failure is a policy violation the LLM cannot legitimately work
  around (budget exhaustion, scope/auth denial, tenant isolation)
- the failure indicates the tool is misconfigured at the orchestration
  layer (not at the LLM call layer) — e.g. the auth flow itself is
  unrecoverable

Do NOT subclass for:
- transient network errors (let the default policy surface them so the
  LLM can retry)
- validation errors on tool args (let the default policy hand the
  validation hints to the LLM)
- generic runtime errors (default policy)
"""

from __future__ import annotations


class RunFatalToolError(Exception):
    """Marker base for tool errors that must end the run.

    Carries two messages:

    * ``safe_summary`` — the public reason the run was failed. Surfaced
      in the ``RUN_FAILED`` event payload and (where applicable) the
      audit trail. Must never leak internal IDs / paths / secrets.
    * ``audit_summary`` — the operational reason for the audit log only;
      may carry slightly more detail than ``safe_summary`` but still
      must not contain raw stack traces or unredacted secrets.

    The exception is NOT routed through :class:`ErrorSanitizer` — the
    caller is responsible for already-safe strings here. ``str(exc)``
    returns ``safe_summary`` so it composes well with default formatting.
    """

    def __init__(
        self,
        safe_summary: str,
        *,
        audit_summary: str | None = None,
    ) -> None:
        super().__init__(safe_summary)
        self.safe_summary = safe_summary
        self.audit_summary = audit_summary or safe_summary


class BudgetExceeded(RunFatalToolError):
    """Per-tool / per-run budget hard cap reached.

    Raised by :class:`ToolBudgetGuardedTool` when admission is rejected
    under HARD enforcement. The LLM should not be given a chance to retry
    — the budget exists exactly to bound spend regardless of what the
    LLM thinks.
    """


class AuthDenied(RunFatalToolError):
    """Capability auth gate denied a tool / MCP / skill.

    Raised when the run's identity lacks the required scope or role for
    the requested capability. Surfacing this to the LLM would invite
    prompt-injection-driven scope-escalation attempts; failing fast is
    the safer default.
    """


class PolicyViolation(RunFatalToolError):
    """A request violates an explicit policy guard (DLP, content, etc.).

    The LLM should not be retried after a policy hit — the input or
    intent itself is the problem.
    """


class TenantIsolationViolation(RunFatalToolError):
    """A capability would cross a tenant boundary.

    Hard-stops the run; never silently proceed past an isolation check.
    """


__all__ = (
    "AuthDenied",
    "BudgetExceeded",
    "PolicyViolation",
    "RunFatalToolError",
    "TenantIsolationViolation",
)
