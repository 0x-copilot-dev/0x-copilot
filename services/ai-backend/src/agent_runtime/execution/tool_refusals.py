"""Tool refusals that are policy decisions rather than faults.

A :class:`~agent_runtime.execution.tool_errors.ToolBudgetRejected` is refused
*before* invocation: the inner tool never runs, so nothing was attempted and
nothing broke. It is the tool-call analogue of the workspace tombstone answer
that :mod:`agent_runtime.capabilities.workspace.policy_answers` rescues from the
failure taxonomy, and it needs the same rescue for the same reason — the runtime
already agrees a refusal is not a failure everywhere except on the wire:

* :class:`~agent_runtime.execution.tool_outcomes.ToolOutcome` gives it its own
  ``REJECTED`` member, and ``TOOL_FAILURE_STATUSES`` deliberately excludes it —
  "a pre-invocation gate, not an in-flight failure".
* :class:`~agent_runtime.execution.tool_error_policy.DefaultToolErrorPolicy`
  routes it to ``SURFACE_TO_LLM`` and declares ``retryable: False``.
* ``ToolBudgetRejected`` is deliberately NOT a ``RunFatalToolError``.

The gap this closes is the last hop. The refusal reaches the model as a
``ToolMessage``, and LangChain's ``status`` field is ``Literal["success",
"error"]`` — there is no third value for "declined". So the refusal arrives at
the stream classifier wearing a fault's clothes, is published as ``failed``, and
the client renders a working budget as a broken run under a retry the cap
guarantees will lose.

Rather than recover the distinction by pattern-matching the message prose at a
distance, the refusal carries a typed marker on ``additional_kwargs`` — a
first-class ``BaseMessage`` field, so it survives serialization and the
checkpointer. ``policy_answers`` matches text only because the Deep Agents
``BackendProtocol`` gives it a single ``error`` channel and no room for a
marker; here the ``ToolMessage`` is authored by our own code, so the structured
channel is available and is strictly the better one.

Adding a refusal means adding it here — never a bare marker at a call site.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agent_runtime.capabilities.task_policy import ToolPolicyRejected
from agent_runtime.execution.tool_errors import ToolBudgetRejected
from agent_runtime.execution.tool_outcomes import ToolErrorCode


@dataclass(frozen=True)
class ToolRefusal:
    """A declared refusal: the typed code and the sentence the user reads."""

    code: str
    safe_message: str


class ToolRefusals:
    """Declared non-fatal refusals and the typed code each one carries."""

    #: Top-level ``additional_kwargs`` key. Namespaced because the dict is
    #: shared with provider-specific extras.
    MARKER_KEY = "_runtime_tool_refusal"

    _CODE_FIELD = "code"
    _SAFE_MESSAGE_FIELD = "safe_message"

    #: The per-tool cap refused the call at the gate. ``TOOL_BUDGET_EXCEEDED``
    #: is reused rather than reinvented: ``ToolBudgetReject.error_code``
    #: already returns it for exactly this decision, and a second name for one
    #: thing is how the two drift apart.
    _BUDGET_CODE = ToolErrorCode.TOOL_BUDGET_EXCEEDED.value

    #: The task policy refused a duplicate or blocked dispatch. Distinct from
    #: the budget code because the remedy differs — the model must revise its
    #: plan, not merely stop spending.
    _POLICY_CODE = "tool_policy_rejected"

    #: Ordered most-specific first: ``ToolPolicyRejected`` subclasses
    #: ``ToolBudgetRejected``, so a plain ``isinstance`` chain in the other
    #: order would label every policy refusal a budget one.
    _BY_TYPE: tuple[tuple[type[ToolBudgetRejected], str], ...] = (
        (ToolPolicyRejected, _POLICY_CODE),
        (ToolBudgetRejected, _BUDGET_CODE),
    )

    #: Every code this module can stamp, for the presentation layer's
    #: retryability table and for tests that assert the set is covered.
    CODES: frozenset[str] = frozenset({_BUDGET_CODE, _POLICY_CODE})

    @classmethod
    def code_for_exception(cls, exc: BaseException) -> str | None:
        """Return the typed code for a declared refusal, else ``None``.

        ``None`` for every other exception — including the fatal
        :class:`~agent_runtime.execution.tool_errors.BudgetExceeded` the guard
        escalates to, which really does end the run and really is a failure.
        """

        for refusal_type, code in cls._BY_TYPE:
            if isinstance(exc, refusal_type):
                return code
        return None

    @classmethod
    def marker_for(cls, exc: BaseException) -> dict[str, object] | None:
        """Return the ``additional_kwargs`` marker for ``exc``, else ``None``.

        The safe message rides along deliberately. The model-facing content is
        ``"<error_class>: <message>\\nHints: {...}"`` — correct for the model,
        wrong for a card — so the clean sentence is carried separately rather
        than reconstructed by stripping the envelope back off downstream.
        """

        code = cls.code_for_exception(exc)
        if code is None:
            return None
        safe_message = getattr(exc, "safe_summary", "")
        # A plain dict, not a MappingProxyType: this rides on a LangChain
        # message through the checkpointer's serializer, and only ordinary
        # JSON-native containers survive that round trip.
        return {
            cls.MARKER_KEY: {
                cls._CODE_FIELD: code,
                cls._SAFE_MESSAGE_FIELD: str(safe_message).strip(),
            }
        }

    @classmethod
    def read(cls, message: object) -> ToolRefusal | None:
        """Return the refusal declared on ``message``, or ``None``.

        Accepts both shapes the stream classifier sees: the live
        ``ToolMessage`` object, and the flat mapping a replayed or
        hand-constructed payload arrives as.
        """

        kwargs = cls._additional_kwargs(message)
        if kwargs is None:
            return None
        marker = kwargs.get(cls.MARKER_KEY)
        if not isinstance(marker, Mapping):
            return None
        code = marker.get(cls._CODE_FIELD)
        # An unrecognised code is not honoured: this seam moves a result OUT of
        # the failure taxonomy, so anything that could be spoofed by tool
        # output must fail closed and keep its failure classification.
        if not isinstance(code, str) or code not in cls.CODES:
            return None
        safe_message = marker.get(cls._SAFE_MESSAGE_FIELD)
        return ToolRefusal(
            code=code,
            safe_message=safe_message.strip() if isinstance(safe_message, str) else "",
        )

    @staticmethod
    def _additional_kwargs(message: object) -> Mapping[str, object] | None:
        if isinstance(message, Mapping):
            candidate = message.get("additional_kwargs")
        else:
            candidate = getattr(message, "additional_kwargs", None)
        return candidate if isinstance(candidate, Mapping) else None


__all__ = ("ToolRefusal", "ToolRefusals")
