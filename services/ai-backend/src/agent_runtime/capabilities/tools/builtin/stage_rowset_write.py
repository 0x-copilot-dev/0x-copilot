"""Built-in compatibility alias for a generic bulk row-set effect proposal.

The agent hands over N per-row changes (each a stable ``row_key``, a human
``title``, the exact connector-op args, and old→new diffs) plus optional
pre-holds. The tool forms a canonical ``row_set`` proposal and delegates only
to an injected proposal port. In enforce mode that port must be backed by the
generic effect stager; the tool has no executor, transport, or surface-emitter
capability. Staging remains non-blocking (NFR-7): the user decides later on
the durable effect surface.

Fail-closed: tool input is untrusted until ``RowsetValidator`` runs inside the
stager. A validation / domain error becomes a safe tool-result error dict (the
run keeps going), never an exception into the graph. The tool NEVER touches an
MCP client — only the shared effect-dispatch path dispatches.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import Field, ValidationError

from agent_runtime.api.constants import Values
from agent_runtime.capabilities.operations.builtin_adapter import (
    BuiltinGatewayGateResolver,
    BuiltinOperationAdapter,
)
from agent_runtime.capabilities.operations.catalog import (
    DEFAULT_OPERATION_DESCRIPTORS,
)
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationRequestFactory,
)
from agent_runtime.capabilities.operations.contracts import (
    OperationGatewayMode,
    OperationRawResult,
    ProposedEffect as GatewayProposedEffect,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.prompts.tools import STAGE_ROWSET_WRITE_TOOL_DESCRIPTION
from agent_runtime.surfaces_v2.ledger_models import (
    EffectProposalKind,
    OperationOutcome,
)
from agent_runtime.surfaces_v2.ledger_ids import EffectStageIdCodec, ProposalUriCodec
from agent_runtime.surfaces_v2.rowset import AgentHold, StagedRow
from agent_runtime.surfaces_v2.staging import StagedWriteError, WriteStager
from agent_runtime.capabilities.surfaces.builtin import server_slug, tool_slug

_OPERATION = BuiltinOperationAdapter(tool_name=Values.Tool.STAGE_ROWSET_WRITE)

# The builtin executor is deliberately not a generic MCP bridge.  Adding a
# target here is a product/security review: the target must have a stable
# connector contract, a reviewed staging flow, and adversarial executor tests.
# All other connector operations use the canonical MCP effect adapter instead.
REVIEWED_ROWSET_TARGETS = frozenset({("linear", "update_issue")})


class _Fields:
    """Field-name constants for the tool input + result."""

    TARGET_CONNECTOR = "target_connector"
    TARGET_OP = "target_op"
    TITLE = "title"
    ROWS = "rows"
    AGENT_HOLDS = "agent_holds"
    STAGE_ID = "stage_id"
    SURFACE_ID = "surface_id"
    ROWS_STAGED = "rows_staged"
    ROWS_PRE_HELD = "rows_pre_held"
    STATUS = "status"


class _Limits:
    """Length caps for the tool's own string fields (rows/holds cap in the validator)."""

    CONNECTOR_MAX = 200
    OP_MAX = 200
    TITLE_MAX = 200


class _Messages:
    """Safe public messages returned to the agent on a rejected proposal."""

    MALFORMED = "The row-set proposal is malformed and was not staged."
    TARGET_NOT_REVIEWED = "This row-set target is not enabled for staged execution."
    UNAVAILABLE = "Bulk staging is not available in this run."


def reviewed_rowset_target(
    *, target_connector: str, target_op: str
) -> tuple[str, str] | None:
    """Return one explicit builtin target, never a model-selected connector path."""

    normalized = (server_slug(target_connector), tool_slug(target_op))
    return normalized if normalized in REVIEWED_ROWSET_TARGETS else None


class StageRowsetWriteInput(RuntimeContract):
    """Input contract for ``stage_rowset_write``.

    ``rows`` / ``agent_holds`` reuse the ``rowset.py`` contracts verbatim — they
    already validate every field (row_key / title / target_args / changes;
    row_key / reason). No separate ``*Input`` mirror types are introduced. Tool
    input stays untrusted until :class:`RowsetValidator` runs in the stager.
    """

    target_connector: str = Field(min_length=1, max_length=_Limits.CONNECTOR_MAX)
    target_op: str = Field(min_length=1, max_length=_Limits.OP_MAX)
    title: str = Field(min_length=1, max_length=_Limits.TITLE_MAX)
    rows: tuple[StagedRow, ...]
    agent_holds: tuple[AgentHold, ...] = ()


