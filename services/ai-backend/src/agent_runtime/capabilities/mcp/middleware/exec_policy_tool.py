"""EXEC_POLICY stage — retry and replay policy, keyed on action (P2-5).

**Composed by the P2-8 registration flip**, behind ``MCP_PER_TOOL_ENABLED``
(default OFF). Until that flag is on for a deployment, the legacy
``call_mcp_tool`` gateway still owns the dispatch and nothing here runs.

One rule, keyed on :class:`~agent_runtime.capabilities.policy.contracts.Action`,
covers the whole never-replay edge:

* **READ** — a transient transport failure (``McpConnectionError`` /
  ``McpTimeoutError``) may be retried with the existing backoff. A read has no
  side effect, so a second attempt costs latency and nothing else.
* **WRITE / DESTRUCTIVE** — ``retry_exceptions=()``: never retried, at all, for
  any failure. A connector write that failed *after* reaching the server is
  indistinguishable at this layer from one that never arrived, and the cost of
  being wrong is a duplicate issue, message, or deletion. Stream resumption is
  disabled for the same reason (:attr:`McpExecPolicy.stream_resumption`).
* **Any action** — :class:`McpAmbiguousDispatchError` is never retried. It is a
  subclass of ``McpConnectionError``, so a plain ``retry_exceptions`` tuple
  would happily replay the one failure whose whole meaning is "this may already
  have dispatched". The exclusion is explicit and independent of action, which
  is why it survives even if a future policy widens the READ retry set.

Retry mechanics (attempt loop, exponential backoff with full jitter, structured
``tool_retry`` log, cancellation always propagated) are **not** re-implemented:
this is a :class:`~agent_runtime.capabilities.retrying_tool.RetryingTool` with a
narrowed decision. Only ``_should_retry`` is overridden.

**Reading a normalized failure.** ``MIDDLEWARE_ORDER`` puts ERROR_MAP *inside*
this stage, so once composed, what arrives here is usually a
:class:`McpToolCallError` rather than the raw ``McpConnectionError``. Keying
retry on raw types alone would therefore silently disable every retry the moment
the two stages are composed. The predicate reads the normalized envelope too —
same taxonomy, one extra branch — so the stage behaves identically wrapped
directly around a tool or composed above ERROR_MAP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import ConfigDict, PositiveInt

from agent_runtime.capabilities.mcp.client import (
    McpAmbiguousDispatchError,
    McpConnectionError,
    McpLeaseError,
    McpTimeoutError,
)
from agent_runtime.capabilities.mcp.middleware.compose import ToolSchemaIdentity
from agent_runtime.capabilities.mcp.middleware.error_map_tool import McpToolCallError
from agent_runtime.capabilities.policy.contracts import (
    Action,
    CapabilityDescriptor,
    MiddlewareStage,
)
from agent_runtime.capabilities.retrying_tool import RetryingTool
from agent_runtime.execution.contracts import RuntimeContract

#: Transport failures a READ may retry. Deliberately narrow — an auth failure or
#: a 4xx is answered identically every time, so retrying it only burns latency.
_TRANSIENT: tuple[type[BaseException], ...] = (McpConnectionError, McpTimeoutError)

#: Never retried, whatever the action and whatever ``retry_exceptions`` says.
_NEVER_REPLAY: tuple[type[BaseException], ...] = (McpAmbiguousDispatchError,)


class McpExecPolicy(RuntimeContract):
    """The execution policy one action class earns.

    Frozen and ``extra="forbid"``. ``retry_transient`` and ``stream_resumption``
    are separate booleans rather than one "is a write" flag because they are
    separate guarantees: the first is about repeating a request, the second
    about resuming a response stream, and a future transport could need one
    without the other.
    """

    action: Action
    #: Hard ceiling on attempts, including the first.
    max_attempts: PositiveInt = 1
    #: Whether a transient transport failure may be retried at all.
    retry_transient: bool = False
    #: Whether the transport may resume an interrupted response stream for this
    #: call. ``False`` for every write: a resumed stream can re-deliver a
    #: request the server already acted on.
    stream_resumption: bool = False


class McpExecPolicyTable:
    """``Action`` → :class:`McpExecPolicy`. The whole rule, in one place.

    An action outside the table resolves to the WRITE policy — the fail-closed
    answer. A new, unrecognised effect class must not inherit a read's freedom
    to retry.
    """

    _READ = McpExecPolicy(
        action=Action.READ,
        max_attempts=3,
        retry_transient=True,
        stream_resumption=True,
    )
    _WRITE = McpExecPolicy(
        action=Action.WRITE,
        max_attempts=1,
        retry_transient=False,
        stream_resumption=False,
    )
    _DESTRUCTIVE = McpExecPolicy(
        action=Action.DESTRUCTIVE,
        max_attempts=1,
        retry_transient=False,
        stream_resumption=False,
    )

    _BY_ACTION: dict[Action, McpExecPolicy] = {
        Action.READ: _READ,
        Action.WRITE: _WRITE,
        Action.DESTRUCTIVE: _DESTRUCTIVE,
    }

    @classmethod
    def for_action(cls, action: Action) -> McpExecPolicy:
        """Return the policy for ``action``, defaulting fail-closed to WRITE."""

        return cls._BY_ACTION.get(action, cls._WRITE)


class McpExecPolicyTool(RetryingTool):
    """Schema-identical wrapper whose retry decision is the action's policy.

    ``retry_exceptions`` is set by the middleware from the policy — ``()`` for a
    write, so even the inherited predicate can never say yes — and
    :meth:`_should_retry` adds the two rules a type tuple cannot express: the
    action-level veto and the never-replay veto.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseTool
    exec_policy: McpExecPolicy
    #: Excluded from retry regardless of ``retry_exceptions`` or action.
    never_retry_exceptions: tuple[type[BaseException], ...] = _NEVER_REPLAY

    @property
    def stream_resumption_enabled(self) -> bool:
        """Whether the transport may resume this call's response stream.

        Read by the session/connection layer at P2-8 wiring. Nothing here
        *enables* resumption — the MCP client only resumes when a caller passes
        a ``ClientMessageMetadata`` resumption token — so this flag can only
        ever tighten, and the never-replay rule below does not depend on it.
        """

        return self.exec_policy.stream_resumption

    def _should_retry(self, exc: BaseException) -> bool:
        """Return ``True`` only when this action may safely repeat ``exc``."""

        if not self.exec_policy.retry_transient:
            # WRITE / DESTRUCTIVE: no failure is retryable, ever.
            return False
        if isinstance(exc, self.never_retry_exceptions):
            # May already have dispatched — a "retry" would be a second effect.
            return False
        if isinstance(exc, McpLeaseError) and not self._lease_is_repeatable(exc):
            # A lease rejection is an McpConnectionError by inheritance, so the
            # base type check below would retry it on a READ and discard the
            # flags the lease itself carries. It answers for itself: only a
            # redispatch-safe lease that can also be re-acquired may repeat.
            # (In the composed stack ERROR_MAP has usually normalized this
            # already; this keeps the guard true of the raw exception too.)
            return False
        if super()._should_retry(exc):
            return True
        return self._is_retryable_normalized(exc)

    @staticmethod
    def _lease_is_repeatable(exc: McpLeaseError) -> bool:
        """Whether a lease rejection may be sent again (both flags required)."""

        return bool(getattr(exc, "redispatch_safe", False)) and bool(
            getattr(exc, "acquisition_safe", False)
        )

    @staticmethod
    def _is_retryable_normalized(exc: BaseException) -> bool:
        """Whether an already-normalized failure is safe to repeat.

        Keyed on ``retryable`` alone, deliberately. ``replay_safe`` answers a
        different question — "is it certain the request never reached the
        server?" — and is honestly ``False`` for a timeout or a dropped
        connection, which are exactly the failures a READ must still be allowed
        to retry. Requiring both would make the honest answer mean "never retry
        anything transient".

        Repeating a call that may have landed is only dangerous when it has an
        external effect, and that case is already excluded twice over before
        this runs: WRITE/DESTRUCTIVE fail the ``retry_transient`` veto above, and
        an ambiguous dispatch is both a never-retry kind and ``retryable=False``.
        ``replay_safe`` therefore travels on the envelope as the never-replay
        signal for downstream consumers, rather than as this predicate's input.
        """

        if not isinstance(exc, McpToolCallError):
            return False
        return exc.retryable


