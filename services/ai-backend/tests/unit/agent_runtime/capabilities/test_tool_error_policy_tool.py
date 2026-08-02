"""Tests for :class:`ToolErrorPolicyTool` and :class:`ToolErrorPolicyRegistry`."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from pydantic import ConfigDict

from agent_runtime.capabilities.mcp.middleware.compose import (
    ToolResultShape,
    ToolSchemaIdentity,
)
from agent_runtime.capabilities.tool_error_policy_tool import (
    ToolErrorPolicyRegistry,
    ToolErrorPolicyTool,
)
from agent_runtime.execution.tool_errors import (
    BudgetExceeded,
    RunFatalToolError,
)


class _ExplosiveTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str = "boom"
    description: str = "explodes"
    exc_factory: Any = None
    sync_result: str = "ok"
    async_result: str = "ok"

    def _run(self, *_: Any, **__: Any) -> Any:
        if self.exc_factory is not None:
            raise self.exc_factory("boom!")
        return self.sync_result

    async def _arun(self, *_: Any, **__: Any) -> Any:
        if self.exc_factory is not None:
            raise self.exc_factory("boom!")
        return self.async_result


class _ContentAndArtifactTool(BaseTool):
    """Inner shaped like the built-in ``web_search`` and every MCP tool.

    Neither half of the pair is a string, which is the shape that made the
    dropped ``response_format`` invisible to string-based assertions.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "web_search"
    description: str = "declares content_and_artifact"
    response_format: str = "content_and_artifact"
    return_direct: bool = True
    tags: list[str] | None = ["builtin"]
    metadata: dict[str, Any] | None = {"origin": "builtin"}
    extras: dict[str, Any] | None = {"cache_control": {"type": "ephemeral"}}
    exc_factory: Any = None
    content: list[dict[str, str]] = []
    artifact: list[dict[str, str]] = []

    def _run(self, *_: Any, **__: Any) -> Any:
        if self.exc_factory is not None:
            raise self.exc_factory("boom!")
        return self.content, self.artifact

    async def _arun(self, *_: Any, **__: Any) -> Any:
        if self.exc_factory is not None:
            raise self.exc_factory("boom!")
        return self.content, self.artifact


def _wrap(inner: _ExplosiveTool) -> ToolErrorPolicyTool:
    return ToolErrorPolicyTool(
        name=inner.name,
        description=inner.description,
        inner=inner,
    )