class RowSetEffectProposal(RuntimeContract):
    """Canonical input to the generic row-set proposal seam.

    The compatibility tool only forms this value.  It cannot decide approval,
    resolve an executor, call a connector, or produce a presentation event.
    A4/A5-owned proposal adapters may map it to durable generic staging.
    """

    proposal_kind: Literal[EffectProposalKind.ROW_SET] = EffectProposalKind.ROW_SET
    target_connector: str = Field(min_length=1, max_length=_Limits.CONNECTOR_MAX)
    target_op: str = Field(min_length=1, max_length=_Limits.OP_MAX)
    title: str = Field(min_length=1, max_length=_Limits.TITLE_MAX)
    rows: tuple[StagedRow, ...]
    agent_holds: tuple[AgentHold, ...] = ()


class RowSetProposalReceipt(RuntimeContract):
    """Bounded stage result returned by a generic row-set proposal port."""

    stage_id: str = Field(min_length=1, max_length=128)
    proposal_ref: str = Field(min_length=1, max_length=2048)
    surface_id: str = Field(min_length=1, max_length=2048)
    rows_staged: int = Field(ge=0)
    rows_pre_held: int = Field(ge=0)
    status: str = Field(min_length=1, max_length=64)


@runtime_checkable
class RowSetEffectProposalPort(Protocol):
    """The only capability the row-set compatibility alias is allowed to use."""

    async def stage_row_set(
        self,
        *,
        proposal: RowSetEffectProposal,
        operation_id: str | None,
    ) -> RowSetProposalReceipt: ...


@dataclass(frozen=True)
class LegacyRowSetEffectProposalPort:
    """Temporary adapter from the v2 row-set stager to the generic proposal port.

    It has staging-only authority.  ``WriteStager`` contains no connector
    handle and does not apply an effect, so the compatibility bridge cannot
    bypass the A5 commit path while runtime wiring migrates to the generic A4
    stager.
    """

    stager: WriteStager
    run: object
    org_id: str
    run_id: str

    async def stage_row_set(
        self,
        *,
        proposal: RowSetEffectProposal,
        operation_id: str | None,
    ) -> RowSetProposalReceipt:
        del operation_id
        state = await self.stager.stage_rowset(
            run=self.run,
            org_id=self.org_id,
            run_id=self.run_id,
            target_connector=proposal.target_connector,
            target_op=proposal.target_op,
            rows=proposal.rows,
            agent_holds=proposal.agent_holds,
            title=proposal.title,
        )
        revision = state.latest_revision()
        if revision is None or not revision.proposal_ref:
            raise StagedWriteError("Bulk staging did not return a proposal reference.")
        counts = state.row_counts
        return RowSetProposalReceipt(
            stage_id=state.stage_id,
            proposal_ref=revision.proposal_ref,
            surface_id=state.surface_id,
            rows_staged=counts.total if counts is not None else 0,
            rows_pre_held=counts.held if counts is not None else 0,
            status=state.status.value,
        )


