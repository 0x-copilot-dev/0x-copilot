"""Contract validation for AC6 code-mode types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.interpreter.contracts import (
    InterpreterError,
    InterpreterErrorCode,
    InterpreterLimitKind,
    InterpreterLimitProfiles,
    InterpreterLimits,
    RunCodeModeInput,
)


class TestRunCodeModeInput:
    def test_minimal_model_facing_input(self) -> None:
        model_input = RunCodeModeInput(code="1 + 1")
        assert model_input.inputs == {}
        assert model_input.external_functions == ()

    def test_model_cannot_set_limits_or_identity(self) -> None:
        # extra="forbid": the model surface has no limits / adapter / run_id.
        with pytest.raises(ValidationError):
            RunCodeModeInput(code="1", limits={"max_code_bytes": 10})  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            RunCodeModeInput(code="1", run_id="r1")  # type: ignore[call-arg]


class TestInterpreterLimits:
    def test_rejects_non_positive_limits(self) -> None:
        with pytest.raises(ValidationError):
            InterpreterLimits(
                max_code_bytes=0,
                segment_timeout_ms=1,
                total_timeout_ms=1,
                max_heap_bytes=1,
                max_allocations=1,
                max_recursion_depth=1,
                max_external_calls=1,
                max_snapshot_bytes=1,
                max_result_bytes=1,
                max_stdout_bytes=1,
                max_stderr_bytes=1,
            )


class TestLimitProfiles:
    def test_desktop_v1_matches_prd_defaults(self) -> None:
        profile = InterpreterLimitProfiles.resolve("desktop_v1")
        assert profile.max_code_bytes == 32 * 1024
        assert profile.max_external_calls == 32
        assert profile.total_timeout_ms == 10_000

    def test_unknown_profile_falls_back_not_unbounds(self) -> None:
        # A typo must never widen limits; it resolves to desktop_v1.
        assert InterpreterLimitProfiles.resolve("nope") == (
            InterpreterLimitProfiles.resolve("desktop_v1")
        )

    def test_profile_clamped_to_hard_ceiling(self) -> None:
        inflated = InterpreterLimits(
            max_code_bytes=10_000_000,
            segment_timeout_ms=999_999,
            total_timeout_ms=999_999,
            max_heap_bytes=10**12,
            max_allocations=10**12,
            max_recursion_depth=100_000,
            max_external_calls=100_000,
            max_snapshot_bytes=10**12,
            max_result_bytes=10**12,
            max_stdout_bytes=10**12,
            max_stderr_bytes=10**12,
        )
        clamped = InterpreterLimitProfiles._clamp(inflated)
        assert clamped.max_external_calls == 64  # hard ceiling
        assert clamped.max_code_bytes == 64 * 1024
        assert clamped.max_recursion_depth == 256


class TestInterpreterError:
    def test_as_failed_projects_code_and_message(self) -> None:
        err = InterpreterError(
            InterpreterErrorCode.RESOURCE_LIMIT_EXCEEDED,
            "over budget",
            limit_kind=InterpreterLimitKind.WALL_TIME,
        )
        failed = err.as_failed(stdout_preview="partial")
        assert failed.code is InterpreterErrorCode.RESOURCE_LIMIT_EXCEEDED
        assert failed.limit_kind is InterpreterLimitKind.WALL_TIME
        assert failed.safe_message == "over budget"
        assert failed.stdout_preview == "partial"
