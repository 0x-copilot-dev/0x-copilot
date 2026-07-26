"""Typed tool exceptions carrying an already-safe, model-visible message.

Two families live here, split by what they do to the run:

* :class:`RunFatalToolError` (and its subclasses) **ends the run** via
  :meth:`RunTerminationCoordinator.terminate`.
* :class:`ToolBudgetRejected` is **non-fatal**: the call is refused, but
  the refusal is handed to the model as a ``ToolMessage`` so it can
  finalize with the work it has already completed.

Every other exception is routed by :class:`DefaultToolErrorPolicy` to
``SURFACE_TO_LLM``: the error text is sanitized, structured hints are
extracted, and the result is handed back to the agent as a
``ToolMessage`` so the LLM can reason about it (retry with corrected
args, switch tools, give up).

Subclass :class:`RunFatalToolError` when:
- the failure is a policy violation the LLM cannot legitimately work
  around AND letting it keep reasoning is itself unsafe (scope/auth
  denial, tenant isolation, DLP)
- the failure indicates the tool is misconfigured at the orchestration
  layer (not at the LLM call layer) — e.g. the auth flow itself is
  unrecoverable

Do NOT subclass :class:`RunFatalToolError` for:
- transient network errors (let the default policy surface them so the
  LLM can retry)
- validation errors on tool args (let the default policy hand the
  validation hints to the LLM)
- generic runtime errors (default policy)
- **a spend cap the model can respect by simply stopping.** Refusing
  the call already bounds the spend; killing the run on top of that
  throws away every result the run had accumulated. Use
  :class:`ToolBudgetRejected` and let the model wrap up.
"""

from __future__ import annotations


class SafeToolError(Exception):
    """Base for tool errors whose message is authored safe at the raise site.

    ``safe_summary`` is public: it reaches the model as a ``ToolMessage``
    and/or the ``RUN_FAILED`` event payload. It is deliberately NOT
    routed through :class:`ErrorSanitizer` — the raiser is responsible
    for a string that carries no internal IDs / paths / secrets.
    ``str(exc)`` returns ``safe_summary`` so it composes well with
    default formatting.
    """

    def __init__(self, safe_summary: str) -> None:
        super().__init__(safe_summary)
        self.safe_summary = safe_summary


class ToolBudgetRejected(SafeToolError):
    """A tool call was refused by a hard budget cap — the run continues.

    Raised by :class:`ToolBudgetGuardedTool` when admission is rejected
    under HARD enforcement. The inner tool never executes, so the cap has
    already done its job: no further spend is incurred on that tool.

    The refusal is surfaced to the model as a tool result rather than
    raised through the stream, because the model's correct response is
    to stop calling that tool and answer with what it has. Failing the
    run instead would discard every completed tool result in the run —
    a strictly worse outcome for the same enforced spend.

    A run that keeps calling tools past the cap is bounded separately by
    :class:`ToolBudgetGuard`'s surfaced-rejection allowance, which
    escalates to :class:`BudgetExceeded` once the model has clearly
    stopped respecting the directive.
    """


class RunFatalToolError(SafeToolError):
    """Marker base for tool errors that must end the run.

    Carries two messages:

    * ``safe_summary`` — the public reason the run was failed. Surfaced
      in the ``RUN_FAILED`` event payload and (where applicable) the
      audit trail. Must never leak internal IDs / paths / secrets.
    * ``audit_summary`` — the operational reason for the audit log only;
      may carry slightly more detail than ``safe_summary`` but still
      must not contain raw stack traces or unredacted secrets.
    """

    def __init__(
        self,
        safe_summary: str,
        *,
        audit_summary: str | None = None,
    ) -> None:
        super().__init__(safe_summary)
        self.audit_summary = audit_summary or safe_summary


class BudgetExceeded(RunFatalToolError):
    """A run kept calling tools after its budget refusals were surfaced.

    This is the backstop, not the normal path. The normal path for a
    hard cap is :class:`ToolBudgetRejected`: refuse the call, tell the
    model to finalize, keep the run alive. Only when the model has
    ignored that directive past
    :class:`ToolBudgetGuard`'s surfaced-rejection allowance does the
    guard escalate here, so a model stuck in a call-reject-call loop
    cannot spin indefinitely.

    Also raised for a budget decision variant the guard does not
    recognize — an unknown variant fails closed rather than admitting.
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
    "SafeToolError",
    "TenantIsolationViolation",
    "ToolBudgetRejected",
)