@dataclass(frozen=True)
class StageRowsetWriteTool:
    """Stage a bulk row-set write; return a summary to the model (non-blocking)."""

    run: object
    org_id: str
    run_id: str
    stager: WriteStager | None = None
    proposal_stager: RowSetEffectProposalPort | None = None
    name: str = Values.Tool.STAGE_ROWSET_WRITE
    description: str = STAGE_ROWSET_WRITE_TOOL_DESCRIPTION

    async def ainvoke(
        self, raw_input: StageRowsetWriteInput | Mapping[str, Any] | str
    ) -> dict[str, Any]:
        """Validate input, stage the row-set, and return the stage summary."""

        try:
            parsed = self._parse(raw_input)
        except ValidationError:
            return {"ok": False, "message": _Messages.MALFORMED}

        proposal = RowSetEffectProposal(
            target_connector=parsed.target_connector,
            target_op=parsed.target_op,
            title=parsed.title,
            rows=parsed.rows,
            agent_holds=parsed.agent_holds,
        )
        try:
            receipt = await self._stage(proposal)
        except StagedWriteError as exc:
            # Typed domain rejection (caps / duplicate keys / holds ⊆ rows) →
            # safe tool-result error; the run continues, NOTHING was staged.
            return {"ok": False, "message": exc.safe_message}

        return {
            "ok": True,
            _Fields.STAGE_ID: receipt.stage_id,
            _Fields.SURFACE_ID: receipt.surface_id,
            _Fields.ROWS_STAGED: receipt.rows_staged,
            _Fields.ROWS_PRE_HELD: receipt.rows_pre_held,
            _Fields.STATUS: receipt.status,
        }

    async def __call__(
        self, raw_input: StageRowsetWriteInput | Mapping[str, Any] | str
    ) -> dict[str, Any]:
        return await self.ainvoke(raw_input)

    @staticmethod
    def _parse(
        raw_input: StageRowsetWriteInput | Mapping[str, Any] | str,
    ) -> StageRowsetWriteInput:
        if isinstance(raw_input, StageRowsetWriteInput):
            return raw_input
        return StageRowsetWriteInput.model_validate(raw_input)

    async def _stage(self, proposal: RowSetEffectProposal) -> RowSetProposalReceipt:
        """Stage through the authoritative gateway in enforce mode only."""

        if (
            reviewed_rowset_target(
                target_connector=proposal.target_connector,
                target_op=proposal.target_op,
            )
            is None
        ):
            raise StagedWriteError(_Messages.TARGET_NOT_REVIEWED)

        # Construction validates this callable, but repeat the catalogue lookup
        # at the model-facing seam so a future mutable/test catalogue cannot
        # leave an unreviewed staging path live.
        _OPERATION.entry()
        context = OperationContext.active()
        if context is None or context.mode is not OperationGatewayMode.ENFORCE:
            if self.proposal_stager is not None:
                return await self.proposal_stager.stage_row_set(
                    proposal=proposal,
                    operation_id=None,
                )
            if self.stager is None:
                raise StagedWriteError(_Messages.UNAVAILABLE)
            # Compatibility-only: the historical v2 stager is deliberately
            # never allowed into an authoritative operation. It has legacy
            # UUID stage ids and emits its own v2 surface events, neither of
            # which is a generic A4 effect-stage contract.
            return await LegacyRowSetEffectProposalPort(
                stager=self.stager,
                run=self.run,
                org_id=self.org_id,
                run_id=self.run_id,
            ).stage_row_set(proposal=proposal, operation_id=None)
        if self.proposal_stager is None:
            # D1/A4 runtime wiring must inject an EffectStager-backed port.
            # Fail before any v2 writer can emit a legacy surface.
            raise StagedWriteError(_Messages.UNAVAILABLE)
        request = OperationRequestFactory.create(
            capability="builtin",
            op=Values.Tool.STAGE_ROWSET_WRITE,
            arguments=proposal.model_dump(mode="json"),
        )
        adapter = _RowSetGatewayAdapter(
            proposal_stager=self.proposal_stager,
            proposal=proposal,
        )
        disposition = await OperationGateway(
            descriptors=DEFAULT_OPERATION_DESCRIPTORS,
            gates=BuiltinGatewayGateResolver(),
        ).invoke(request, adapter)
        if (
            disposition.outcome is not OperationOutcome.STAGED
            or adapter.receipt is None
        ):
            raise StagedWriteError(disposition.agent_summary)
        return adapter.receipt


@dataclass
class _RowSetGatewayAdapter:
    """Expose only proposal construction to the universal operation gateway."""

    proposal_stager: RowSetEffectProposalPort
    proposal: RowSetEffectProposal
    receipt: RowSetProposalReceipt | None = None

    async def execute_read(self, request: object) -> OperationRawResult:
        del request
        raise StagedWriteError("A row-set write must be staged, never read-executed.")

    async def build_proposal(self, request: object) -> GatewayProposedEffect:
        operation_id = getattr(request, "operation_id", None)
        if not isinstance(operation_id, str):
            raise StagedWriteError("The row-set proposal is missing its operation id.")
        self.receipt = await self.proposal_stager.stage_row_set(
            proposal=self.proposal,
            operation_id=operation_id,
        )
        EffectStageIdCodec.parse(self.receipt.stage_id)
        parsed_proposal = ProposalUriCodec.parse(self.receipt.proposal_ref)
        if parsed_proposal.stage_id != self.receipt.stage_id:
            raise StagedWriteError("The row-set proposal receipt is inconsistent.")
        return GatewayProposedEffect(
            stage_id=self.receipt.stage_id,
            proposal_ref=self.receipt.proposal_ref,
            safe_summary="Proposed row-set write; waiting for review.",
            activity_ref=self.receipt.surface_id,
        )


__all__ = [
    "LegacyRowSetEffectProposalPort",
    "RowSetEffectProposal",
    "RowSetEffectProposalPort",
    "RowSetProposalReceipt",
    "REVIEWED_ROWSET_TARGETS",
    "StageRowsetWriteInput",
    "StageRowsetWriteTool",
    "reviewed_rowset_target",
]
