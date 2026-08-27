"""Configuration tests (PRD-shell-execution §4.4).

Two properties are being defended. First, the capability is **off** unless a
deployment says otherwise, and a misconfiguration cannot turn it on or quietly
change a ceiling. Second, the model cannot reach any of it: the only value a
request contributes is ``timeout_s``, and that is refused rather than clamped.
"""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.shell.config import (
    ShellCommandBudget,
    ShellExecutionConfig,
)
from agent_runtime.capabilities.shell.contracts import (
    ShellExecutionStatus,
    ShellRefusalReason,
    ShellRefusedError,
)

ENABLE = "RUNTIME_ENABLE_SHELL_EXECUTION"


class EnabledConfigMixin:
    """An enabled config, built without touching the process environment."""

    def enabled(self, **overrides: object) -> ShellExecutionConfig:
        payload: dict[str, object] = {"enabled": True}
        payload.update(overrides)
        return ShellExecutionConfig.model_validate(payload)


class TestFromEnv:
    def test_is_off_with_an_empty_environment(self) -> None:
        assert ShellExecutionConfig.from_env({}).enabled is False

    @pytest.mark.parametrize("token", ["1", "true", "TRUE", " yes ", "on"])
    def test_enables_on_a_recognised_token(self, token: str) -> None:
        assert ShellExecutionConfig.from_env({ENABLE: token}).enabled is True

    @pytest.mark.parametrize("token", ["", "0", "false", "maybe", "true-ish", "2"])
    def test_stays_off_for_anything_else(self, token: str) -> None:
        assert ShellExecutionConfig.from_env({ENABLE: token}).enabled is False

    def test_carries_the_house_defaults(self) -> None:
        """One house answer, not two: these are the sandbox lane's numbers."""

        config = ShellExecutionConfig.from_env({ENABLE: "1"})

        assert config.default_timeout_s == 120
        assert config.max_timeout_s == 600
        assert config.combined_output_preview_bytes == 64 * 1024
        assert config.max_commands_per_run == 64
        assert config.shell_path == "/bin/sh"

    def test_applies_deployment_overrides(self) -> None:
        config = ShellExecutionConfig.from_env(
            {
                ENABLE: "1",
                "RUNTIME_SHELL_DEFAULT_TIMEOUT_S": "30",
                "RUNTIME_SHELL_MAX_TIMEOUT_S": "60",
                "RUNTIME_SHELL_OUTPUT_PREVIEW_BYTES": "4096",
                "RUNTIME_SHELL_MAX_COMMANDS_PER_RUN": "8",
            }
        )

        assert (config.default_timeout_s, config.max_timeout_s) == (30, 60)
        assert config.combined_output_preview_bytes == 4096
        assert config.max_commands_per_run == 8

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("RUNTIME_SHELL_DEFAULT_TIMEOUT_S", "thirty"),
            ("RUNTIME_SHELL_MAX_TIMEOUT_S", "-1"),
            ("RUNTIME_SHELL_MAX_TIMEOUT_S", "99999"),
            ("RUNTIME_SHELL_OUTPUT_PREVIEW_BYTES", "0"),
            ("RUNTIME_SHELL_OUTPUT_PREVIEW_BYTES", "1e6"),
            ("RUNTIME_SHELL_MAX_COMMANDS_PER_RUN", "999"),
        ],
    )
    def test_a_malformed_override_disables_the_capability(
        self, key: str, value: str
    ) -> None:
        """Fail closed: an unreadable limit is not a limit.

        A deployment that meant to lower a ceiling and typoed the value gets no
        shell — not the default ceiling it never asked for.
        """

        config = ShellExecutionConfig.from_env({ENABLE: "1", key: value})

        assert config.enabled is False

    def test_a_default_above_the_ceiling_disables_the_capability(self) -> None:
        config = ShellExecutionConfig.from_env(
            {
                ENABLE: "1",
                "RUNTIME_SHELL_DEFAULT_TIMEOUT_S": "500",
                "RUNTIME_SHELL_MAX_TIMEOUT_S": "60",
            }
        )

        assert config.enabled is False

    def test_the_shell_path_is_not_env_derived(self) -> None:
        """§11.4: an env-settable shell is one export away from the login shell.

        Both spellings are supplied and both are ignored — the user's ``SHELL``
        and a plausible-looking ``RUNTIME_SHELL_PATH`` that this module
        deliberately does not read.
        """

        config = ShellExecutionConfig.from_env(
            {ENABLE: "1", "SHELL": "/bin/zsh", "RUNTIME_SHELL_PATH": "/bin/zsh"}
        )

        assert config.shell_path == "/bin/sh"

    def test_reads_os_environ_when_given_nothing(self, monkeypatch) -> None:
        monkeypatch.delenv(ENABLE, raising=False)

        assert ShellExecutionConfig.from_env().enabled is False


