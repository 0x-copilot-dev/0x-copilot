"""Contract tests for ``run_command`` (PRD-shell-execution §4.2, §4.3).

The theme: a shape that cannot be half-true. Every assertion here is about a row
that would otherwise be able to lie — an approval card showing a command that is
not the command that runs, a result whose status and exit code disagree, a
refusal that quietly carries process output.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.shell.contracts import (
    RunCommandInput,
    RunCommandResult,
    ShellCommandCancelled,
    ShellExecutionOutcome,
    ShellExecutionRequest,
    ShellExecutionStatus,
    ShellRefusal,
    ShellRefusalReason,
    ShellRefusedError,
)


class RunCommandInputMixin:
    """Shared construction for the model-facing input."""

    def build_input(self, **overrides: object) -> RunCommandInput:
        payload: dict[str, object] = {"command": "pytest -q"}
        payload.update(overrides)
        return RunCommandInput.model_validate(payload)


class ResultMixin:
    """Shared construction for the model-facing result."""

    def completed(self, **overrides: object) -> RunCommandResult:
        payload: dict[str, object] = {
            "status": ShellExecutionStatus.COMPLETED,
            "exit_code": 0,
            "output": "ok\n",
            "duration_ms": 12,
            "workspace": "project",
        }
        payload.update(overrides)
        return RunCommandResult.model_validate(payload)


class TestShellContract:
    """The base class every shape in this package inherits."""

    def test_hides_the_input_value_from_validation_errors(self) -> None:
        """A command is untrusted text; a traceback is not an audited log call.

        Pydantic renders ``input_value=...`` into ``str(ValidationError)`` by
        default, so without ``hide_input_in_errors`` a malformed command would
        reach any handler that stringifies the error. Discovered by this test
        failing against the message-only version of the assertion.
        """

        with pytest.raises(ValidationError) as error:
            RunCommandInput.model_validate({"command": "secret-marker-\x00-here"})

        rendered = str(error.value)
        assert "secret-marker-" not in rendered
        assert "control character" in rendered

    def test_still_forbids_extras_freezes_and_validates_assignment(self) -> None:
        """The subclass config must MERGE with ``RuntimeContract``, not replace it."""

        config = RunCommandInput.model_config

        assert config["extra"] == "forbid"
        assert config["frozen"] is True
        assert config["validate_assignment"] is True
        assert config["hide_input_in_errors"] is True


class TestRunCommandInput(RunCommandInputMixin):
    def test_parses_the_minimal_call(self) -> None:
        parsed = self.build_input()

        assert parsed.command == "pytest -q"
        assert parsed.workspace is None
        assert parsed.timeout_s is None

    def test_rejects_an_invented_env_argument(self) -> None:
        """``extra="forbid"`` is the reason the card cannot omit an argument.

        With ``extra="ignore"`` this payload would run its command and drop the
        ``env`` silently — and the approval card, built from the validated
        arguments, would never show the field the human approved.
        """

        with pytest.raises(ValidationError) as error:
            RunCommandInput.model_validate(
                {"command": "printenv", "env": {"AWS_PROFILE": "prod"}}
            )

        assert "env" in str(error.value)

    def test_rejects_an_invented_cwd_argument(self) -> None:
        with pytest.raises(ValidationError):
            RunCommandInput.model_validate({"command": "ls", "cwd": "/etc"})

    def test_rejects_a_nul_that_would_truncate_at_exec(self) -> None:
        """AC: the card and the process can never disagree (§16.3)."""

        with pytest.raises(ValidationError) as error:
            self.build_input(command="echo ok\x00; rm -rf ~")

        assert "control character" in str(error.value)

    def test_rejects_other_c0_control_characters(self) -> None:
        for control in ("\x1b", "\x07", "\x0c", "\x1f"):
            with pytest.raises(ValidationError):
                self.build_input(command=f"echo {control}hidden")

    def test_allows_tab_newline_and_carriage_return(self) -> None:
        parsed = self.build_input(command="echo a\n\techo b\r\n")

        assert "\n" in parsed.command and "\t" in parsed.command

    def test_the_message_itself_names_no_value(self) -> None:
        """The authored message is value-free even before Pydantic hides the input."""

        with pytest.raises(ValidationError) as error:
            self.build_input(command="secret-\x00-marker")

        assert error.value.errors()[0]["msg"].endswith(
            "only tab, newline and carriage return are permitted"
        )

    def test_rejects_a_whitespace_only_command(self) -> None:
        with pytest.raises(ValidationError):
            self.build_input(command="   \n\t ")

    def test_rejects_a_command_past_the_length_ceiling(self) -> None:
        with pytest.raises(ValidationError):
            self.build_input(command="x" * 8193)

    def test_accepts_a_command_at_the_length_ceiling(self) -> None:
        assert len(self.build_input(command="x" * 8192).command) == 8192

    def test_rejects_a_zero_or_negative_timeout(self) -> None:
        for value in (0, -1):
            with pytest.raises(ValidationError):
                self.build_input(timeout_s=value)

    def test_does_not_clamp_a_large_timeout_at_the_schema(self) -> None:
        """The ceiling is config, not schema: refusal happens where the cap is known."""

        assert self.build_input(timeout_s=99_999).timeout_s == 99_999

    def test_rejects_a_control_character_in_the_workspace_label(self) -> None:
        with pytest.raises(ValidationError):
            self.build_input(workspace="proj\x00ect")

    def test_rejects_a_blank_workspace_label(self) -> None:
        with pytest.raises(ValidationError):
            self.build_input(workspace="   ")

    def test_is_frozen(self) -> None:
        parsed = self.build_input()

        with pytest.raises(ValidationError):
            parsed.command = "rm -rf /"  # type: ignore[misc]


class TestRunCommandResult(ResultMixin):
    def test_carries_the_exit_code_as_an_integer(self) -> None:
        """AC1.3: the model learns failure from a field, not from prose."""

        result = self.completed(exit_code=1, output="2 failed\n")

        assert result.exit_code == 1
        assert result.status is ShellExecutionStatus.COMPLETED

    def test_a_completed_result_must_have_an_exit_code(self) -> None:
        with pytest.raises(ValidationError):
            self.completed(exit_code=None)

    @pytest.mark.parametrize(
        "status",
        [
            ShellExecutionStatus.TIMEOUT,
            ShellExecutionStatus.CANCELLED,
        ],
    )
    def test_a_non_completed_status_reports_no_exit_code(
        self, status: ShellExecutionStatus
    ) -> None:
        """AC5.4: a timeout is ``exit_code: null``, never the kill signal's code."""

        with pytest.raises(ValidationError):
            self.completed(status=status, exit_code=-9)

        allowed = self.completed(status=status, exit_code=None)
        assert allowed.exit_code is None

    def test_a_refusal_must_carry_a_reason(self) -> None:
        with pytest.raises(ValidationError):
            RunCommandResult.model_validate(
                {"status": ShellExecutionStatus.REFUSED, "reason": None}
            )

    def test_a_completed_result_may_not_carry_a_reason(self) -> None:
        with pytest.raises(ValidationError):
            self.completed(reason=ShellRefusalReason.COMMAND_NOT_PERMITTED)

    def test_a_refusal_carries_no_output(self) -> None:
        """Nothing ran, so there is nothing for ``output`` to hold."""

        with pytest.raises(ValidationError):
            RunCommandResult.model_validate(
                {
                    "status": ShellExecutionStatus.REFUSED,
                    "reason": ShellRefusalReason.COMMAND_NOT_PERMITTED,
                    "output": "whatever",
                }
            )

    def test_a_spawned_result_must_name_its_workspace(self) -> None:
        with pytest.raises(ValidationError):
            self.completed(workspace="")

    def test_a_truncated_result_must_report_the_true_total(self) -> None:
        with pytest.raises(ValidationError):
            self.completed(truncated=True, output_total_bytes=None)

    def test_total_bytes_without_truncation_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self.completed(truncated=False, output_total_bytes=10)

    def test_an_output_ref_requires_a_truncation(self) -> None:
        with pytest.raises(ValidationError):
            self.completed(truncated=False, output_ref="tool-results/out.txt")

    def test_output_ref_rejects_a_windows_host_path(self) -> None:
        with pytest.raises(ValidationError):
            self.completed(
                truncated=True,
                output_total_bytes=99,
                output_ref="C:/Users/me/AppData/out.txt",
            )

    def test_output_ref_rejects_a_unc_path(self) -> None:
        with pytest.raises(ValidationError):
            self.completed(
                truncated=True, output_total_bytes=99, output_ref="//host/share/out.txt"
            )

    def test_serialises_status_and_reason_as_plain_strings(self) -> None:
        """The result reaches the model as JSON; the enum must not leak its repr."""

        dumped = RunCommandResult(
            status=ShellExecutionStatus.REFUSED,
            reason=ShellRefusalReason.COMMAND_NOT_PERMITTED,
            exit_note="no",
        ).model_dump(mode="json")

        assert dumped["status"] == "refused"
        assert dumped["reason"] == "command_not_permitted"


