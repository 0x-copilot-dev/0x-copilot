"""Unit tests for the P2-5 exec-policy / error-map / citations / observe stages.

All four stages plus the composer are additive and unwired, so these tests are
the whole proof that the pipeline is safe before P2-8 composes it. They pin five
properties, three of which are security- or provenance-bearing:

* **Schema identity.** Every stage returns a tool the model cannot tell apart
  from the one it wraps — same name, description, and args schema — and
  propagates ``response_format``, without which an MCP tool's
  ``(content, artifact)`` return would reach the model as the repr of a tuple.
* **Never replay.** A WRITE or DESTRUCTIVE call is never retried, and an
  ambiguous dispatch is never retried for **any** action — including through the
  composed stack, where the failure arrives already normalized.
* **The error table, case by case.** Each ``McpClientError`` maps to one typed
  code, one authored safe message, and the right ``retryable`` /``replay_safe``
  pair; internal detail never reaches the message.
* **Citation identity.** The projector is keyed on the connector slug from the
  descriptor URN, never on the tool's own name.
* **Composition order.** The stack is exactly ``MIDDLEWARE_ORDER``, wrapped
  innermost-first; anything else is refused with a typed error before a single
  wrapper is built.

Everything runs against fakes — no network, no credential, no live MCP server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict

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
from agent_runtime.capabilities.mcp.constants import Messages
from agent_runtime.capabilities.mcp.middleware import citations_tool
from agent_runtime.capabilities.mcp.middleware.citations_tool import (
    McpCitationsMiddleware,
)
from agent_runtime.capabilities.mcp.middleware.compose import (
    MiddlewareCompositionError,
    ToolMiddlewareComposer,
)
from agent_runtime.capabilities.mcp.middleware.error_map_tool import (
    McpErrorCopy,
    McpErrorMapMiddleware,
    McpErrorTaxonomy,
    McpToolCallError,
    McpToolErrorEnvelope,
)
from agent_runtime.capabilities.mcp.middleware.exec_policy_tool import (
    McpExecPolicyMiddleware,
    McpExecPolicyTable,
)
from agent_runtime.capabilities.mcp.middleware.observe_tool import (
    McpCallBindingScope,
    McpObserveMiddleware,
)
from agent_runtime.capabilities.policy.contracts import (
    MIDDLEWARE_ORDER,
    Action,
    CapabilityDescriptor,
    CapabilityUrn,
    ConnectorState,
    MiddlewareStage,
    ToolMiddleware,
    Trust,
)
from agent_runtime.execution.contracts import RuntimeErrorCode


class StubToolInput(BaseModel):
    """The args schema a wrapper must propagate byte-identically."""

    query: str


class StubMcpTool(BaseTool):
    """A discovered MCP tool: counts calls, raises a scripted failure sequence.

    ``response_format="content_and_artifact"`` and the flattened annotation
    ``metadata`` mirror what ``langchain-mcp-adapters`` actually builds, so the
    propagation assertions are about the real shape, not a convenient one.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "linear_create_issue"
    description: str = "Create an issue in Linear."
    args_schema: Any = StubToolInput
    response_format: str = "content_and_artifact"
    metadata: dict[str, Any] | None = {"readOnlyHint": False}
    #: Raised on the first N calls, in order; exhausted entries fall through.
    failures: list[Callable[[], BaseException]] = []
    result: Any = "issue created"
    calls: int = 0

    def _run(self, *_: Any, **__: Any) -> Any:
        self.calls += 1
        if self.calls <= len(self.failures):
            raise self.failures[self.calls - 1]()
        return self.result

    async def _arun(self, *_: Any, **__: Any) -> Any:
        return self._run()