class TestArunPolicy:
    async def test_run_fatal_propagates(self) -> None:
        wrapped = _wrap(_ExplosiveTool(exc_factory=BudgetExceeded))
        with pytest.raises(RunFatalToolError):
            await wrapped._arun()

    async def test_plain_exception_becomes_tool_output_string(self) -> None:
        wrapped = _wrap(_ExplosiveTool(exc_factory=ValueError))
        result = await wrapped._arun()
        assert isinstance(result, str)
        assert "ValueError" in result
        assert "boom!" in result

    async def test_cancelled_error_is_re_raised_not_classified(self) -> None:
        wrapped = _wrap(_ExplosiveTool(exc_factory=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await wrapped._arun()

    async def test_keyboard_interrupt_is_re_raised(self) -> None:
        wrapped = _wrap(_ExplosiveTool(exc_factory=KeyboardInterrupt))
        with pytest.raises(KeyboardInterrupt):
            await wrapped._arun()

    async def test_success_path_returns_inner_value_unchanged(self) -> None:
        wrapped = _wrap(_ExplosiveTool(exc_factory=None, async_result="hello"))
        assert await wrapped._arun() == "hello"


class TestRunSyncPolicy:
    def test_plain_exception_becomes_tool_output_string(self) -> None:
        wrapped = _wrap(_ExplosiveTool(exc_factory=ValueError))
        result = wrapped._run()
        assert isinstance(result, str)
        assert "ValueError" in result

    def test_run_fatal_propagates(self) -> None:
        wrapped = _wrap(_ExplosiveTool(exc_factory=BudgetExceeded))
        with pytest.raises(RunFatalToolError):
            wrapped._run()


class TestRegistry:
    def test_registry_wraps_basetools_only(self) -> None:
        class _InnerRegistry:
            def list_available_tools(self, _ctx: object) -> tuple[object, ...]:
                return (_ExplosiveTool(), object())  # second one is not a BaseTool

        wrapped_registry = ToolErrorPolicyRegistry(inner=_InnerRegistry())
        tools = wrapped_registry.list_available_tools(None)
        assert isinstance(tools[0], ToolErrorPolicyTool)
        assert not isinstance(tools[1], BaseTool)

    def test_registry_does_not_double_wrap(self) -> None:
        already = _wrap(_ExplosiveTool())

        class _InnerRegistry:
            def list_available_tools(self, _ctx: object) -> tuple[object, ...]:
                return (already,)

        wrapped_registry = ToolErrorPolicyRegistry(inner=_InnerRegistry())
        tools = wrapped_registry.list_available_tools(None)
        assert tools[0] is already


class DispatchSurfaceMixin:
    """Builders and constants for the outermost-wrapper dispatch regression."""

    CONTENT: list[dict[str, str]] = [{"title": "T", "link": "https://example.test/a"}]
    ARTIFACT: list[dict[str, str]] = [{"raw": "payload", "secret": "artifact-only"}]
    TOOL_CALL_ID = "call_error_policy_1"

    def _inner(self, *, exc_factory: Any = None) -> _ContentAndArtifactTool:
        return _ContentAndArtifactTool(
            content=self.CONTENT,
            artifact=self.ARTIFACT,
            exc_factory=exc_factory,
        )

    def _wrap_through_registry(self, tool: BaseTool) -> ToolErrorPolicyTool:
        class _InnerRegistry:
            def list_available_tools(self, _ctx: object) -> tuple[object, ...]:
                return (tool,)

        rendered = ToolErrorPolicyRegistry(inner=_InnerRegistry()).list_available_tools(
            None
        )
        return rendered[0]  # type: ignore[return-value]

    def _tool_call(self) -> dict[str, Any]:
        return {
            "name": "web_search",
            "args": {},
            "id": self.TOOL_CALL_ID,
            "type": "tool_call",
        }


class TestRegistryDispatchSurfacePropagation(DispatchSurfaceMixin):
    def test_wraps_with_every_identity_field(self) -> None:
        inner = self._inner()

        wrapped = self._wrap_through_registry(inner)

        for field in (
            *ToolSchemaIdentity.MODEL_SURFACE,
            *ToolSchemaIdentity.DISPATCH_SURFACE,
        ):
            assert getattr(wrapped, field) == getattr(inner, field), field

    def test_carries_the_inner_response_format(self) -> None:
        # The live defect: this wrapper is the OUTERMOST layer of the built-in
        # chain, and LangChain reads response_format off the tool it dispatches.
        wrapped = self._wrap_through_registry(self._inner())

        assert wrapped.response_format == ToolResultShape.CONTENT_AND_ARTIFACT

    def test_copies_rather_than_shares_annotations(self) -> None:
        inner = self._inner()

        wrapped = self._wrap_through_registry(inner)
        wrapped.metadata["origin"] = "mutated"  # type: ignore[index]
        wrapped.tags.append("mutated")  # type: ignore[union-attr]
        wrapped.extras["cache_control"] = "mutated"  # type: ignore[index]

        assert inner.metadata == {"origin": "builtin"}
        assert inner.tags == ["builtin"]
        assert inner.extras == {"cache_control": {"type": "ephemeral"}}

    async def test_artifact_reaches_the_tool_message(self) -> None:
        wrapped = self._wrap_through_registry(self._inner())

        message = await wrapped.ainvoke(self._tool_call())

        assert isinstance(message, ToolMessage)
        assert message.artifact == self.ARTIFACT
        assert "artifact-only" not in message.content


class TestSurfacedErrorHonorsTheDeclaredShape(DispatchSurfaceMixin):
    """The trap that comes with propagating ``response_format``.

    The wrapper authors its own return value on the surfaced-error path, and
    ``BaseTool.arun`` unpacks exactly two values from a ``content_and_artifact``
    return. A bare string there would make the error handler raise on the very
    call it was rescuing.
    """

    async def test_async_surfaced_error_is_rendered_as_a_pair(self) -> None:
        wrapped = self._wrap_through_registry(self._inner(exc_factory=ValueError))

        result = await wrapped._arun()

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert "ValueError" in result[0]
        # No raw tool output was produced, so nothing may be invented for it.
        assert result[1] is None

    def test_sync_surfaced_error_is_rendered_as_a_pair(self) -> None:
        wrapped = self._wrap_through_registry(self._inner(exc_factory=ValueError))

        result = wrapped._run()

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert "ValueError" in result[0]
        assert result[1] is None

    async def test_surfaced_error_dispatches_to_a_tool_message(self) -> None:
        wrapped = self._wrap_through_registry(self._inner(exc_factory=ValueError))

        message = await wrapped.ainvoke(self._tool_call())

        assert isinstance(message, ToolMessage)
        assert "ValueError" in message.content
        assert "boom!" in message.content
        assert message.artifact is None

    async def test_plain_content_tool_still_surfaces_a_bare_string(self) -> None:
        # A tool that never declared the tuple format must be unaffected.
        wrapped = _wrap(_ExplosiveTool(exc_factory=ValueError))

        result = await wrapped._arun()

        assert isinstance(result, str)

    async def test_fatal_classification_still_propagates_under_the_pair_format(
        self,
    ) -> None:
        wrapped = self._wrap_through_registry(self._inner(exc_factory=BudgetExceeded))

        with pytest.raises(RunFatalToolError):
            await wrapped._arun()