class TestShellRefusal:
    def test_refused_and_unavailable_are_the_only_statuses(self) -> None:
        for status in (
            ShellExecutionStatus.COMPLETED,
            ShellExecutionStatus.TIMEOUT,
            ShellExecutionStatus.CANCELLED,
        ):
            with pytest.raises(ValidationError):
                ShellRefusal(
                    status=status,
                    reason=ShellRefusalReason.COMMAND_NOT_PERMITTED,
                    note="no",
                )

    def test_projects_into_a_result_with_no_output_and_no_workspace(self) -> None:
        refusal = ShellRefusal.refused(
            ShellRefusalReason.COMMAND_NOT_PERMITTED, "That command cannot be run."
        )

        result = refusal.as_result(duration_ms=3)

        assert result.status is ShellExecutionStatus.REFUSED
        assert result.reason is ShellRefusalReason.COMMAND_NOT_PERMITTED
        assert result.exit_note == "That command cannot be run."
        assert result.output == ""
        assert result.workspace == ""
        assert result.exit_code is None

    def test_unavailable_projects_with_its_own_status(self) -> None:
        result = ShellRefusal.unavailable(
            ShellRefusalReason.SHELL_EXECUTION_DISABLED, "off"
        ).as_result()

        assert result.status is ShellExecutionStatus.UNAVAILABLE

    def test_the_typed_error_carries_the_refusal(self) -> None:
        refusal = ShellRefusal.refused(
            ShellRefusalReason.TIMEOUT_ABOVE_MAXIMUM, "too long"
        )

        error = ShellRefusedError(refusal)

        assert error.refusal is refusal
        assert str(error) == "too long"