@dataclass(frozen=True)
class McpExecPolicyMiddleware:
    """The EXEC_POLICY stage of the P0 pipeline.

    Backoff is injected rather than hard-coded so a test can compose the real
    stage without sleeping; production keeps ``RetryingTool``'s own defaults.
    """

    # ``init=False``: the composer's order assertion reads ``stage`` and trusts
    # it, so a constructor-settable stage would let a caller mislabel a
    # middleware and compose a stack with POLICY anywhere but outermost -- the
    # exact failure the assertion exists to prevent.
    stage: MiddlewareStage = field(default=MiddlewareStage.EXEC_POLICY, init=False)
    initial_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 4.0

    def wrap(self, tool: BaseTool, descriptor: CapabilityDescriptor) -> BaseTool:
        """Return ``tool`` wrapped in the retry policy its action earns."""

        policy = McpExecPolicyTable.for_action(descriptor.action)
        retry_exceptions: tuple[type[BaseException], ...] = (
            _TRANSIENT if policy.retry_transient else ()
        )
        fields: dict[str, Any] = ToolSchemaIdentity.fields_of(tool)
        return McpExecPolicyTool(
            **fields,
            inner=tool,
            exec_policy=policy,
            max_attempts=policy.max_attempts,
            retry_exceptions=retry_exceptions,
            initial_backoff_seconds=self.initial_backoff_seconds,
            max_backoff_seconds=self.max_backoff_seconds,
        )


__all__ = [
    "McpExecPolicy",
    "McpExecPolicyMiddleware",
    "McpExecPolicyTable",
    "McpExecPolicyTool",
]