class TestResolveTimeout(EnabledConfigMixin):
    def test_defaults_when_the_model_omits_it(self) -> None:
        assert self.enabled().resolve_timeout_s(None) == 120

    def test_accepts_a_value_at_the_ceiling(self) -> None:
        assert self.enabled().resolve_timeout_s(600) == 600

    def test_refuses_rather_than_clamping_above_the_ceiling(self) -> None:
        """§4.2: a silent clamp teaches the model that the command is broken."""

        with pytest.raises(ShellRefusedError) as error:
            self.enabled().resolve_timeout_s(1_800)

        refusal = error.value.refusal
        assert refusal.reason is ShellRefusalReason.TIMEOUT_ABOVE_MAXIMUM
        assert refusal.status is ShellExecutionStatus.REFUSED

    def test_the_refusal_names_the_real_constraint(self) -> None:
        """The model must learn the ceiling in one turn, not by re-guessing."""

        with pytest.raises(ShellRefusedError) as error:
            self.enabled(default_timeout_s=30, max_timeout_s=90).resolve_timeout_s(
                1_800
            )

        assert "90" in error.value.refusal.note
        assert "1800" in error.value.refusal.note


class TestRequireEnabled(EnabledConfigMixin):
    def test_raises_an_unavailable_refusal_when_off(self) -> None:
        with pytest.raises(ShellRefusedError) as error:
            ShellExecutionConfig().require_enabled()

        assert error.value.refusal.status is ShellExecutionStatus.UNAVAILABLE
        assert error.value.refusal.reason is ShellRefusalReason.SHELL_EXECUTION_DISABLED

    def test_is_silent_when_enabled(self) -> None:
        assert self.enabled().require_enabled() is None


class TestShellCommandBudget:
    def test_counts_down_from_the_limit(self) -> None:
        budget = ShellCommandBudget(3)

        budget.consume()
        budget.consume()

        assert (budget.spent, budget.remaining, budget.limit) == (2, 1, 3)

    def test_refuses_once_the_run_is_out(self) -> None:
        budget = ShellCommandBudget(1)
        budget.consume()

        with pytest.raises(ShellRefusedError) as error:
            budget.consume()

        assert error.value.refusal.reason is ShellRefusalReason.COMMAND_BUDGET_EXHAUSTED
        assert "1" in error.value.refusal.note

    def test_a_refused_claim_does_not_advance_the_counter(self) -> None:
        budget = ShellCommandBudget(1)
        budget.consume()

        with pytest.raises(ShellRefusedError):
            budget.consume()

        assert budget.spent == 1
        assert budget.remaining == 0

    def test_counts_spawns_not_completions(self) -> None:
        """A run that times out repeatedly must still exhaust its allowance.

        Counting completions instead would let a hanging command be re-spawned
        without bound, which is the shape the budget exists to stop.
        """

        budget = ShellCommandBudget(2)
        budget.consume()
        budget.consume()

        with pytest.raises(ShellRefusedError):
            budget.consume()
