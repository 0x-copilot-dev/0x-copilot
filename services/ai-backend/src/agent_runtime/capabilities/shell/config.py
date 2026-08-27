"""Deployment-trusted configuration for ``run_command`` (PRD-shell-execution §4.4).

Everything the model is forbidden to influence — whether commands can run at
all, the timeout ceiling, the output cap, the per-run command budget, and which
shell binary is invoked — is resolved here, **once, from the process
environment**. The model never reaches this module, and there is deliberately no
per-request override for any of it: a request-supplied environment is a way to
smuggle a credential into a child process, and a request-supplied shell is a way
to reintroduce rc-file sourcing.

**This is not a second settings mechanism and must not be folded into
``RuntimeSettings``**, which is the *process* composition root — merged once at
boot and injected everywhere. A capability config gates whether one capability
exists at all, so it must resolve from a bare mapping in a test and its parse
failure must disable that capability rather than refuse to boot the service.
Twelve other ``from_env`` capability configs in ``agent_runtime`` resolve theirs
the same way (``sandbox``, ``interpreter``, ``desktop``, ``mcp.credentials``,
``surfaces``, ``api.connector_policy_client``, …): this is the house pattern,
not a fork of it — and unlike ``sandbox.config`` it keeps its readers inside the
class, per this service's "no module-level helper functions" rule.

Gating: the capability is OFF unless ``RUNTIME_ENABLE_SHELL_EXECUTION`` holds an
enabling token. The parse fails closed in both directions — an unrecognised
enable token leaves it disabled, and **any** malformed numeric override disables
the capability outright rather than silently falling back to a default.

The three numbers are the ones this repo already uses, so there is one house
answer rather than two: 120 s matches ``RemoteSandboxConfig.command_timeout_s``,
64 KiB matches ``combined_command_preview_bytes`` and the ``execute``
descriptor's ``max_inline_result_bytes``, and 64 commands matches PRD-FS-08's
per-session budget.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Final

from pydantic import Field, ValidationError, model_validator

from agent_runtime.capabilities.shell.contracts import (
    ShellRefusal,
    ShellRefusalReason,
    ShellRefusedError,
)
from agent_runtime.execution.contracts import RuntimeContract


class _EnvFields:
    """Environment variable names (single source of truth).

    There is deliberately no ``RUNTIME_SHELL_PATH``. ``shell_path`` is a
    constructor field so a test can point at a stub, but it is never derived
    from the environment: an env-settable shell is one export away from being
    the user's login shell, which is the exact thing §11.4 refuses.
    """

    ENABLE: Final = "RUNTIME_ENABLE_SHELL_EXECUTION"
    DEFAULT_TIMEOUT_S: Final = "RUNTIME_SHELL_DEFAULT_TIMEOUT_S"
    MAX_TIMEOUT_S: Final = "RUNTIME_SHELL_MAX_TIMEOUT_S"
    OUTPUT_PREVIEW_BYTES: Final = "RUNTIME_SHELL_OUTPUT_PREVIEW_BYTES"
    MAX_COMMANDS_PER_RUN: Final = "RUNTIME_SHELL_MAX_COMMANDS_PER_RUN"

    #: Model field → environment variable, for every numeric override. The
    #: defaults are deliberately absent: they live on the fields themselves, so
    #: ``from_env`` omits an unset key rather than re-stating its value.
    NUMERIC: Final = {
        "default_timeout_s": DEFAULT_TIMEOUT_S,
        "max_timeout_s": MAX_TIMEOUT_S,
        "combined_output_preview_bytes": OUTPUT_PREVIEW_BYTES,
        "max_commands_per_run": MAX_COMMANDS_PER_RUN,
    }

    #: Mirrors ``RemoteSandboxConfig``'s enabling tokens, so one spelling turns
    #: on either capability. Copied rather than imported: importing would make
    #: ``shell`` depend on ``sandbox`` for four string literals. The real fix is
    #: one shared token set for all 18 copies in this service (``settings.
    #: _BOOL_TRUTHY`` among them), which is a change to files this module does
    #: not own.
    TRUTHY: Final = frozenset({"1", "true", "yes", "on"})


class _Note:
    """Model-facing refusal sentences, authored rather than interpolated.

    They name the real constraint — §4.2's argument for refusing a too-large
    timeout instead of clamping it is precisely that the model learns the
    ceiling in one turn instead of concluding from a surprise timeout that the
    command is broken. The closed ``reason`` code, not this prose, is what the
    product contract is made of.
    """

    DISABLED: Final = (
        "Running commands is turned off for this workspace. Nothing was run."
    )
    BUDGET_EXHAUSTED: Final = (
        "This run has used its whole command allowance ({limit} commands). "
        "Nothing was run."
    )
    TIMEOUT_TOO_LARGE: Final = (
        "timeout_s={requested} is above the maximum of {maximum} seconds. "
        "Nothing was run - ask again with timeout_s at or below {maximum}, or "
        "run something shorter."
    )


class ShellExecutionConfig(RuntimeContract):
    """Resolved, deployment-trusted shell-execution configuration for one process.

    ``enabled=False`` means the capability is absent: no tool is registered, the
    model is told in its prompt that it has no shell, and nothing here is
    reachable.
    """

    enabled: bool = False
    default_timeout_s: int = Field(default=120, ge=1, le=15 * 60)
    max_timeout_s: int = Field(default=600, ge=1, le=15 * 60)
    combined_output_preview_bytes: int = Field(default=64 * 1024, ge=1, le=256 * 1024)
    max_commands_per_run: int = Field(default=64, ge=1, le=512)
    #: NOT the user's ``$SHELL``, and not a login shell. A login shell sources
    #: ``~/.zshrc``, which is arbitrary user code running with the child's
    #: environment and able to re-export everything the allowlist just removed.
    #: Consequence to state out loud in the tool description: aliases and shell
    #: functions are unavailable — ``ll`` will not work, ``ls -la`` will.
    shell_path: str = Field(default="/bin/sh", min_length=1)

    @model_validator(mode="after")
    def _check_ceiling_is_above_default(self) -> "ShellExecutionConfig":
        """A default above the ceiling would refuse every call that omits a timeout."""

        if self.default_timeout_s > self.max_timeout_s:
            raise ValueError(
                "default_timeout_s must not exceed max_timeout_s; "
                "a default above the ceiling refuses every unqualified command"
            )
        return self

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "ShellExecutionConfig":
        """Resolve config from the environment, failing closed on bad input.

        Two independent closes:

        * an enable value that is not one of :attr:`_EnvFields.TRUTHY` leaves
          ``enabled=False``;
        * any numeric override that is unparseable, out of range, or mutually
          inconsistent yields a **disabled** config rather than a partly-applied
          one. A deployment that meant to lower the ceiling and typoed the value
          gets no shell, not the default ceiling it never asked for.
        """

        source = environ if environ is not None else os.environ
        enabled = (source.get(_EnvFields.ENABLE) or "").strip().lower() in (
            _EnvFields.TRUTHY
        )
        if not enabled:
            return cls()
        try:
            overrides: dict[str, int] = {
                field: value
                for field, key in _EnvFields.NUMERIC.items()
                if (value := cls._read_int(source, key)) is not None
            }
            return cls(enabled=True, **overrides)
        except (ValueError, TypeError, ValidationError):
            # Fail closed: an unreadable limit is not a limit.
            return cls()

    @staticmethod
    def _read_int(source: Mapping[str, str], key: str) -> int | None:
        """Parse one integer override, or ``None`` when the variable is unset.

        Raising on anything malformed is what :meth:`from_env` converts into a
        disabled config. ``None`` for "unset" is deliberate: the field's own
        ``Field(default=...)`` then applies, so each of the four numbers is
        written in exactly one place instead of once here and once there.
        """

        raw = (source.get(key) or "").strip()
        return int(raw) if raw else None

    def resolve_timeout_s(self, requested: int | None) -> int:
        """Return the timeout for one call, refusing anything above the ceiling.

        Refused, never clamped (§4.2). A silent clamp means a model that asked
        for thirty minutes and got two concludes from the timeout that the
        command is broken and retries it; a refusal tells it the real constraint
        in one turn.
        """

        if requested is None:
            return self.default_timeout_s
        if requested > self.max_timeout_s:
            raise ShellRefusedError(
                ShellRefusal.refused(
                    ShellRefusalReason.TIMEOUT_ABOVE_MAXIMUM,
                    _Note.TIMEOUT_TOO_LARGE.format(
                        requested=requested, maximum=self.max_timeout_s
                    ),
                )
            )
        return requested

    def require_enabled(self) -> None:
        """Raise the typed refusal when commands are off for this deployment.

        A second check beside the tool being absent from the toolset: the tool
        is not registered when the capability is off, so this only fires if some
        future call path reaches the executor without going through
        registration. It fails closed rather than assuming registration is the
        only door.
        """

        if not self.enabled:
            raise ShellRefusedError(
                ShellRefusal.unavailable(
                    ShellRefusalReason.SHELL_EXECUTION_DISABLED, _Note.DISABLED
                )
            )


class ShellCommandBudget:
    """The per-run command allowance, as a counter that can only go one way.

    Run-scoped and in-memory: the budget bounds one run's spawns, so it is
    created beside the run and dies with it. Deliberately mutable — it is the
    one thing in this package that is not a frozen contract, because it is a
    consumable rather than a value.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._spent = 0

    @property
    def limit(self) -> int:
        """How many commands this run may spawn in total."""

        return self._limit

    @property
    def spent(self) -> int:
        """How many have been spawned so far."""

        return self._spent

    @property
    def remaining(self) -> int:
        """How many are left. Never negative."""

        return max(self._limit - self._spent, 0)

    def require_available(self) -> None:
        """Raise the typed refusal when the run has no allowance left.

        The read half of :meth:`consume`, so the caller can check before a human
        is shown a card and claim only after the decision — asking someone to
        approve a command the run has no allowance left to spawn is a card that
        cannot lead anywhere. Lives here rather than at the call site because
        ``spent >= limit`` is the counter's own business; a caller that had to
        spell it out would be a second place to get the comparison wrong.
        """

        if self._spent >= self._limit:
            raise ShellRefusedError(
                ShellRefusal.refused(
                    ShellRefusalReason.COMMAND_BUDGET_EXHAUSTED,
                    _Note.BUDGET_EXHAUSTED.format(limit=self._limit),
                )
            )

    def consume(self) -> None:
        """Claim one command, or raise the typed refusal when the run is out.

        Claimed *before* the spawn, so a command that hangs and is killed still
        counts. Counting completions instead would let a run that times out
        repeatedly spawn without bound.
        """

        self.require_available()
        self._spent += 1
