"""PRESENT stage — the sixth job the gateway did, and the migration dropped.

The gap these tests close is not a bug in a stage; it is a *missing* stage. The
per-tool pipeline replaced ``CallMcpTool`` with five named stages, but the
gateway also routed every completed call through the Operation Gateway, whose
presenter is what puts ``read.executed`` / ``surface.created`` / ``view.derived``
on the Work Ledger. Nothing in the five stages did that, so turning the per-tool
path on silently stopped MCP results reaching the v2 canvas.

Nothing caught it because every existing test proved one *edge*: the emitter is
bound and maps values; the gateway calls the presenter; a run streams and
persists. The behaviour is a *path*, and no test walked it. So these assert the
join specifically — a per-tool call reaches the presenter — rather than
re-proving either end.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.tools import BaseTool

from agent_runtime.capabilities.mcp.middleware.present_tool import (
    McpPresentMiddleware,
)
from agent_runtime.capabilities.policy.contracts import (
    Action,
    CapabilityDescriptor,
    CapabilityUrn,
    ConnectorState,
    MiddlewareStage,
    Trust,
)

_CONNECTOR = "linear"
_TOOL = "get_issues"


class RecordingPresenter:
    """Stands in for ``SurfaceLedgerOperationOutcomePresenter``."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self.outcomes: list[Any] = []
        self._raises = raises

    async def present(self, outcome: Any) -> None:
        if self._raises is not None:
            raise self._raises
        self.outcomes.append(outcome)


class StubTool(BaseTool):
    """An MCP tool at the shape the adapters return it."""

    name: str = _TOOL
    description: str = "stub"
    response_format: str = "content_and_artifact"
    result: Any = ("issues", {"raw": [1, 2]})

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("MCP tools are driven asynchronously.")

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return self.result


class PresentFixture:
    """Builds the stage over a stub tool."""

    @staticmethod
    def descriptor(action: Action = Action.READ) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            urn=CapabilityUrn.for_mcp(_CONNECTOR, _TOOL),
            action=action,
            trust=Trust.TRUSTED,
            scopes=(),
            source="mcp",
            connector_state=ConnectorState.LIVE,
        )

    @staticmethod
    def wrap(
        *,
        action: Action = Action.READ,
        presenter: RecordingPresenter | None = None,
        tool: BaseTool | None = None,
    ) -> tuple[BaseTool, RecordingPresenter]:
        recorder = presenter or RecordingPresenter()
        wrapped = McpPresentMiddleware(presenter=recorder).wrap(
            tool or StubTool(), PresentFixture.descriptor(action)
        )
        return wrapped, recorder


class TestTheStageIsPartOfTheFixedPipeline:
    """A stage the composer does not know about is a stage that never runs."""

    def test_it_declares_the_present_stage(self) -> None:
        assert McpPresentMiddleware().stage is MiddlewareStage.PRESENT

    def test_the_stage_cannot_be_relabelled(self) -> None:
        """``init=False`` — a mislabelled stage defeats the order guarantee."""

        with pytest.raises(TypeError):
            McpPresentMiddleware(stage=MiddlewareStage.POLICY)  # type: ignore[call-arg]

    def test_it_keeps_the_model_facing_surface_identical(self) -> None:
        inner = StubTool()
        wrapped, _ = PresentFixture.wrap(tool=inner)

        assert wrapped.name == inner.name
        assert wrapped.response_format == inner.response_format
        assert wrapped.metadata == inner.metadata
        assert wrapped.return_direct == inner.return_direct


class TestAnExecutedReadReachesTheLedger:
    """The join that was missing: a per-tool call reaches the presenter."""

    async def test_a_read_is_presented(self) -> None:
        wrapped, presenter = PresentFixture.wrap()

        await wrapped.ainvoke({"tool_call_id": "call-1"})

        assert len(presenter.outcomes) == 1

    async def test_the_outcome_carries_the_generic_descriptor_identity(self) -> None:
        """``capability``/``op``, not an MCP-shaped payload — see presentation.py."""

        wrapped, presenter = PresentFixture.wrap()

        await wrapped.ainvoke({"tool_call_id": "call-1"})

        outcome = presenter.outcomes[0]
        assert outcome.capability == _CONNECTOR
        assert outcome.op == _TOOL

    async def test_the_structured_half_of_the_result_is_what_is_projected(
        self,
    ) -> None:
        """MCP returns ``(content, artifact)``; the artifact is the data."""

        wrapped, presenter = PresentFixture.wrap()

        await wrapped.ainvoke({"tool_call_id": "call-1"})

        assert presenter.outcomes[0].output == {"raw": [1, 2]}

    async def test_the_call_id_ties_the_surface_to_the_tool_call(self) -> None:
        wrapped, presenter = PresentFixture.wrap()

        await wrapped.ainvoke({"tool_call_id": "call-abc"})

        assert presenter.outcomes[0].operation_id == "call-abc"

    async def test_the_result_ref_is_logical_not_a_path(self) -> None:
        """``OperationPresentationOutcome`` refuses a filesystem-shaped ref."""

        wrapped, presenter = PresentFixture.wrap()

        await wrapped.ainvoke({"tool_call_id": "call-1"})

        ref = presenter.outcomes[0].result_ref
        assert not ref.startswith("/")
        assert ":\\" not in ref

    async def test_the_tool_result_is_returned_unchanged(self) -> None:
        """Presenting is a side effect; the model must see the tool's own result.

        A plain-dict invoke yields the content half of ``content_and_artifact``
        — LangChain only builds the two-tuple for a real ``ToolCall``. What
        matters here is that the stage returns it untouched.
        """

        wrapped, _ = PresentFixture.wrap()

        assert await wrapped.ainvoke({"tool_call_id": "call-1"}) == "issues"


class TestAWriteIsNotPresentedHere:
    """A write parks on the gate and is presented by the approval path."""

    async def test_a_write_does_not_double_emit(self) -> None:
        wrapped, presenter = PresentFixture.wrap(action=Action.WRITE)

        await wrapped.ainvoke({"tool_call_id": "call-1"})

        assert presenter.outcomes == []

    async def test_a_write_still_returns_its_result(self) -> None:
        wrapped, _ = PresentFixture.wrap(action=Action.WRITE)

        assert await wrapped.ainvoke({"tool_call_id": "call-1"}) is not None


class TestRenderingNeverFailsTheCall:
    """The connector answered and the model has the data; a canvas is a projection."""

    async def test_a_presenter_failure_does_not_fail_the_tool(self) -> None:
        wrapped, _ = PresentFixture.wrap(
            presenter=RecordingPresenter(raises=RuntimeError("ledger is down"))
        )

        assert await wrapped.ainvoke({"tool_call_id": "call-1"}) == "issues"

    async def test_a_call_without_an_id_still_presents(self) -> None:
        """``tool_call_id`` is injected only when the schema opted in."""

        wrapped, presenter = PresentFixture.wrap()

        await wrapped.ainvoke({})

        assert len(presenter.outcomes) == 1
