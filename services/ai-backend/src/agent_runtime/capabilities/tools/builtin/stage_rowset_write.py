"""Built-in tool that stages a BULK row-set write for per-row review (PRD-D3).

The propose seam for the row-set shape: the agent hands over N per-row changes
(each a stable ``row_key``, a human ``title``, the exact connector-op args, and
old→new diffs) plus optional pre-holds; the tool validates, delegates to
:meth:`WriteStager.stage_rowset`, and returns a summary. It does NOT interrupt
the graph — staging is non-blocking (NFR-7); the user decides on the surface
while the run continues.

Fail-closed: tool input is untrusted until ``RowsetValidator`` runs inside the
stager. A validation / domain error becomes a safe tool-result error dict (the
run keeps going), never an exception into the graph. The tool NEVER touches an
MCP client — only the CommitEngine path dispatches.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import Field, ValidationError

from agent_runtime.api.constants import Values
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.prompts.tools import STAGE_ROWSET_WRITE_TOOL_DESCRIPTION
from agent_runtime.surfaces_v2.rowset import AgentHold, StagedRow
from agent_runtime.surfaces_v2.staging import StagedWriteError, WriteStager


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
    UNAVAILABLE = "Bulk staging is not available in this run."


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


@dataclass(frozen=True)
class StageRowsetWriteTool:
    """Stage a bulk row-set write; return a summary to the model (non-blocking)."""

    stager: WriteStager
    run: object
    org_id: str
    run_id: str
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

        try:
            state = await self.stager.stage_rowset(
                run=self.run,
                org_id=self.org_id,
                run_id=self.run_id,
                target_connector=parsed.target_connector,
                target_op=parsed.target_op,
                rows=parsed.rows,
                agent_holds=parsed.agent_holds,
                title=parsed.title,
            )
        except StagedWriteError as exc:
            # Typed domain rejection (caps / duplicate keys / holds ⊆ rows) →
            # safe tool-result error; the run continues, NOTHING was staged.
            return {"ok": False, "message": exc.safe_message}

        counts = state.row_counts
        return {
            "ok": True,
            _Fields.STAGE_ID: state.stage_id,
            _Fields.SURFACE_ID: state.surface_id,
            _Fields.ROWS_STAGED: counts.total if counts is not None else 0,
            _Fields.ROWS_PRE_HELD: counts.held if counts is not None else 0,
            _Fields.STATUS: state.status.value,
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


__all__ = ["StageRowsetWriteInput", "StageRowsetWriteTool"]
