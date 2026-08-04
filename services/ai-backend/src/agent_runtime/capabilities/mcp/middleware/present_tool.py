"""PRESENT stage — put one executed read on the Work Ledger.

The stage the per-tool migration was missing, and the reason it could not
replace the gateway.

``CallMcpTool`` did six jobs. Five became the named stages of this pipeline;
the sixth was never named, so nobody noticed it was gone. That job was routing
a completed call through the Operation Gateway, whose
:class:`~agent_runtime.capabilities.operations.presentation.SurfaceLedgerOperationOutcomePresenter`
is what emits ``read.executed`` / ``surface.created`` / ``view.derived``. Without
it an MCP result never reaches the v2 canvas: the connector answers, the model
reads the answer, and the user sees nothing rendered.

**The presenter was never MCP-specific.** Its own docstring says the contract is
"deliberately not MCP-shaped: ``capability`` and ``op`` are the generic
descriptor identity". Only its *caller* was MCP-specific, which is precisely why
the concern could go missing when the caller was replaced. This stage restores
the call at the one place that survives any future dispatch change — the
per-tool pipeline itself.

**Reads only.** The gateway presented "each executed read". A write parks on the
approval gate and is presented by the approval path, so presenting it here would
double-emit.

**Never raises.** Failing to render a result must not fail the call that
produced it. The model already has the data; the canvas is a projection of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
from typing import Any, Final

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import ConfigDict

from agent_runtime.capabilities.delegating_tool import NO_CONFIG, DelegatingTool
from agent_runtime.capabilities.mcp.middleware.compose import ToolSchemaIdentity
from agent_runtime.capabilities.mcp.middleware.observe_tool import McpCallBindingScope
from agent_runtime.capabilities.operations.contracts import (
    OperationPresentationOutcome,
)
from agent_runtime.capabilities.operations.presentation import (
    SurfaceLedgerOperationOutcomePresenter,
)
from agent_runtime.capabilities.policy.contracts import (
    Action,
    CapabilityDescriptor,
    CapabilityUrn,
    MiddlewareStage,
)

_LOGGER = logging.getLogger(__name__)


class _PresentLog:
    """Log messages for the one stage whose failures are invisible by design."""

    SKIPPED_NO_OUTPUT: Final = (
        "mcp present stage: result carried no presentable output (connector=%s tool=%s)"
    )
    FAILED: Final = (
        "mcp present stage failed; the call succeeded but its surface was not "
        "rendered (connector=%s tool=%s)"
    )


class PresentValues:
    """Local spellings for the presentation contract."""

    #: ``result_ref`` must be logical, not a filesystem path
    #: (``OperationPresentationOutcome`` refuses a leading ``/`` or a drive).
    REF_TEMPLATE: Final = "mcp/{connector}/{tool}/{call_id}"
    ANONYMOUS_CALL: Final = "unattributed"
    OUTPUT_KEY: Final = "result"


#: Read from kwargs, never added and never removed — see ``McpPresentedTool._call_id``.
#: Same spelling ``McpObservedTool`` / ``McpCitedTool`` use.
_TOOL_CALL_ID: Final = "tool_call_id"


class McpPresentedTool(DelegatingTool):
    """Schema-identical wrapper that presents each executed read.

    Async only, for the same reason ``McpCitationsTool`` is: the emitter is
    async, the runtime always dispatches through ``_arun``, and a direct sync
    unit-test invocation must not need an event loop.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseTool
    #: Connector slug from the descriptor URN — the presenter's ``capability``.
    connector: str
    #: The registered tool name — the presenter's ``op``.
    tool_name: str
    #: Presenting a write here would double-emit against the approval path.
    presentable: bool
    presenter: Any = None

    def _run(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Sync path: delegate unchanged — presentation requires the async path."""

        return self.delegate(*args, config=config, **kwargs)

    async def _arun(
        self, *args: Any, config: RunnableConfig = NO_CONFIG, **kwargs: Any
    ) -> Any:
        """Async path: delegate, then put the outcome on the ledger."""

        started = time.monotonic()
        result = await self.adelegate(*args, config=config, **kwargs)
        if self.presentable:
            await self._present(
                result, kwargs, latency_ms=int((time.monotonic() - started) * 1000)
            )
        return result

    async def _present(
        self, result: object, kwargs: dict[str, Any], *, latency_ms: int
    ) -> None:
        """Project one outcome, best-effort.

        Swallows everything on purpose: the connector already answered and the
        model already has the result. A rendering failure must not turn a
        successful tool call into a failed one.
        """

        try:
            output = self._output_of(result)
            if output is None:
                _LOGGER.debug(
                    _PresentLog.SKIPPED_NO_OUTPUT, self.connector, self.tool_name
                )
                return
            call_id = self._call_id(kwargs)
            presenter = self.presenter or SurfaceLedgerOperationOutcomePresenter()
            await presenter.present(
                OperationPresentationOutcome(
                    operation_id=call_id,
                    capability=self.connector,
                    op=self.tool_name,
                    result_ref=PresentValues.REF_TEMPLATE.format(
                        connector=self.connector,
                        tool=self.tool_name,
                        call_id=call_id,
                    ),
                    output=output,
                    latency_ms=latency_ms,
                )
            )
        except Exception:  # noqa: BLE001 — see the docstring
            _LOGGER.warning(
                _PresentLog.FAILED,
                self.connector,
                self.tool_name,
                exc_info=True,
            )

    @staticmethod
    def _call_id(kwargs: dict[str, Any]) -> str:
        """This call's id: the injected kwarg, else the OBSERVE binding.

        The kwarg exists only when the inner tool's schema declares
        ``InjectedToolCallId``, which an MCP tool's schema does not — so on the
        live agent path it is simply absent, and reading it alone stamped every
        surface ``payload_ref: "call:unattributed"``. That reference is the only
        join between a surface and the ``tool_result`` holding its payload, so a
        constant sentinel meant the fold could never resolve one and every live
        surface rendered empty. Unit tests pass ``call_id`` explicitly and never
        entered the state.

        The OBSERVE stage sits directly outside this one in the pinned
        middleware order and binds the real id for the duration of the call, so
        this is the same fallback :class:`McpCitedTool` already relies on — one
        id, agreed by every stage that reports on the call.
        """

        value = kwargs.get(_TOOL_CALL_ID)
        if isinstance(value, str) and value:
            return value
        binding = McpCallBindingScope.current()
        if binding is not None and binding.tool_call_id:
            return str(binding.tool_call_id)
        return PresentValues.ANONYMOUS_CALL

    @staticmethod
    def _output_of(result: object) -> dict[str, object] | None:
        """Normalise a ``content_and_artifact`` return into the presenter's dict.

        MCP tools return ``(content, artifact)``. The artifact is the structured
        half and is preferred — but it exists only when the *server* sent
        ``structuredContent``: ``langchain-mcp-adapters`` builds
        ``MCPToolArtifact`` from that field alone and otherwise leaves the half
        ``None`` (``tools.py::_convert_call_tool_result``). Reading the artifact
        and giving up when it was missing therefore dropped every text-only
        connector off the canvas entirely — no surface, no ledger event, for a
        call that had succeeded and had a perfectly good answer in ``content``.

        So the content half is a fallback, not a second-class citizen. ``None``
        is reserved for the genuinely empty result — both halves absent. A
        non-mapping payload (a text blob, a list of content blocks, a scalar) is
        wrapped, because the shape a connector answers in is not a reason to
        drop it off the canvas either.
        """

        payload: object = result
        if isinstance(result, tuple) and len(result) == 2:
            content, artifact = result
            payload = content if artifact is None else artifact
        if payload is None:
            return None
        if isinstance(payload, dict):
            return dict(payload)
        return {PresentValues.OUTPUT_KEY: payload}


@dataclass
class McpPresentMiddleware:
    """The PRESENT stage: an executed read reaches the Work Ledger."""

    # ``init=False`` for the same reason every other stage does it: a
    # mislabelled middleware defeats the composer's order guarantee.
    stage: MiddlewareStage = field(default=MiddlewareStage.PRESENT, init=False)
    #: Overridden only by tests; production resolves the run-scoped emitter
    #: through the presenter's own ContextVar.
    presenter: Any = None

    def wrap(self, tool: BaseTool, descriptor: CapabilityDescriptor) -> BaseTool:
        """Return ``tool`` wrapped so its reads reach the ledger."""

        parsed = CapabilityUrn.parse(descriptor.urn)
        return McpPresentedTool(
            # Schema identity is not optional: the composer refuses a stage that
            # changes the model-facing surface, and dropping `response_format`
            # alone would deliver the MCP tuple to the model as a repr.
            **ToolSchemaIdentity.fields_of(tool),
            inner=tool,
            connector=parsed.namespace,
            tool_name=tool.name,
            presentable=descriptor.action is Action.READ,
            presenter=self.presenter,
        )


__all__ = (
    "McpPresentMiddleware",
    "McpPresentedTool",
    "PresentValues",
)
