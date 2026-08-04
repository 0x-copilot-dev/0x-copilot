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

The same shape of gap sat one layer down and lasted longer: the stage ran, but
``_output_of`` read only the *artifact* half of the MCP tuple, which
``langchain-mcp-adapters`` fills in solely from a server's
``structuredContent``. Every text-only connector therefore succeeded, logged
"no presentable output", and rendered nothing. The tests below drive that half
absent — including through the adapters' own converter, so the assumption about
the library is pinned rather than restated.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import _convert_call_tool_result
from mcp.types import CallToolResult, TextContent

from agent_runtime.capabilities.mcp.middleware.present_tool import (
    McpPresentMiddleware,
    PresentValues,
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
        # ``None`` keeps the stub's own default; pass a tuple to drive one shape.
        result: Any = None,
    ) -> tuple[BaseTool, RecordingPresenter]:
        recorder = presenter or RecordingPresenter()
        stub = tool or (StubTool() if result is None else StubTool(result=result))
        wrapped = McpPresentMiddleware(presenter=recorder).wrap(
            stub, PresentFixture.descriptor(action)
        )
        return wrapped, recorder

    @staticmethod
    def text_only_server_result(text: str) -> tuple[Any, Any]:
        """The exact tuple the adapters build for a server that answers in text.

        Hand-writing ``(content, None)`` would only prove we agree with
        ourselves. Driving a real ``CallToolResult`` through the library's own
        converter pins the behaviour the fallback exists for: the artifact half
        is populated from ``structuredContent`` alone, so a text-only server
        leaves it ``None``. A library change to that rule breaks here rather
        than silently emptying the canvas again.
        """

        return _convert_call_tool_result(
            CallToolResult(
                content=[TextContent(type="text", text=text)],
                structuredContent=None,
                isError=False,
            )
        )


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


class TestATextOnlyServerStillReachesTheCanvas:
    """A missing artifact is not a missing answer.

    ``langchain-mcp-adapters`` builds the artifact half from a server's
    ``structuredContent`` alone, so every server that answers in plain text
    leaves it ``None``. Reading only that half dropped those calls before the
    presenter — successful, answered, and invisible. The content half is the
    fallback.
    """

    async def test_a_result_without_an_artifact_is_still_presented(self) -> None:
        wrapped, presenter = PresentFixture.wrap(result=("ISS-1 Fix login", None))

        await wrapped.ainvoke({"tool_call_id": "call-1"})

        assert len(presenter.outcomes) == 1

    async def test_a_text_content_half_is_wrapped_like_any_other_scalar(self) -> None:
        wrapped, presenter = PresentFixture.wrap(result=("ISS-1 Fix login", None))

        await wrapped.ainvoke({"tool_call_id": "call-1"})

        assert presenter.outcomes[0].output == {
            PresentValues.OUTPUT_KEY: "ISS-1 Fix login"
        }

    async def test_a_list_of_content_blocks_is_projected_whole(self) -> None:
        """The adapters' real content half is a list, not a string."""

        blocks = [{"type": "text", "text": "ISS-1"}, {"type": "text", "text": "ISS-2"}]
        wrapped, presenter = PresentFixture.wrap(result=(blocks, None))

        await wrapped.ainvoke({"tool_call_id": "call-1"})

        assert presenter.outcomes[0].output == {PresentValues.OUTPUT_KEY: blocks}

    async def test_a_mapping_content_half_is_projected_unwrapped(self) -> None:
        """Same rule the artifact half gets: a mapping is already the output."""

        wrapped, presenter = PresentFixture.wrap(result=({"issues": [1, 2]}, None))

        await wrapped.ainvoke({"tool_call_id": "call-1"})

        assert presenter.outcomes[0].output == {"issues": [1, 2]}

    async def test_the_artifact_still_wins_when_the_server_sent_one(self) -> None:
        """A fallback, not a replacement — structured data remains the better half."""

        wrapped, presenter = PresentFixture.wrap(
            result=("ISS-1 Fix login", {"issues": [1, 2]})
        )

        await wrapped.ainvoke({"tool_call_id": "call-1"})

        assert presenter.outcomes[0].output == {"issues": [1, 2]}


class TestARealAdaptersResultWithNoStructuredContent:
    """The same claim, driven through the library that makes it true.

    The four defects this fix belongs to were all green under tests that agreed
    with the code's assumptions. So this one builds an actual ``CallToolResult``
    with ``structuredContent = None`` and converts it with the adapters' own
    converter, rather than asserting over a tuple we wrote ourselves.
    """

    async def test_it_reaches_the_presenter(self) -> None:
        wrapped, presenter = PresentFixture.wrap(
            result=PresentFixture.text_only_server_result("ISS-1 Fix login")
        )

        await wrapped.ainvoke({"tool_call_id": "call-1"})

        assert len(presenter.outcomes) == 1

    async def test_the_projected_output_carries_the_server_text(self) -> None:
        wrapped, presenter = PresentFixture.wrap(
            result=PresentFixture.text_only_server_result("ISS-1 Fix login")
        )

        await wrapped.ainvoke({"tool_call_id": "call-1"})

        blocks = presenter.outcomes[0].output[PresentValues.OUTPUT_KEY]
        assert [block["text"] for block in blocks] == ["ISS-1 Fix login"]


class TestOnlyAGenuinelyEmptyResultIsDropped:
    """``None`` means "there was nothing", never "there was nothing structured"."""

    async def test_a_result_with_both_halves_absent_is_not_presented(self) -> None:
        wrapped, presenter = PresentFixture.wrap(result=(None, None))

        await wrapped.ainvoke({"tool_call_id": "call-1"})

        assert presenter.outcomes == []

    async def test_an_empty_result_still_does_not_fail_the_call(self) -> None:
        wrapped, _ = PresentFixture.wrap(result=(None, None))

        assert await wrapped.ainvoke({"tool_call_id": "call-1"}) is None


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