class PassThroughTool(DelegatingTool):
    """A stage wrapper that only records which stage produced it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseTool
    stage_marker: MiddlewareStage

    def _run(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        return self.delegate(*args, config=config, **kwargs)

    async def _arun(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        return await self.adelegate(*args, config=config, **kwargs)


@dataclass
class RecordingMiddleware:
    """A schema-identical stage that records the order stages were applied in.

    Stands in for stages owned by other increments (P2-4's POLICY stage) so
    these tests prove P2-5 in isolation.
    """

    stage: MiddlewareStage
    applied: list[MiddlewareStage] = field(default_factory=list)

    def wrap(self, tool: BaseTool, descriptor: CapabilityDescriptor) -> BaseTool:
        """Record the application and return an identical pass-through."""

        del descriptor
        self.applied.append(self.stage)
        return PassThroughTool(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            inner=tool,
            stage_marker=self.stage,
        )


@dataclass
class RenamingMiddleware:
    """A stage that violates schema identity — the composer must refuse it."""

    stage: MiddlewareStage

    def wrap(self, tool: BaseTool, descriptor: CapabilityDescriptor) -> BaseTool:
        """Return a wrapper under a different model-visible name."""

        del descriptor
        return PassThroughTool(
            name=f"{tool.name}_renamed",
            description=tool.description,
            args_schema=tool.args_schema,
            inner=tool,
            stage_marker=self.stage,
        )


class RecordingProjector:
    """Stand-in for :class:`CitationProjectingMcpMiddleware`, records forwards."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raises = raises

    async def project(
        self, *, connector: str, tool_call_id: str | None, result: object
    ) -> None:
        """Record one forwarded projection (or fail, to prove best-effort)."""

        self.calls.append(
            {"connector": connector, "tool_call_id": tool_call_id, "result": result}
        )
        if self._raises is not None:
            raise self._raises


@dataclass(frozen=True)
class ErrorCase:
    """One row of the expected error-map table."""

    label: str
    raise_: Callable[[], BaseException]
    code: McpLoadErrorCode
    runtime_code: RuntimeErrorCode
    safe_message: str
    retryable: bool
    replay_safe: bool


class ErrorMapCases:
    """The expected mapping, case by case — the table the stage is tested against."""

    CASES: tuple[ErrorCase, ...] = (
        ErrorCase(
            label="auth",
            raise_=lambda: McpAuthError("token 9f3a expired for https://x/mcp"),
            code=McpLoadErrorCode.AUTH_FAILURE,
            runtime_code=RuntimeErrorCode.PERMISSION_DENIED,
            safe_message=Messages.Loader.AUTH_FAILED,
            retryable=False,
            replay_safe=True,
        ),
        ErrorCase(
            label="rejected_4xx",
            raise_=lambda: McpRequestRejectedError("bad body", status_code=400),
            code=McpLoadErrorCode.MCP_PROTOCOL_ERROR,
            runtime_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            safe_message=Messages.Loader.REQUEST_REJECTED,
            retryable=False,
            replay_safe=True,
        ),
        ErrorCase(
            label="not_found_404",
            raise_=lambda: McpNotFoundError("no such server", status_code=404),
            code=McpLoadErrorCode.UNKNOWN_SERVER,
            runtime_code=RuntimeErrorCode.CAPABILITY_NOT_FOUND,
            safe_message=Messages.Loader.SERVER_NOT_FOUND,
            retryable=False,
            replay_safe=True,
        ),
        ErrorCase(
            label="connection",
            raise_=lambda: McpConnectionError("connect https://x/mcp refused"),
            code=McpLoadErrorCode.CONNECTION_FAILED,
            runtime_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            safe_message=Messages.Loader.CONNECTION_FAILED,
            # Retryable for a READ, but a connection can drop AFTER the request
            # left, so nothing may claim the call did not land.
            retryable=True,
            replay_safe=False,
        ),
        ErrorCase(
            label="timeout",
            raise_=lambda: McpTimeoutError("deadline exceeded"),
            code=McpLoadErrorCode.TIMEOUT,
            runtime_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            safe_message=Messages.Loader.TIMEOUT,
            # Same split: a timeout is the canonical "may already have reached
            # the server" failure.
            retryable=True,
            replay_safe=False,
        ),
        ErrorCase(
            label="ambiguous",
            raise_=lambda: McpAmbiguousDispatchError("rpc may have dispatched"),
            code=McpLoadErrorCode.CONNECTION_FAILED,
            runtime_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            safe_message=McpErrorCopy.AMBIGUOUS_DISPATCH,
            retryable=False,
            replay_safe=False,
        ),
        ErrorCase(
            label="unsupported_method",
            raise_=lambda: McpUnsupportedMethodError("resources/list"),
            code=McpLoadErrorCode.MCP_PROTOCOL_ERROR,
            runtime_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            safe_message=McpErrorCopy.UNSUPPORTED_METHOD,
            retryable=False,
            replay_safe=True,
        ),
        ErrorCase(
            label="unclassified_client_error",
            raise_=lambda: McpClientError("something else went wrong"),
            code=McpLoadErrorCode.MCP_PROTOCOL_ERROR,
            runtime_code=RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
            safe_message=Messages.Loader.PROTOCOL_ERROR,
            retryable=False,
            replay_safe=True,
        ),
    )


class MiddlewareFixtureMixin:
    """Builders shared by every concrete test class."""

    CONNECTOR = "linear"
    CAPABILITY = "create_issue"
    #: A leaky string: an endpoint, a token, and a host path in one message.
    LEAKY_DETAIL = (
        "https://linear.example/mcp Bearer sk-ab99f1c2d3e4f5a6 "
        "at /Users/dev/enterprise-search/services/ai-backend/x.py"
    )

    def descriptor(
        self,
        *,
        action: Action = Action.WRITE,
        connector: str | None = None,
        capability: str | None = None,
    ) -> CapabilityDescriptor:
        """A per-tool MCP descriptor at the shape P1b's source emits."""

        return CapabilityDescriptor(
            urn=CapabilityUrn.for_mcp(
                connector or self.CONNECTOR, capability or self.CAPABILITY
            ),
            action=action,
            trust=Trust.TRUSTED,
            scopes=(),
            source="mcp",
            connector_state=ConnectorState.LIVE,
        )

    def tool(self, **overrides: Any) -> StubMcpTool:
        """A discovered MCP tool with no scripted failures unless asked."""

        overrides.setdefault("failures", [])
        return StubMcpTool(**overrides)

    def exec_middleware(self) -> McpExecPolicyMiddleware:
        """The real EXEC_POLICY stage with backoff injected to zero."""

        return McpExecPolicyMiddleware(
            initial_backoff_seconds=0.0, max_backoff_seconds=0.0
        )

    def stages(self) -> tuple[ToolMiddleware, ...]:
        """The four P2-5 stages plus a recording stand-in for POLICY."""

        return (
            RecordingMiddleware(stage=MiddlewareStage.POLICY),
            self.exec_middleware(),
            McpObserveMiddleware(),
            McpErrorMapMiddleware(),
            McpCitationsMiddleware(),
        )

    def compose(
        self,
        tool: BaseTool,
        descriptor: CapabilityDescriptor,
        stack: tuple[ToolMiddleware, ...] | None = None,
    ) -> BaseTool:
        """Compose the full ordered stack around ``(tool, descriptor)``."""

        return ToolMiddlewareComposer.compose(
            (tool, descriptor), stack if stack is not None else self.stages()
        )

    def install_projector(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        raises: BaseException | None = None,
    ) -> RecordingProjector:
        """Replace the citation projector the CITATIONS stage forwards to."""

        projector = RecordingProjector(raises=raises)
        monkeypatch.setattr(
            citations_tool, "CitationProjectingMcpMiddleware", projector
        )
        return projector


class TestSchemaIdentity(MiddlewareFixtureMixin):
    def test_every_stage_preserves_the_model_visible_surface(self) -> None:
        inner = self.tool()
        descriptor = self.descriptor()
        for middleware in (
            self.exec_middleware(),
            McpObserveMiddleware(),
            McpErrorMapMiddleware(),
            McpCitationsMiddleware(),
        ):
            wrapped = middleware.wrap(inner, descriptor)
            assert wrapped.name == inner.name
            assert wrapped.description == inner.description
            assert wrapped.args_schema is inner.args_schema

    def test_every_stage_propagates_the_dispatch_surface(self) -> None:
        inner = self.tool()
        descriptor = self.descriptor()
        for middleware in (
            self.exec_middleware(),
            McpObserveMiddleware(),
            McpErrorMapMiddleware(),
            McpCitationsMiddleware(),
        ):
            wrapped = middleware.wrap(inner, descriptor)
            # Without this the MCP tuple return reaches the model as a repr.
            assert wrapped.response_format == "content_and_artifact"
            assert wrapped.metadata == inner.metadata
            assert wrapped.return_direct == inner.return_direct

    def test_propagated_metadata_is_copied_not_shared(self) -> None:
        inner = self.tool()
        wrapped = McpObserveMiddleware().wrap(inner, self.descriptor())
        assert wrapped.metadata is not inner.metadata

    def test_composed_stack_keeps_the_surface_of_the_raw_tool(self) -> None:
        inner = self.tool()
        composed = self.compose(inner, self.descriptor())
        assert composed.name == inner.name
        assert composed.description == inner.description
        assert composed.args_schema is inner.args_schema


class TestStageIsNotConstructorSettable(MiddlewareFixtureMixin):
    """The composer's order assertion reads ``stage`` and trusts it.

    If a caller could mislabel a middleware, a stack could be composed whose
    outermost wrapper is not POLICY -- and a denied call would reach the
    connector, the exact failure the assertion exists to prevent.
    """

    @pytest.mark.parametrize(
        "factory",
        [
            McpExecPolicyMiddleware,
            McpErrorMapMiddleware,
            McpObserveMiddleware,
            McpCitationsMiddleware,
        ],
    )
    def test_stage_cannot_be_supplied(self, factory) -> None:  # noqa: ANN001
        with pytest.raises(TypeError):
            factory(stage=MiddlewareStage.POLICY)


class TestCompose(MiddlewareFixtureMixin):
    def test_wraps_innermost_first_leaving_policy_outermost(self) -> None:
        applied: list[MiddlewareStage] = []
        stack = tuple(
            RecordingMiddleware(stage=stage, applied=applied)
            for stage in MIDDLEWARE_ORDER
        )
        composed = self.compose(self.tool(), self.descriptor(), stack)
        assert tuple(applied) == tuple(reversed(MIDDLEWARE_ORDER))
        assert composed.stage_marker is MiddlewareStage.POLICY

    def test_rejects_a_reordered_stack(self) -> None:
        reordered = (
            RecordingMiddleware(stage=MiddlewareStage.EXEC_POLICY),
            RecordingMiddleware(stage=MiddlewareStage.POLICY),
            RecordingMiddleware(stage=MiddlewareStage.OBSERVE),
            RecordingMiddleware(stage=MiddlewareStage.ERROR_MAP),
            RecordingMiddleware(stage=MiddlewareStage.CITATIONS),
        )
        with pytest.raises(MiddlewareCompositionError) as excinfo:
            self.compose(self.tool(), self.descriptor(), reordered)
        assert "middleware stack must be exactly" in str(excinfo.value)

    def test_rejects_a_partial_stack(self) -> None:
        partial = tuple(
            RecordingMiddleware(stage=stage) for stage in MIDDLEWARE_ORDER[:3]
        )
        with pytest.raises(MiddlewareCompositionError) as excinfo:
            self.compose(self.tool(), self.descriptor(), partial)
        assert "middleware stack must be exactly" in str(excinfo.value)

    def test_rejects_a_duplicated_stage(self) -> None:
        duplicated = (
            RecordingMiddleware(stage=MiddlewareStage.POLICY),
            RecordingMiddleware(stage=MiddlewareStage.POLICY),
            RecordingMiddleware(stage=MiddlewareStage.EXEC_POLICY),
            RecordingMiddleware(stage=MiddlewareStage.OBSERVE),
            RecordingMiddleware(stage=MiddlewareStage.ERROR_MAP),
        )
        with pytest.raises(MiddlewareCompositionError):
            self.compose(self.tool(), self.descriptor(), duplicated)

    def test_rejects_an_empty_stack(self) -> None:
        with pytest.raises(MiddlewareCompositionError) as excinfo:
            self.compose(self.tool(), self.descriptor(), ())
        assert "<empty>" in str(excinfo.value)

    def test_refuses_a_stage_that_breaks_schema_identity(self) -> None:
        stack = (
            RecordingMiddleware(stage=MiddlewareStage.POLICY),
            RecordingMiddleware(stage=MiddlewareStage.EXEC_POLICY),
            RecordingMiddleware(stage=MiddlewareStage.OBSERVE),
            RecordingMiddleware(stage=MiddlewareStage.ERROR_MAP),
            RenamingMiddleware(stage=MiddlewareStage.CITATIONS),
        )
        with pytest.raises(MiddlewareCompositionError) as excinfo:
            self.compose(self.tool(), self.descriptor(), stack)
        assert "schema-identical" in str(excinfo.value)

    def test_no_wrapper_is_built_when_the_order_is_wrong(self) -> None:
        applied: list[MiddlewareStage] = []
        stack = (
            RecordingMiddleware(stage=MiddlewareStage.CITATIONS, applied=applied),
            RecordingMiddleware(stage=MiddlewareStage.POLICY, applied=applied),
        )
        with pytest.raises(MiddlewareCompositionError):
            self.compose(self.tool(), self.descriptor(), stack)
        assert applied == []


class TestExecPolicyRetries(MiddlewareFixtureMixin):
    async def test_read_retries_a_transient_connection_failure(self) -> None:
        inner = self.tool(
            failures=[lambda: McpConnectionError("refused")],
            result="rows",
        )
        wrapped = self.exec_middleware().wrap(
            inner, self.descriptor(action=Action.READ)
        )
        assert await wrapped._arun(query="q") == "rows"
        assert inner.calls == 2

    async def test_read_retries_a_transient_timeout(self) -> None:
        inner = self.tool(failures=[lambda: McpTimeoutError("slow")], result="rows")
        wrapped = self.exec_middleware().wrap(
            inner, self.descriptor(action=Action.READ)
        )
        assert await wrapped._arun(query="q") == "rows"
        assert inner.calls == 2

    async def test_write_never_auto_retries(self) -> None:
        inner = self.tool(failures=[lambda: McpConnectionError("refused")] * 5)
        wrapped = self.exec_middleware().wrap(
            inner, self.descriptor(action=Action.WRITE)
        )
        with pytest.raises(McpConnectionError):
            await wrapped._arun(query="q")
        assert inner.calls == 1

    async def test_destructive_never_auto_retries(self) -> None:
        inner = self.tool(failures=[lambda: McpTimeoutError("slow")] * 5)
        wrapped = self.exec_middleware().wrap(
            inner, self.descriptor(action=Action.DESTRUCTIVE)
        )
        with pytest.raises(McpTimeoutError):
            await wrapped._arun(query="q")
        assert inner.calls == 1

    async def test_a_lease_that_is_not_redispatch_safe_is_never_retried(self) -> None:
        # McpLeaseError subclasses McpConnectionError, so the plain type check
        # would retry it on a READ and discard the flags the lease carries. The
        # legacy adapter refused exactly this; a "retry" of a lease that already
        # dispatched is a second effect.
        inner = self.tool(
            failures=[lambda: McpLeaseError("lease_lost", redispatch_safe=False)] * 5
        )
        wrapped = self.exec_middleware().wrap(
            inner, self.descriptor(action=Action.READ)
        )
        with pytest.raises(McpLeaseError):
            await wrapped._arun(query="q")
        assert inner.calls == 1

    async def test_a_fully_safe_lease_may_be_retried_on_a_read(self) -> None:
        # The control: both flags set means the call never dispatched and a
        # fresh lease can be acquired, so a READ may repeat it.
        inner = self.tool(
            failures=[
                lambda: McpLeaseError(
                    "lease_expired", redispatch_safe=True, acquisition_safe=True
                )
            ],
            result="rows",
        )
        wrapped = self.exec_middleware().wrap(
            inner, self.descriptor(action=Action.READ)
        )
        assert await wrapped._arun(query="q") == "rows"
        assert inner.calls == 2

    def test_write_declares_an_empty_retry_set_and_no_stream_resumption(self) -> None:
        for action in (Action.WRITE, Action.DESTRUCTIVE):
            wrapped = self.exec_middleware().wrap(
                self.tool(), self.descriptor(action=action)
            )
            assert wrapped.retry_exceptions == ()
            assert wrapped.max_attempts == 1
            assert wrapped.stream_resumption_enabled is False

    def test_read_declares_the_transient_retry_set(self) -> None:
        wrapped = self.exec_middleware().wrap(
            self.tool(), self.descriptor(action=Action.READ)
        )
        assert wrapped.retry_exceptions == (McpConnectionError, McpTimeoutError)
        assert wrapped.stream_resumption_enabled is True

    async def test_ambiguous_dispatch_is_never_retried_on_a_read(self) -> None:
        inner = self.tool(failures=[lambda: McpAmbiguousDispatchError("maybe")] * 5)
        wrapped = self.exec_middleware().wrap(
            inner, self.descriptor(action=Action.READ)
        )
        with pytest.raises(McpAmbiguousDispatchError):
            await wrapped._arun(query="q")
        assert inner.calls == 1

    async def test_ambiguous_dispatch_is_never_retried_on_a_write(self) -> None:
        inner = self.tool(failures=[lambda: McpAmbiguousDispatchError("maybe")] * 5)
        wrapped = self.exec_middleware().wrap(
            inner, self.descriptor(action=Action.WRITE)
        )
        with pytest.raises(McpAmbiguousDispatchError):
            await wrapped._arun(query="q")
        assert inner.calls == 1

    async def test_auth_failure_is_not_retried_even_on_a_read(self) -> None:
        inner = self.tool(failures=[lambda: McpAuthError("expired")] * 5)
        wrapped = self.exec_middleware().wrap(
            inner, self.descriptor(action=Action.READ)
        )
        with pytest.raises(McpAuthError):
            await wrapped._arun(query="q")
        assert inner.calls == 1

    async def test_cancellation_is_never_retried(self) -> None:
        inner = self.tool(failures=[lambda: asyncio.CancelledError()] * 5)
        wrapped = self.exec_middleware().wrap(
            inner, self.descriptor(action=Action.READ)
        )
        with pytest.raises(asyncio.CancelledError):
            await wrapped._arun(query="q")
        assert inner.calls == 1

    async def test_a_normalized_retryable_failure_is_still_retried(self) -> None:
        envelope = McpErrorTaxonomy.classify(McpConnectionError("refused"))
        assert envelope is not None
        inner = self.tool(failures=[lambda: McpToolCallError(envelope)], result="rows")
        wrapped = self.exec_middleware().wrap(
            inner, self.descriptor(action=Action.READ)
        )
        assert await wrapped._arun(query="q") == "rows"
        assert inner.calls == 2

    async def test_a_normalized_never_replay_failure_is_not_retried(self) -> None:
        envelope = McpErrorTaxonomy.classify(McpAmbiguousDispatchError("maybe"))
        assert envelope is not None
        inner = self.tool(failures=[lambda: McpToolCallError(envelope)] * 5)
        wrapped = self.exec_middleware().wrap(
            inner, self.descriptor(action=Action.READ)
        )
        with pytest.raises(McpToolCallError):
            await wrapped._arun(query="q")
        assert inner.calls == 1

    def test_an_unknown_action_falls_back_to_the_write_policy(self) -> None:
        policy = McpExecPolicyTable.for_action("unknown_action")  # type: ignore[arg-type]
        assert policy.retry_transient is False
        assert policy.stream_resumption is False


class TestErrorMapTable(MiddlewareFixtureMixin):
    @pytest.mark.parametrize(
        "case",
        [pytest.param(case, id=case.label) for case in ErrorMapCases.CASES],
    )
    async def test_maps_each_failure_to_its_typed_safe_envelope(
        self, case: ErrorCase
    ) -> None:
        inner = self.tool(failures=[case.raise_])
        wrapped = McpErrorMapMiddleware().wrap(inner, self.descriptor())
        with pytest.raises(McpToolCallError) as excinfo:
            await wrapped._arun(query="q")
        envelope = excinfo.value.envelope
        assert envelope.code is case.code
        assert envelope.runtime_code is case.runtime_code
        assert envelope.safe_message == case.safe_message
        assert envelope.retryable is case.retryable
        assert envelope.replay_safe is case.replay_safe
        assert str(excinfo.value) == case.safe_message

    async def test_envelope_carries_the_descriptor_identity(self) -> None:
        inner = self.tool(failures=[lambda: McpConnectionError("refused")])
        wrapped = McpErrorMapMiddleware().wrap(inner, self.descriptor())
        with pytest.raises(McpToolCallError) as excinfo:
            await wrapped._arun(query="q")
        assert excinfo.value.envelope.connector == self.CONNECTOR
        assert excinfo.value.envelope.tool == self.CAPABILITY

    async def test_never_leaks_internal_detail_into_the_safe_message(self) -> None:
        inner = self.tool(
            failures=[lambda: McpConnectionError(self.LEAKY_DETAIL)],
        )
        wrapped = McpErrorMapMiddleware().wrap(inner, self.descriptor())
        with pytest.raises(McpToolCallError) as excinfo:
            await wrapped._arun(query="q")
        assert self.LEAKY_DETAIL not in excinfo.value.envelope.safe_message
        assert self.LEAKY_DETAIL not in str(excinfo.value)
        assert "sk-ab99f1c2d3e4f5a6" not in str(excinfo.value)

    async def test_preserves_the_original_failure_as_the_cause(self) -> None:
        original = McpTimeoutError("deadline exceeded")
        inner = self.tool(failures=[lambda: original])
        wrapped = McpErrorMapMiddleware().wrap(inner, self.descriptor())
        with pytest.raises(McpToolCallError) as excinfo:
            await wrapped._arun(query="q")
        assert excinfo.value.__cause__ is original

    async def test_a_non_mcp_failure_propagates_unchanged(self) -> None:
        inner = self.tool(failures=[lambda: ValueError("schema mismatch")])
        wrapped = McpErrorMapMiddleware().wrap(inner, self.descriptor())
        with pytest.raises(ValueError) as excinfo:
            await wrapped._arun(query="q")
        assert not isinstance(excinfo.value, McpToolCallError)

    async def test_cancellation_propagates_unmapped(self) -> None:
        inner = self.tool(failures=[lambda: asyncio.CancelledError()])
        wrapped = McpErrorMapMiddleware().wrap(inner, self.descriptor())
        with pytest.raises(asyncio.CancelledError):
            await wrapped._arun(query="q")

    async def test_an_already_normalized_failure_is_not_re_described(self) -> None:
        envelope = McpErrorTaxonomy.classify(McpAmbiguousDispatchError("maybe"))
        assert envelope is not None
        original = McpToolCallError(envelope)
        inner = self.tool(failures=[lambda: original])
        wrapped = McpErrorMapMiddleware().wrap(inner, self.descriptor())
        with pytest.raises(McpToolCallError) as excinfo:
            await wrapped._arun(query="q")
        assert excinfo.value is original
        assert excinfo.value.envelope.replay_safe is False

    def test_classify_returns_none_for_a_non_mcp_failure(self) -> None:
        assert McpErrorTaxonomy.classify(ValueError("nope")) is None

    def test_a_timeout_is_retryable_but_never_replay_safe(self) -> None:
        # The two flags answer different questions: a timeout MAY have reached
        # the server, so a read may repeat it but nothing may assume it did not
        # land. Claiming replay_safe here would mislead every consumer that
        # trusts it as the never-replay invariant.
        envelope = McpErrorTaxonomy.classify(McpTimeoutError("slow"))
        assert envelope is not None
        assert envelope.retryable is True
        assert envelope.replay_safe is False

    def test_a_dropped_connection_is_retryable_but_never_replay_safe(self) -> None:
        envelope = McpErrorTaxonomy.classify(McpConnectionError("refused"))
        assert envelope is not None
        assert envelope.retryable is True
        assert envelope.replay_safe is False

    def test_a_lease_error_is_judged_on_its_own_flags(self) -> None:
        # Narrow-before-broad: the lease rule must win over the generic
        # connection rule, and read the flags rather than discard them.
        unsafe = McpErrorTaxonomy.classify(
            McpLeaseError("lease_lost", redispatch_safe=False)
        )
        assert unsafe is not None
        assert unsafe.retryable is False
        assert unsafe.replay_safe is False

        safe = McpErrorTaxonomy.classify(
            McpLeaseError("lease_expired", redispatch_safe=True, acquisition_safe=True)
        )
        assert safe is not None
        assert safe.retryable is True
        assert safe.replay_safe is True

    def test_the_envelope_is_json_safe_for_a_model_surface(self) -> None:
        envelope = McpErrorTaxonomy.classify(
            McpAuthError("expired"), connector="linear", tool="create_issue"
        )
        assert isinstance(envelope, McpToolErrorEnvelope)
        payload = McpToolCallError(envelope).as_tool_result()
        assert payload["code"] == McpLoadErrorCode.AUTH_FAILURE.value
        assert payload["runtime_code"] == RuntimeErrorCode.PERMISSION_DENIED.value
        assert payload["retryable"] is False


class TestCitations(MiddlewareFixtureMixin):
    async def test_forwards_connector_call_id_and_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        projector = self.install_projector(monkeypatch)
        inner = self.tool(result={"content": [{"type": "text", "text": "hi"}]})
        wrapped = McpCitationsMiddleware().wrap(inner, self.descriptor())
        result = await wrapped._arun(query="q", tool_call_id="call_42")
        assert projector.calls == [
            {
                "connector": self.CONNECTOR,
                "tool_call_id": "call_42",
                "result": result,
            }
        ]

    async def test_connector_comes_from_the_urn_not_the_tool_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        projector = self.install_projector(monkeypatch)
        inner = self.tool(name="linear_create_issue")
        wrapped = McpCitationsMiddleware().wrap(
            inner, self.descriptor(connector="notion")
        )
        await wrapped._arun(query="q")
        assert projector.calls[0]["connector"] == "notion"

    async def test_falls_back_to_the_observe_binding_for_the_call_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        projector = self.install_projector(monkeypatch)
        descriptor = self.descriptor()
        inner = self.tool()
        # OBSERVE sits directly outside CITATIONS in MIDDLEWARE_ORDER.
        cited = McpCitationsMiddleware().wrap(inner, descriptor)
        observed = McpObserveMiddleware().wrap(cited, descriptor)
        await observed._arun(query="q", tool_call_id="call_from_binding")
        assert projector.calls[0]["tool_call_id"] == "call_from_binding"

    async def test_a_missing_call_id_is_forwarded_as_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        projector = self.install_projector(monkeypatch)
        wrapped = McpCitationsMiddleware().wrap(self.tool(), self.descriptor())
        await wrapped._arun(query="q")
        assert projector.calls[0]["tool_call_id"] is None

    async def test_projection_failure_never_poisons_the_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.install_projector(monkeypatch, raises=RuntimeError("ledger down"))
        inner = self.tool(result="rows")
        wrapped = McpCitationsMiddleware().wrap(inner, self.descriptor())
        assert await wrapped._arun(query="q") == "rows"

    async def test_a_failed_call_is_never_projected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        projector = self.install_projector(monkeypatch)
        inner = self.tool(failures=[lambda: McpConnectionError("refused")])
        wrapped = McpCitationsMiddleware().wrap(inner, self.descriptor())
        with pytest.raises(McpConnectionError):
            await wrapped._arun(query="q")
        assert projector.calls == []


class TestObserveBinding(MiddlewareFixtureMixin):
    async def test_binds_the_connector_identity_for_the_duration_of_the_call(
        self,
    ) -> None:
        seen: list[Any] = []

        class BindingReader(BaseTool):
            model_config = ConfigDict(arbitrary_types_allowed=True)
            name: str = "linear_create_issue"
            description: str = "Create an issue in Linear."

            def _run(self, *_: Any, **__: Any) -> str:
                seen.append(McpCallBindingScope.current())
                return "ok"

            async def _arun(self, *_: Any, **__: Any) -> str:
                return self._run()

        wrapped = McpObserveMiddleware().wrap(
            BindingReader(), self.descriptor(action=Action.WRITE)
        )
        await wrapped._arun(query="q", tool_call_id="call_7")
        binding = seen[0]
        assert binding is not None
        assert binding.connector == self.CONNECTOR
        assert binding.tool_name == "linear_create_issue"
        assert binding.action is Action.WRITE
        assert binding.tool_call_id == "call_7"
        assert binding.urn == CapabilityUrn.for_mcp(self.CONNECTOR, self.CAPABILITY)

    async def test_unbinds_after_the_call(self) -> None:
        wrapped = McpObserveMiddleware().wrap(self.tool(), self.descriptor())
        await wrapped._arun(query="q")
        assert McpCallBindingScope.current() is None

    async def test_unbinds_after_a_failed_call(self) -> None:
        inner = self.tool(failures=[lambda: McpConnectionError("refused")])
        wrapped = McpObserveMiddleware().wrap(inner, self.descriptor())
        with pytest.raises(McpConnectionError):
            await wrapped._arun(query="q")
        assert McpCallBindingScope.current() is None

    async def test_does_not_consume_the_injected_call_id(self) -> None:
        received: list[dict[str, Any]] = []

        class KwargsCapturingTool(BaseTool):
            model_config = ConfigDict(arbitrary_types_allowed=True)
            name: str = "linear_create_issue"
            description: str = "Create an issue in Linear."

            def _run(self, *_: Any, **kwargs: Any) -> str:
                received.append(kwargs)
                return "ok"

            async def _arun(self, *_: Any, **kwargs: Any) -> str:
                return self._run(**kwargs)

        wrapped = McpObserveMiddleware().wrap(KwargsCapturingTool(), self.descriptor())
        await wrapped._arun(query="q", tool_call_id="call_7")
        assert received[0]["tool_call_id"] == "call_7"


class TestComposedStack(MiddlewareFixtureMixin):
    async def test_a_read_retries_through_the_normalizing_stage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        projector = self.install_projector(monkeypatch)
        inner = self.tool(
            failures=[lambda: McpConnectionError("refused")] * 2, result="rows"
        )
        composed = self.compose(inner, self.descriptor(action=Action.READ))
        assert await composed._arun(query="q") == "rows"
        # ERROR_MAP sits INSIDE EXEC_POLICY: the retry stage only ever sees the
        # normalized failure, and must still retry it.
        assert inner.calls == 3
        assert len(projector.calls) == 1
        assert projector.calls[0]["connector"] == self.CONNECTOR

    async def test_a_write_is_never_replayed_through_the_stack(self) -> None:
        inner = self.tool(failures=[lambda: McpConnectionError("refused")] * 5)
        composed = self.compose(inner, self.descriptor(action=Action.WRITE))
        with pytest.raises(McpToolCallError) as excinfo:
            await composed._arun(query="q")
        assert inner.calls == 1
        assert excinfo.value.envelope.code is McpLoadErrorCode.CONNECTION_FAILED

    async def test_an_ambiguous_dispatch_is_never_replayed_through_the_stack(
        self,
    ) -> None:
        inner = self.tool(failures=[lambda: McpAmbiguousDispatchError("maybe")] * 5)
        composed = self.compose(inner, self.descriptor(action=Action.READ))
        with pytest.raises(McpToolCallError) as excinfo:
            await composed._arun(query="q")
        assert inner.calls == 1
        assert excinfo.value.envelope.replay_safe is False
        assert str(excinfo.value) == McpErrorCopy.AMBIGUOUS_DISPATCH
