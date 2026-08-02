"""ERROR_MAP stage — normalize the MCP failure taxonomy (migration P2-5).

**Composed by the P2-8 registration flip**, behind ``MCP_PER_TOOL_ENABLED``
(default OFF). Until that flag is on for a deployment, the legacy
``call_mcp_tool`` gateway still owns the dispatch and nothing here runs.

One connector failure has to answer three questions at once, and collapsing them
is what once told a user "the server isn't responding right now — try again in a
moment" about an HTTP 400, a sentence in which every clause was false and whose
advice could not succeed:

* **what happened** — a typed code (:class:`McpLoadErrorCode` for the MCP
  subsystem's own public surface, :class:`RuntimeErrorCode` for the runtime's
  API/stream surface), never a class name and never a traceback;
* **what to say** — a ``safe_message`` authored here, which the model paraphrases
  into user-facing copy, so each one states its own transience and the one
  action that resolves it;
* **whether a retry can help** — ``retryable``, and separately ``replay_safe``,
  which is *not* the same question (see below).

``retryable`` vs ``replay_safe``. A timeout on a read is both. An ambiguous
dispatch is neither: the request may already have reached the server, so a
"retry" would be a second side effect, not a second attempt. The two flags are
kept apart because one of them is advice and the other is an invariant — the
EXEC_POLICY stage refuses to replay regardless of what ``retryable`` says.

**This stage raises; it does not swallow.** ``MIDDLEWARE_ORDER`` puts ERROR_MAP
*inside* EXEC_POLICY, so returning a normalized failure as a value here would
mean the retry stage never sees a failure and could never retry anything. The
normalized :class:`McpToolCallError` therefore propagates outward carrying its
:class:`McpToolErrorEnvelope`; EXEC_POLICY reads the envelope to decide whether
to try again. ``str(exc)`` is exactly the safe message, so an outer sanitizer has
nothing to strip.

**Something has to catch it, though.** A typed exception that escapes the
composed stack is a tool-node exception: the run fails instead of the model
being told "that connector call failed, here is why, and here is whether
retrying helps". :class:`McpErrorResultTool` closes that loop at the
registration site — it wraps the *composed* stack (outside POLICY, so it sees
every stage's failures including the one EXEC_POLICY re-raises after its last
attempt) and turns the envelope into an ordinary tool result the model can route
around. It catches :class:`McpToolCallError` only: an un-normalized exception is
not a connector failure and must keep its existing path.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import ConfigDict, Field

from agent_runtime.capabilities.delegating_tool import NO_CONFIG, DelegatingTool
from agent_runtime.capabilities.mcp.cards import McpLoadErrorCode
from agent_runtime.capabilities.mcp.client import (
    McpAmbiguousDispatchError,
    McpAuthError,
    McpClientError,
    McpConnectionError,
    McpLeaseError,
    McpNotFoundError,
    McpRequestRejectedError,
    McpTimeoutError,
    McpUnsupportedMethodError,
)
from agent_runtime.capabilities.mcp.constants import Limits, Messages
from agent_runtime.capabilities.mcp.middleware.compose import (
    ToolResultShape,
    ToolSchemaIdentity,
)
from agent_runtime.capabilities.policy.contracts import (
    CapabilityDescriptor,
    CapabilityUrn,
    MiddlewareStage,
)
from agent_runtime.execution.contracts import RuntimeContract, RuntimeErrorCode

_LOGGER = logging.getLogger(__name__)

#: Never classified, always propagated — a cancelled run is not a connector
#: failure, and a policy must never be able to swallow cancellation.
_NEVER_MAP: tuple[type[BaseException], ...] = (
    asyncio.CancelledError,
    KeyboardInterrupt,
    SystemExit,
)


class McpErrorCopy:
    """Safe public copy this stage authors, for cases the loader has none.

    Everything else reuses ``Messages.Loader`` verbatim, so one connector
    failure reads the same whether it happened while loading a server or while
    calling one of its tools.
    """

    AMBIGUOUS_DISPATCH = (
        "The connector did not confirm this call, and it may already have taken "
        "effect. It was not retried. Do not repeat it — check the connector to "
        "see whether the change landed before doing anything else."
    )
    UNSUPPORTED_METHOD = (
        "The connector does not support this operation. It replied, so this is "
        "not an outage and not temporary: a retry will not change it."
    )


class McpToolErrorEnvelope(RuntimeContract):
    """The normalized, safe description of one failed MCP tool call.

    Frozen and ``extra="forbid"`` (via :class:`RuntimeContract`). Every field is
    safe for a model prompt and for an API surface: two typed codes, an authored
    message, two independent booleans, and the connector/tool identity taken
    from the descriptor URN — never from an exception's text.
    """

    #: The MCP subsystem's public failure code (same vocabulary as
    #: ``McpLoadError.code``, so one connector speaks one language).
    code: McpLoadErrorCode
    #: The runtime's API/stream failure class for the same event.
    runtime_code: RuntimeErrorCode
    #: Authored copy the model paraphrases. Never derived from ``str(exc)``.
    safe_message: str = Field(min_length=1, max_length=Limits.SAFE_MESSAGE_MAX_LENGTH)
    #: Whether trying again could plausibly succeed (advice).
    retryable: bool = False
    #: Whether the call is known NOT to have reached the server (invariant).
    #: ``False`` means a repeat may duplicate a side effect.
    replay_safe: bool = True
    #: Connector slug from the descriptor URN; ``None`` outside a described call.
    connector: str | None = None
    #: Tool name from the descriptor URN.
    tool: str | None = None


class McpToolCallError(McpClientError):
    """A normalized MCP tool-call failure, carrying its safe envelope.

    Subclasses :class:`McpClientError` so an existing ``except McpClientError``
    anywhere outward still catches it — a normalization must never turn a
    handled failure into an unhandled one. ``str(exc)`` is the envelope's
    ``safe_message`` and nothing else; the original exception survives only as
    ``__cause__``, for logs.
    """

    def __init__(self, envelope: McpToolErrorEnvelope) -> None:
        """Wrap the safe envelope this failure reports."""

        super().__init__(envelope.safe_message)
        self.envelope = envelope

    @property
    def retryable(self) -> bool:
        """Whether trying again could plausibly succeed."""

        return self.envelope.retryable

    @property
    def replay_safe(self) -> bool:
        """Whether the call is known not to have reached the server."""

        return self.envelope.replay_safe

    def as_tool_result(self) -> dict[str, Any]:
        """Return the JSON-safe payload a caller may hand to the model."""

        return self.envelope.model_dump(mode="json", exclude_none=True)


@dataclass(frozen=True)
class McpErrorRule:
    """One row of the taxonomy: which failures map to which safe description."""

    kinds: tuple[type[BaseException], ...]
    code: McpLoadErrorCode
    runtime_code: RuntimeErrorCode
    safe_message: str
    retryable: bool = False
    replay_safe: bool = True


class McpErrorTaxonomy:
    """Classify an MCP failure into its safe, typed envelope.

    Ordered, first-match-wins, and the order is load-bearing because the
    taxonomy is a hierarchy: ``McpAmbiguousDispatchError`` ⊂
    ``McpConnectionError`` and ``McpNotFoundError`` ⊂
    ``McpRequestRejectedError``. The narrow rows come first, mirroring
    ``McpToolLoadFailure`` in ``tool_source.py`` and the ``except`` chain in
    ``operation_adapter.py`` — one taxonomy, three call sites, no divergence.

    A 4xx is matched **before** the connection family and is never retryable:
    the peer answered and refused, so nothing about it is transient.
    """

    _RULES: tuple[McpErrorRule, ...] = (
        # Never-replay first: it is a connection error by inheritance, but the
        # only rule that matters about it is that it must not be repeated.
        McpErrorRule(
            kinds=(McpAmbiguousDispatchError,),
            code=McpLoadErrorCode.CONNECTION_FAILED,
            runtime_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            safe_message=McpErrorCopy.AMBIGUOUS_DISPATCH,
            retryable=False,
            replay_safe=False,
        ),
        # A lease rejection is a connection error by inheritance, but it carries
        # its own ``redispatch_safe`` flag and must be judged on it -- the legacy
        # adapter gated exactly this. Placed before the generic connection rule
        # (narrow before broad) so the flag is never discarded.
        McpErrorRule(
            kinds=(McpLeaseError,),
            code=McpLoadErrorCode.CONNECTION_FAILED,
            runtime_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            safe_message=Messages.Loader.CONNECTION_FAILED,
            # Both are refined per-instance from the exception itself; the
            # defaults here are the fail-closed answer for a lease that does not
            # declare itself safe.
            retryable=False,
            replay_safe=False,
        ),
        McpErrorRule(
            kinds=(McpTimeoutError, TimeoutError),
            code=McpLoadErrorCode.TIMEOUT,
            runtime_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            safe_message=Messages.Loader.TIMEOUT,
            retryable=True,
            # A timeout is the canonical case where the request MAY already have
            # reached the server, so it is retryable for a read but never
            # replay-safe. The two answer different questions.
            replay_safe=False,
        ),
        McpErrorRule(
            kinds=(McpAuthError, PermissionError),
            code=McpLoadErrorCode.AUTH_FAILURE,
            runtime_code=RuntimeErrorCode.PERMISSION_DENIED,
            safe_message=Messages.Loader.AUTH_FAILED,
            retryable=False,
        ),
        McpErrorRule(
            kinds=(McpNotFoundError,),
            code=McpLoadErrorCode.UNKNOWN_SERVER,
            runtime_code=RuntimeErrorCode.CAPABILITY_NOT_FOUND,
            safe_message=Messages.Loader.SERVER_NOT_FOUND,
            retryable=False,
        ),
        McpErrorRule(
            kinds=(McpRequestRejectedError,),
            code=McpLoadErrorCode.MCP_PROTOCOL_ERROR,
            runtime_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            safe_message=Messages.Loader.REQUEST_REJECTED,
            retryable=False,
        ),
        McpErrorRule(
            kinds=(McpUnsupportedMethodError,),
            code=McpLoadErrorCode.MCP_PROTOCOL_ERROR,
            runtime_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            safe_message=McpErrorCopy.UNSUPPORTED_METHOD,
            retryable=False,
        ),
        McpErrorRule(
            kinds=(McpConnectionError, ConnectionError),
            code=McpLoadErrorCode.CONNECTION_FAILED,
            runtime_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            safe_message=Messages.Loader.CONNECTION_FAILED,
            retryable=True,
            # A connection can drop mid-flight, after the request left. Safe to
            # retry a READ (the action veto stops writes), never safe to claim
            # the call did not land.
            replay_safe=False,
        ),
        # Any other MCP client failure: recognised as ours, but not understood.
        # Fail closed — do not advertise a retry we cannot justify.
        McpErrorRule(
            kinds=(McpClientError,),
            code=McpLoadErrorCode.MCP_PROTOCOL_ERROR,
            runtime_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            safe_message=Messages.Loader.PROTOCOL_ERROR,
            retryable=False,
        ),
    )

    @classmethod
    def classify(
        cls,
        exc: BaseException,
        *,
        connector: str | None = None,
        tool: str | None = None,
    ) -> McpToolErrorEnvelope | None:
        """Return the safe envelope for ``exc``, or ``None`` if it is not ours.

        ``None`` means "not an MCP transport/protocol failure" — a schema
        validation error, a defect in the tool itself. Those are re-raised
        unchanged rather than dressed up as connector failures; the runtime's
        generic tool-error policy already sanitizes them.

        Idempotent: an already-normalized :class:`McpToolCallError` returns its
        own envelope, so classifying twice (this stage, then the retry stage)
        can never re-describe a failure.
        """

        if isinstance(exc, McpToolCallError):
            return exc.envelope
        for rule in cls._RULES:
            if isinstance(exc, rule.kinds):
                retryable, replay_safe = cls._refine(exc, rule)
                return McpToolErrorEnvelope(
                    code=rule.code,
                    runtime_code=rule.runtime_code,
                    safe_message=rule.safe_message,
                    retryable=retryable,
                    replay_safe=replay_safe,
                    connector=connector,
                    tool=tool,
                )
        return None

    @staticmethod
    def _refine(exc: BaseException, rule: McpErrorRule) -> tuple[bool, bool]:
        """Refine ``(retryable, replay_safe)`` from the exception's own flags.

        The table answers by exception *kind*; a lease rejection additionally
        answers for *itself*. ``McpLeaseError`` carries ``redispatch_safe`` (may
        this exact call be sent again?) and ``acquisition_safe`` (may a fresh
        lease be acquired?), and the legacy adapter refused to retry unless the
        lease said so. Discarding those flags is how a not-redispatch-safe lease
        error ends up retried, so they are read here rather than flattened into
        the static row.
        """

        if isinstance(exc, McpLeaseError):
            replay_safe = bool(getattr(exc, "redispatch_safe", False))
            retryable = replay_safe and bool(getattr(exc, "acquisition_safe", False))
            return retryable, replay_safe
        return rule.retryable, rule.replay_safe


class McpErrorMappingTool(DelegatingTool):
    """Schema-identical wrapper that normalizes MCP failures on the way out.

    Holds only the connector/tool identity resolved from the descriptor URN —
    no card, no endpoint, no credential — so nothing it can carry is capable of
    leaking.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseTool
    connector: str
    capability: str

    def _run(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Sync path: delegate, normalizing any MCP failure that escapes."""

        try:
            return self.delegate(*args, config=config, **kwargs)
        except _NEVER_MAP:
            raise
        except BaseException as exc:  # noqa: BLE001 — width is intentional
            normalized = self._normalize(exc)
            if normalized is exc:
                raise
            raise normalized from exc

    async def _arun(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Async path: delegate, normalizing any MCP failure that escapes."""

        try:
            return await self.adelegate(*args, config=config, **kwargs)
        except _NEVER_MAP:
            raise
        except BaseException as exc:  # noqa: BLE001 — width is intentional
            normalized = self._normalize(exc)
            if normalized is exc:
                raise
            raise normalized from exc

    def _normalize(self, exc: BaseException) -> BaseException:
        """Return the exception to raise: normalized when ours, else unchanged."""

        envelope = McpErrorTaxonomy.classify(
            exc, connector=self.connector, tool=self.capability
        )
        if envelope is None:
            return exc
        # The class name and the connector are safe; the exception's own text is
        # not, and is deliberately never logged or attached here.
        _LOGGER.info(
            "mcp_tool_error_mapped",
            extra={
                "metadata": {
                    "connector": self.connector,
                    "tool_name": self.name,
                    "error_class": type(exc).__name__,
                    "code": envelope.code.value,
                    "retryable": envelope.retryable,
                    "replay_safe": envelope.replay_safe,
                }
            },
        )
        if isinstance(exc, McpToolCallError):
            return exc
        return McpToolCallError(envelope)


class McpErrorResultTool(DelegatingTool):
    """Outermost wrapper: hand a normalized connector failure back as a result.

    Not a :data:`MIDDLEWARE_ORDER` stage — deliberately. It sits *outside* the
    composed five, because the failure it renders may be raised by any of them
    (EXEC_POLICY re-raises after its last attempt, POLICY delegates through it),
    and because it must never be able to swallow a failure before EXEC_POLICY
    has decided whether to retry it.

    Only :class:`McpToolCallError` is converted. Anything else — a schema
    validation error, a defect in the tool, a cancelled run — keeps the path it
    already had; dressing an unknown exception up as a connector result would
    tell the model a lie about what happened.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseTool

    @classmethod
    def wrapping(cls, tool: BaseTool) -> "McpErrorResultTool":
        """Return a schema-identical wrapper that renders MCP failures as results."""

        return cls(**ToolSchemaIdentity.fields_of(tool), inner=tool)

    def _run(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Sync path: delegate, rendering a normalized MCP failure as a result."""

        try:
            return self.delegate(*args, config=config, **kwargs)
        except McpToolCallError as exc:
            return self._as_result(exc)

    async def _arun(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Async path: delegate, rendering a normalized MCP failure as a result."""

        try:
            return await self.adelegate(*args, config=config, **kwargs)
        except McpToolCallError as exc:
            return self._as_result(exc)

    def _as_result(self, exc: McpToolCallError) -> Any:
        """Render the safe envelope in the return shape this wrapper declares."""

        return ToolResultShape.render(
            exc.as_tool_result(), response_format=self.response_format
        )


@dataclass(frozen=True)
class McpErrorMapMiddleware:
    """The ERROR_MAP stage of the P0 pipeline."""

    # ``init=False`` -- see the note on the exec-policy stage: a mislabelled
    # middleware would defeat the composer's whole order guarantee.
    stage: MiddlewareStage = field(default=MiddlewareStage.ERROR_MAP, init=False)

    def wrap(self, tool: BaseTool, descriptor: CapabilityDescriptor) -> BaseTool:
        """Return ``tool`` wrapped so its MCP failures arrive normalized."""

        parsed = CapabilityUrn.parse(descriptor.urn)
        return McpErrorMappingTool(
            **ToolSchemaIdentity.fields_of(tool),
            inner=tool,
            connector=parsed.namespace,
            capability=parsed.name,
        )


__all__ = [
    "McpErrorCopy",
    "McpErrorMapMiddleware",
    "McpErrorMappingTool",
    "McpErrorResultTool",
    "McpErrorRule",
    "McpErrorTaxonomy",
    "McpToolCallError",
    "McpToolErrorEnvelope",
]