class TestShellExecutionRequest:
    def build(self, **overrides: object) -> ShellExecutionRequest:
        payload: dict[str, object] = {
            "command": "true",
            "cwd": Path("/tmp"),
            "timeout_s": 5,
            "env": {},
            "shell_path": "/bin/sh",
            "output_cap_bytes": 1024,
        }
        payload.update(overrides)
        return ShellExecutionRequest.model_validate(payload)

    def test_requires_an_absolute_working_directory(self) -> None:
        """A relative cwd would resolve against the worker's own directory."""

        with pytest.raises(ValidationError):
            self.build(cwd=Path("relative/path"))

    def test_keeps_the_environment_out_of_its_repr(self) -> None:
        """Hygiene: an exception rendering the request must not print the env."""

        request = self.build(env={"PATH": "/usr/bin", "LANG": "C.UTF-8"})

        assert "/usr/bin" not in repr(request)

    def test_defaults_to_no_spill_file(self) -> None:
        assert self.build().spill_path is None

    def test_rejects_an_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            self.build(shell=True)


class TestShellExecutionOutcome:
    def test_rejects_a_status_that_never_spawned(self) -> None:
        for status in (
            ShellExecutionStatus.REFUSED,
            ShellExecutionStatus.UNAVAILABLE,
        ):
            with pytest.raises(ValidationError):
                ShellExecutionOutcome(status=status)

    def test_cancelled_carries_its_partial_outcome_on_the_exception(self) -> None:
        """AC5.2: cancellation preserves output rather than returning an empty string."""

        outcome = ShellExecutionOutcome(
            status=ShellExecutionStatus.CANCELLED, output="half a build log"
        )

        error = ShellCommandCancelled(outcome)

        assert error.outcome.output == "half a build log"
        assert error.outcome.exit_code is None
