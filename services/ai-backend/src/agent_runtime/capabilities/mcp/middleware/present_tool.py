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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import logging
import time
from types import MappingProxyType
from typing import Any, Final

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import ConfigDict

from agent_runtime.capabilities.delegating_tool import NO_CONFIG, DelegatingTool
from agent_runtime.capabilities.mcp.middleware.compose import ToolSchemaIdentity
from agent_runtime.capabilities.mcp.tool_naming import McpToolName
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
from agent_runtime.capabilities.surfaces.write_ops_capture import (
    CapturedWriteOps,
    WriteOpCandidate,
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
    #: The connector-register tool name — the presenter's ``op``. Deliberately
    #: not ``name`` (the model-surface, ``mcp__``-namespaced one this tool also
    #: carries): an ``op`` names something at the connector.
    tool_name: str
    #: Presenting a write here would double-emit against the approval path.
    presentable: bool
    #: This connector's OWN write ops, read off the same catalogue that produced
    #: this tool. The read this stage presents is the one moment those
    #: descriptors are in hand — the server is loaded, its ``input_schema``s are
    #: in memory — and the connector write-back lane needs them hours later in a
    #: different process. Capturing here is what lets that lane be a lookup
    #: instead of an MCP client on an HTTP request path.
    write_ops: tuple[WriteOpCandidate, ...] = ()
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
                    write_ops=self.write_ops,
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
        """This call's id, or :data:`PresentValues.ANONYMOUS_CALL`.

        **On the live agent path this is ALWAYS anonymous, and that is a
        structural fact rather than a bug to patch here.** Three independent
        doors into the tool layer are shut, each verified:

        * the kwarg arrives only for a tool whose ``args_schema`` declares
          ``Annotated[str, InjectedToolCallId]``. An MCP tool's ``args_schema``
          is the server's raw JSON-Schema **dict**, not a ``BaseModel``, so
          there is nowhere to hang the annotation — and synthesising a model to
          host it would replace every argument the connector declares (see
          ``ToolSchemaIdentity``);
        * the OBSERVE binding reads the same absent kwarg, so it carries no id
          either;
        * the ToolCall itself never reaches a wrapper. A live probe showed the
          outermost wrapper receiving bare arguments (``keys=[]``) — the call
          envelope is unwrapped upstream.

        So the id is joined where it EXISTS instead: ``SurfaceContentProjection``
        matches a surface to its ``tool_result`` by ``(connector, op)`` and event
        order. The kwarg is still read first because a caller that does supply a
        real id (every unit test, and any future injected path) should win over
        the positional join.
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


class ConnectorWriteOpCatalogue:
    """``connector -> that connector's WRITE ops``, read off a loaded catalogue.

    The one honest place this map can be built. ``descriptor.action`` is the
    classification the source already made, and ``BaseTool.args_schema`` for an
    MCP tool is the server's raw ``inputSchema`` **dict** (see
    :class:`ToolSchemaIdentity`) — so the pair the registrar already holds
    carries both halves of a write-op descriptor, and nothing has to be
    re-fetched or re-classified to assemble them.

    Building it here rather than at save time is the whole design: by the time a
    user presses Save the session is gone, and reconstructing it would mean an
    MCP client on an HTTP request path.

    A tool whose ``args_schema`` is not a mapping is still captured, with an
    empty schema. It then refuses loudly if the model chooses it (an op with no
    declared ``required`` cannot bound a write), which is the diagnosable
    outcome; dropping it would instead read as *"this connector has no write
    op"*.
    """

    @classmethod
    def from_pairs(
        cls, pairs: Iterable[tuple[BaseTool, CapabilityDescriptor]]
    ) -> Mapping[str, tuple[WriteOpCandidate, ...]]:
        """Group every WRITE tool's descriptor under its connector namespace.

        Names are captured in the CONNECTOR register, not the model-surface one.
        A candidate here is an op the write-back lane will later dispatch at the
        connector, and it is already grouped under that connector — so carrying
        ``mcp__linear__`` on it would be both redundant and, at dispatch, wrong.
        """

        grouped: dict[str, list[WriteOpCandidate]] = {}
        for tool, descriptor in pairs:
            if descriptor.action is not Action.WRITE:
                continue
            name = McpToolName.strip(tool.name or "")
            if not name:
                continue
            grouped.setdefault(
                CapabilityUrn.parse(descriptor.urn).namespace, []
            ).append(
                WriteOpCandidate(
                    name=name,
                    description=tool.description or "",
                    input_schema=cls._schema_of(tool),
                )
            )
        return MappingProxyType(
            {
                connector: CapturedWriteOps.bounded(ops)
                for connector, ops in grouped.items()
            }
        )

    @staticmethod
    def _schema_of(tool: BaseTool) -> dict[str, Any]:
        """The server's raw ``inputSchema``, or ``{}`` when it is not one."""

        schema = getattr(tool, "args_schema", None)
        return dict(schema) if isinstance(schema, Mapping) else {}


@dataclass
class McpPresentMiddleware:
    """The PRESENT stage: an executed read reaches the Work Ledger."""

    # ``init=False`` for the same reason every other stage does it: a
    # mislabelled middleware defeats the composer's order guarantee.
    stage: MiddlewareStage = field(default=MiddlewareStage.PRESENT, init=False)
    #: ``connector -> WRITE ops``, from :class:`ConnectorWriteOpCatalogue`. The
    #: registrar supplies it because only the registrar has seen every tool the
    #: run loaded; the default empty map keeps every unit that composes this
    #: stage alone working, and simply captures nothing.
    write_ops: Mapping[str, tuple[WriteOpCandidate, ...]] = field(default_factory=dict)
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
            # Connector register, matching ``ConnectorWriteOpCatalogue`` — the
            # surface's read ``op`` and its captured write ops must be in one
            # register or the write-back mapper compares two vocabularies.
            tool_name=McpToolName.strip(tool.name),
            presentable=descriptor.action is Action.READ,
            write_ops=self.write_ops.get(parsed.namespace, ()),
            presenter=self.presenter,
        )


__all__ = (
    "ConnectorWriteOpCatalogue",
    "McpPresentMiddleware",
    "McpPresentedTool",
    "PresentValues",
)
