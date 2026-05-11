"""Tests for :class:`DefaultToolErrorPolicy`."""

from __future__ import annotations


import pytest
from langchain_core.tools import BaseTool

from agent_runtime.execution.tool_error_policy import (
    DefaultToolErrorPolicy,
    ToolErrorOutcome,
)
from agent_runtime.execution.tool_errors import (
    AuthDenied,
    BudgetExceeded,
    PolicyViolation,
    RunFatalToolError,
    TenantIsolationViolation,
)


class _FakeTool(BaseTool):
    name: str = "fake"
    description: str = "test"

    def _run(self) -> None:  # type: ignore[override]
        return None


def _tool() -> BaseTool:
    return _FakeTool()


class TestDefaultPolicyRouting:
    def test_run_fatal_subclass_routes_to_fail_run(self) -> None:
        exc = BudgetExceeded("over per-tool cap")
        c = DefaultToolErrorPolicy().classify(exc, tool=_tool())
        assert c.outcome is ToolErrorOutcome.FAIL_RUN
        assert c.error_class == "BudgetExceeded"
        assert c.sanitized_message == "over per-tool cap"

    @pytest.mark.parametrize(
        "exc_class",
        [BudgetExceeded, AuthDenied, PolicyViolation, TenantIsolationViolation],
    )
    def test_each_typed_subclass_routes_to_fail_run(
        self, exc_class: type[RunFatalToolError]
    ) -> None:
        c = DefaultToolErrorPolicy().classify(exc_class("nope"), tool=_tool())
        assert c.outcome is ToolErrorOutcome.FAIL_RUN

    def test_plain_exception_routes_to_surface_to_llm(self) -> None:
        c = DefaultToolErrorPolicy().classify(
            ValueError("limit must be 1-100"), tool=_tool()
        )
        assert c.outcome is ToolErrorOutcome.SURFACE_TO_LLM
        assert c.error_class == "ValueError"
        assert "limit must be 1-100" in c.sanitized_message

    def test_surfaced_message_includes_hints_when_present(self) -> None:
        from pydantic import BaseModel, ValidationError

        class _Model(BaseModel):
            limit: int

        with pytest.raises(ValidationError) as caught:
            _Model.model_validate({"limit": "bad"})
        c = DefaultToolErrorPolicy().classify(caught.value, tool=_tool())
        content = c.to_llm_message_content()
        assert "ValidationError" in content
        assert "Hints:" in content
        assert "limit" in content

    def test_surfaced_message_omits_hints_when_empty(self) -> None:
        c = DefaultToolErrorPolicy().classify(ValueError("plain"), tool=_tool())
        content = c.to_llm_message_content()
        assert "Hints:" not in content

    def test_audit_trace_is_captured(self) -> None:
        c = DefaultToolErrorPolicy().classify(ValueError("x"), tool=_tool())
        # No backtrace from a freshly-constructed ValueError, but the
        # field is present and at minimum contains the class line.
        assert c.audit_trace is not None
        assert "ValueError" in c.audit_trace


class TestSurfaceMessageSanitization:
    def test_sanitized_message_strips_paths(self) -> None:
        c = DefaultToolErrorPolicy().classify(
            RuntimeError("/Users/dev/secret.py error"), tool=_tool()
        )
        assert "/Users/" not in c.sanitized_message
        assert "[redacted]" in c.sanitized_message

    def test_sanitized_message_strips_run_id(self) -> None:
        c = DefaultToolErrorPolicy().classify(
            RuntimeError("run 8475dbace42f4e34a2d2fb1555a542e0 dead"), tool=_tool()
        )
        assert "8475dbace42f4e34a2d2fb1555a542e0" not in c.sanitized_message
