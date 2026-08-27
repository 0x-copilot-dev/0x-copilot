"""Typed contracts for the ``run_command`` capability (PRD-shell-execution §4.1-§4.3).

This module is the single source of truth for what the model may say and what it
is told back. Every value that crosses the tool boundary is a frozen
``RuntimeContract``, so model output is coerced and validated at the edge rather
than trusted.

Three properties are enforced structurally here rather than by convention:

* **The model cannot name a path, an environment variable, or a shell.**
  :class:`RunCommandInput` has exactly three fields and ``extra="forbid"``. A
  model that emits ``env=`` or ``cwd=`` fails at ``model_validate`` — it does not
  get its command run with the extra field silently dropped, which would put an
  argument in front of the human that the approval card never rendered (§4.2).
* **The card and the process can never disagree.** A NUL truncates the string at
  the exec boundary while the card renders the whole of it, so the C0 range
  (other than tab/newline/carriage-return) is refused at validation, above every
  approval control.
* **Failure is a field, not prose.** ``exit_code`` is an integer on the result,
  so a model never has to infer failure from English (§4.3, AC1.3).

Two shapes are ours rather than the PRD's, and both are called out in the class
docstrings: :class:`ShellExecutionRequest` / :class:`ShellExecutionOutcome` are
the *executor's* IO, one layer below :class:`RunCommandResult`, and they exist so
the executor never has to know a workspace label, a scratch layout, or a policy
decision.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Final

from pydantic import ConfigDict, Field, field_validator, model_validator

from agent_runtime.execution.contracts import RuntimeContract


class _Text:
    """Character-class constants. Kept off the Pydantic models on purpose.

    An underscore-prefixed class attribute on a ``BaseModel`` is claimed by
    Pydantic as a private attribute, so shared constants live on a plain class
    (the same reason ``settings.py`` keeps ``_EXECUTION`` at module level).
    """

    #: The only C0 control characters a command may contain. Everything else in
    #: ``0x00``-``0x1F`` is refused: NUL truncates at the exec boundary, and the
    #: rest are invisible in the approval card.
    ALLOWED_CONTROL: Final = frozenset("\t\n\r")

    #: Exclusive upper bound of the C0 range.
    C0_CEILING: Final = " "

    MAX_COMMAND_CHARS: Final = 8192
    MAX_LABEL_CHARS: Final = 128


class _Message:
    """Safe, value-free validation messages.

    None of these quote the offending value. A command is model output and may
    carry content ingested from a connector; a validation error is one of the
    places that content would otherwise reach a log line.
    """

    CONTROL_CHARACTERS: Final = (
        "command contains a control character; only tab, newline and carriage "
        "return are permitted"
    )
    BLANK_COMMAND: Final = "command must contain something other than whitespace"
    LABEL_CONTROL_CHARACTERS: Final = "workspace label contains a control character"
    BLANK_LABEL: Final = "workspace label must not be blank"
    EXIT_CODE_ON_NON_COMPLETED: Final = (
        "exit_code is set only for a completed command; every other status "
        "reports exit_code=None"
    )
    MISSING_EXIT_CODE: Final = "a completed command must report an exit_code"
    REASON_REQUIRED: Final = "refused and unavailable results must carry a reason"
    REASON_FORBIDDEN: Final = "reason is set only for refused or unavailable results"
    OUTPUT_ON_REFUSAL: Final = (
        "a refused or unavailable call ran no process and therefore has no output"
    )
    TOTAL_BYTES_REQUIRES_TRUNCATION: Final = (
        "output_total_bytes and output_ref describe a truncation and are set "
        "only when truncated is true"
    )
    TRUNCATION_REQUIRES_TOTAL: Final = (
        "a truncated result must report output_total_bytes"
    )
    WORKSPACE_REQUIRED: Final = (
        "a command that reached a process must name the workspace it ran in"
    )
    REFUSAL_STATUS: Final = (
        "a refusal describes a call that never spawned; its status must be "
        "refused or unavailable"
    )
    OUTCOME_STATUS: Final = (
        "an execution outcome describes a call that spawned; its status must be "
        "completed, timeout or cancelled"
    )
    OUTPUT_REF_NOT_VIRTUAL: Final = (
        "output_ref must be a virtual agent-scratch path, never a host path"
    )
    CWD_NOT_ABSOLUTE: Final = "cwd must be an absolute directory path"


class ShellContract(RuntimeContract):
    """``RuntimeContract`` plus ``hide_input_in_errors``, for one reason.

    Every model in this package holds text nobody in this repository wrote: a
    command is model output that may have been shaped by content ingested from a
    connector, and command output is whatever the process printed. Pydantic
    renders ``input_value=...`` into ``str(ValidationError)``, so without this a
    validation failure is a path by which that text reaches a log line — the one
    place §13's "length-clipped in logs, never value-scanned" precedent would be
    silently violated, because a traceback is not a log call anybody audits.

    ``model_config`` is merged across the MRO, so ``extra="forbid"``,
    ``frozen=True`` and ``validate_assignment=True`` are all still in force;
    a test pins that rather than trusting the merge.
    """

    model_config = ConfigDict(hide_input_in_errors=True)


class ShellExecutionStatus(StrEnum):
    """How one ``run_command`` call ended.

    Wire values are exactly the PRD's ``Literal`` set; the enum is the house form
    (``CLAUDE.md``: "use enums, literals, constrained strings") and serialises
    identically.
    """

    #: The process ran to completion and reported an exit status. This says
    #: nothing about success — a non-zero ``exit_code`` is still ``completed``.
    COMPLETED = "completed"
    #: The process outlived its timeout and its process group was killed.
    TIMEOUT = "timeout"
    #: The run was cancelled while the process was live; the group was killed.
    CANCELLED = "cancelled"
    #: Nothing was spawned, and nothing could have been: a floor decision.
    REFUSED = "refused"
    #: Nothing was spawned because the capability was not available for this
    #: call (disabled, no writable workspace, grant detached, spawn failed).
    UNAVAILABLE = "unavailable"


class ShellStatusGroups:
    """The two halves of :class:`ShellExecutionStatus`, by whether a process ran.

    A plain class rather than members on the enum: an annotated assignment in an
    ``Enum`` body is still an assignment, so a ``frozenset`` there would be
    coerced into an enum member and a ``StrEnum`` would raise at import.
    """

    #: Statuses that reached a real process and therefore carry a workspace
    #: label and (possibly partial) output.
    SPAWNED: Final = frozenset(
        {
            ShellExecutionStatus.COMPLETED,
            ShellExecutionStatus.TIMEOUT,
            ShellExecutionStatus.CANCELLED,
        }
    )

    #: Statuses that never reached a process and therefore carry a ``reason``.
    NOT_SPAWNED: Final = frozenset(
        {ShellExecutionStatus.REFUSED, ShellExecutionStatus.UNAVAILABLE}
    )


class ShellRefusalReason(StrEnum):
    """Closed vocabulary for why a call produced no process.

    Closed on purpose (§4.3): a free-form reason is a way for deployment
    configuration to leak into model-visible output. The prose that *does* reach
    the model is :attr:`ShellRefusal.note`, which is authored here, never
    interpolated from an exception.
    """

    #: ``RUNTIME_ENABLE_SHELL_EXECUTION`` is not set for this deployment.
    SHELL_EXECUTION_DISABLED = "shell_execution_disabled"
    #: No attached folder is writable, so there is nothing to bind a cwd to.
    NO_WRITABLE_WORKSPACE = "no_writable_workspace"
    #: The ``workspace`` label is not one of the bound labels. Never falls back
    #: to a default root (§4.2).
    UNKNOWN_WORKSPACE = "unknown_workspace"
    #: The grant went away between run start and this call (§16.1).
    WORKSPACE_UNAVAILABLE = "workspace_unavailable"
    #: ``timeout_s`` exceeded ``max_timeout_s``. Refused, never clamped (§4.2).
    TIMEOUT_ABOVE_MAXIMUM = "timeout_above_maximum"
    #: The command is on the never-list. Unappealable: no card is created (§9.3).
    COMMAND_NOT_PERMITTED = "command_not_permitted"
    #: The run used its whole ``max_commands_per_run`` allowance (§4.4).
    COMMAND_BUDGET_EXHAUSTED = "command_budget_exhausted"
    #: The process could not be started at all (missing shell, unusable cwd).
    EXECUTION_UNAVAILABLE = "execution_unavailable"
    #: A human was shown the command and declined it (§8). Distinct from
    #: :attr:`COMMAND_NOT_PERMITTED` on purpose: that one is a floor and is
    #: unappealable, this one is a person's answer to this call and says nothing
    #: about the next one. Collapsing the two would tell a model to retry an
    #: unappealable refusal, or to give up after a decline the user might
    #: reverse — the exact failure a coarsened error taxonomy produces.
    COMMAND_DECLINED = "command_declined"
    #: The command needed approval in a run with no approval channel wired, so
    #: the GATE failed closed. A configuration fact, not a policy judgement, and
    #: permanent for this run.
    COMMAND_APPROVAL_UNAVAILABLE = "command_approval_unavailable"


class RunCommandInput(ShellContract):
    """Model-facing schema. Command text and an opaque workspace label.

    Never a path, never an environment variable, never a timeout above the
    configured cap. ``RuntimeContract`` supplies ``extra="forbid", frozen=True``:
    a model that invents ``env=`` or ``cwd=`` must fail loudly at the boundary,
    not have it silently dropped, because the approval card is built from the
    validated arguments and would never show the field the human is approving.
    """

    command: str = Field(
        min_length=1,
        max_length=_Text.MAX_COMMAND_CHARS,
        description=(
            "One shell command to run in the workspace. Runs with a non-login, "
            "non-interactive shell; stdin is closed. State is NOT preserved "
            "between calls - use one command, not `cd X && ...`. Shell aliases "
            "and functions are not available."
        ),
    )

    workspace: str | None = Field(
        default=None,
        max_length=_Text.MAX_LABEL_CHARS,
        description=(
            "Which attached folder to run in, by its label. Omit when only one "
            "is attached. Not a path."
        ),
    )

    timeout_s: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Seconds to wait. Defaults to 120. Values above the configured "
            "maximum are refused, not clamped."
        ),
    )

    @field_validator("command")
    @classmethod
    def _reject_control_characters(cls, value: str) -> str:
        """NUL and the C0 range other than tab / newline / carriage-return are refused.

        A NUL truncates the string at the exec boundary while the approval card
        renders the whole thing: the human would approve one command and a
        different one would run. That is the entire class of "the card and the
        process disagree" bug, and it is closed here rather than in the UI.

        The value is never echoed back — see :class:`_Message`. Note what is
        deliberately *not* rejected: ``0x7F`` (DEL) and the zero-width /
        bidirectional Unicode characters are still permitted, because they do
        not truncate at ``execve`` and refusing them here would be a rendering
        rule enforced in the wrong layer. §16.3 owns the rendering half.
        """

        for character in value:
            if character < _Text.C0_CEILING and character not in _Text.ALLOWED_CONTROL:
                raise ValueError(_Message.CONTROL_CHARACTERS)
        if not value.strip():
            raise ValueError(_Message.BLANK_COMMAND)
        return value

    @field_validator("workspace")
    @classmethod
    def _reject_label_control_characters(cls, value: str | None) -> str | None:
        """Hold the label to the same rule as the command.

        Stricter than §4.2, which validates only ``command``. A label is
        rendered on the approval card beside the command (AC2.3), so an
        invisible character in it has the same "card lies to the human" shape.
        The label is resolved against a closed set of bound labels afterwards;
        this only keeps an unrenderable one from reaching that resolution.
        """

        if value is None:
            return None
        for character in value:
            if character < _Text.C0_CEILING and character not in _Text.ALLOWED_CONTROL:
                raise ValueError(_Message.LABEL_CONTROL_CHARACTERS)
        if not value.strip():
            raise ValueError(_Message.BLANK_LABEL)
        return value


class RunCommandResult(ShellContract):
    """What the model is told. Every field is either a runtime fact or absent.

    Returned as a JSON string rather than prose, so a model learns that
    something failed by reading ``exit_code`` instead of parsing English.

    ``workspace`` is the **label**. The host-absolute path is not in the result,
    not in the event payload, and not in the transcript — the same rule the
    sandbox lane holds itself to for its own event sink.

    One deviation from §4.3, made deliberately: ``workspace`` carries a default
    of ``""`` instead of being unconditionally required, because a refusal can
    happen before any workspace is bound (a disabled capability, an unknown
    label) and requiring the field there would force the code to invent a label.
    The invariant is kept as a validator instead — anything that reached a
    process must name its workspace.
    """

    status: ShellExecutionStatus
    exit_code: int | None = None
    output: str = ""
    truncated: bool = False
    output_ref: str | None = Field(default=None, max_length=1024)
    output_total_bytes: int | None = Field(default=None, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    workspace: str = Field(default="", max_length=_Text.MAX_LABEL_CHARS)
    exit_note: str | None = Field(default=None, max_length=2048)
    reason: ShellRefusalReason | None = None

    @field_validator("output_ref")
    @classmethod
    def _reject_host_shaped_ref(cls, value: str | None) -> str | None:
        """Reject the two spellings that are host paths on their face.

        A Windows drive letter (``C:/...``) or a UNC prefix (``//host/share``)
        cannot be a virtual agent-scratch path. This is a guard, not a proof:
        the caller that mints ``output_ref`` owns the invariant, and a POSIX
        host path is indistinguishable from a virtual one at this layer. Said
        plainly rather than implied, so nobody reads the validator as the
        control.
        """

        if value is None:
            return None
        candidate = value.replace("\\", "/")
        drive_shaped = len(candidate) >= 2 and candidate[1] == ":"
        if drive_shaped or candidate.startswith("//"):
            raise ValueError(_Message.OUTPUT_REF_NOT_VIRTUAL)
        return value

    @model_validator(mode="after")
    def _check_status_invariants(self) -> "RunCommandResult":
        """Keep the status and the rest of the row from disagreeing."""

        spawned = self.status in ShellStatusGroups.SPAWNED
        if self.status is ShellExecutionStatus.COMPLETED:
            if self.exit_code is None:
                raise ValueError(_Message.MISSING_EXIT_CODE)
        elif self.exit_code is not None:
            raise ValueError(_Message.EXIT_CODE_ON_NON_COMPLETED)

        if spawned:
            if self.reason is not None:
                raise ValueError(_Message.REASON_FORBIDDEN)
            if not self.workspace:
                raise ValueError(_Message.WORKSPACE_REQUIRED)
        else:
            if self.reason is None:
                raise ValueError(_Message.REASON_REQUIRED)
            if self.output:
                raise ValueError(_Message.OUTPUT_ON_REFUSAL)

        if self.truncated:
            if self.output_total_bytes is None:
                raise ValueError(_Message.TRUNCATION_REQUIRES_TOTAL)
        elif self.output_total_bytes is not None or self.output_ref is not None:
            raise ValueError(_Message.TOTAL_BYTES_REQUIRES_TRUNCATION)
        return self


class ShellRefusal(ShellContract):
    """One call that produced no process, and why.

    Carried by :class:`ShellRefusedError` so the decision can be raised at the
    point that makes it and converted into a :class:`RunCommandResult` once, at
    the tool boundary. ``reason`` is the closed code; ``note`` is the
    model-facing sentence, authored from constants rather than interpolated from
    an exception, so a refusal cannot leak deployment configuration.
    """

    status: ShellExecutionStatus
    reason: ShellRefusalReason
    note: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def _check_status_did_not_spawn(self) -> "ShellRefusal":
        if self.status not in ShellStatusGroups.NOT_SPAWNED:
            raise ValueError(_Message.REFUSAL_STATUS)
        return self

    @classmethod
    def refused(cls, reason: ShellRefusalReason, note: str) -> "ShellRefusal":
        """A floor decision: this command may not run, and there is nothing to click."""

        return cls(status=ShellExecutionStatus.REFUSED, reason=reason, note=note)

    @classmethod
    def unavailable(cls, reason: ShellRefusalReason, note: str) -> "ShellRefusal":
        """The capability could not serve this call; the command itself is not judged."""

        return cls(status=ShellExecutionStatus.UNAVAILABLE, reason=reason, note=note)

    def as_result(self, *, duration_ms: int = 0) -> RunCommandResult:
        """Project the refusal into the model-facing result shape.

        ``output`` stays empty because no process wrote anything; the sentence
        goes to ``exit_note``, which is the result's one model-facing prose
        field. ``workspace`` is left empty for the same reason the field has a
        default at all.
        """

        return RunCommandResult(
            status=self.status,
            output="",
            duration_ms=duration_ms,
            exit_note=self.note,
            reason=self.reason,
        )


class ShellRefusedError(Exception):
    """Typed domain error raised where a refusal is decided.

    Raised rather than returned so a refusal cannot be dropped by a caller that
    forgot to inspect a union. The tool boundary catches it exactly once and
    calls :meth:`ShellRefusal.as_result`.
    """

    def __init__(self, refusal: ShellRefusal) -> None:
        super().__init__(refusal.note)
        self._refusal = refusal

    @property
    def refusal(self) -> ShellRefusal:
        """The typed decision, safe to project into model-facing output."""

        return self._refusal


class ShellExecutionRequest(ShellContract):
    """Everything the executor needs, and nothing it may decide for itself.

    Not a model-facing shape: by the time one of these exists the never-list has
    run, the workspace label has been resolved to a real directory, the timeout
    has been checked against the configured ceiling, and the environment has
    been *built* by :mod:`~agent_runtime.capabilities.shell.environment`. The
    executor's whole job is to spawn, bound, and reap — it makes no policy
    decision, which is why it can be the one subprocess call site without also
    being the place every rule accretes.
    """

    command: str = Field(min_length=1, max_length=_Text.MAX_COMMAND_CHARS)
    cwd: Path
    timeout_s: int = Field(ge=1)
    #: Constructed by allowlist; never ``os.environ``. ``repr=False`` keeps it
    #: out of exception rendering as a matter of hygiene, not because it is
    #: expected to hold anything sensitive — by construction it does not.
    env: dict[str, str] = Field(default_factory=dict, repr=False)
    shell_path: str = Field(min_length=1)
    output_cap_bytes: int = Field(ge=1)
    #: Where the full output spills once it passes ``output_cap_bytes``. A
    #: virtual-scratch-backed host path minted by the caller; ``None`` means
    #: overflow is counted and dropped rather than kept.
    spill_path: Path | None = None
    #: Hard ceiling on the spill file. Beyond the PRD, which caps only what the
    #: model sees: without this, "print a gigabyte" is bounded in memory and
    #: unbounded on the user's disk.
    spill_cap_bytes: int = Field(default=8 * 1024 * 1024, ge=0)

    @field_validator("cwd")
    @classmethod
    def _require_absolute_cwd(cls, value: Path) -> Path:
        """A relative cwd would resolve against the worker's own directory."""

        if not value.is_absolute():
            raise ValueError(_Message.CWD_NOT_ABSOLUTE)
        return value


class ShellExecutionOutcome(ShellContract):
    """What the executor observed. No labels, no policy, no scratch vocabulary.

    ``output`` is the tail, decoded, already inside ``output_cap_bytes``.
    ``output_total_bytes`` is the true total the process wrote, which is what
    makes the truncation notice able to say "kept the last 64 KiB of 4.2 MB"
    honestly even though those bytes are long gone from memory.
    """

    status: ShellExecutionStatus
    exit_code: int | None = None
    output: str = ""
    truncated: bool = False
    output_total_bytes: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    #: True when the spill file itself hit ``spill_cap_bytes`` and stopped
    #: growing. The model's ``output`` is still the true tail; the spill then
    #: holds the head. Surfaced rather than hidden so the transcript can say so.
    spill_truncated: bool = False
    #: Set when a spill file was actually written.
    spill_written: bool = False

    @model_validator(mode="after")
    def _check_exit_code_matches_status(self) -> "ShellExecutionOutcome":
        if self.status is ShellExecutionStatus.COMPLETED:
            if self.exit_code is None:
                raise ValueError(_Message.MISSING_EXIT_CODE)
        elif self.exit_code is not None:
            raise ValueError(_Message.EXIT_CODE_ON_NON_COMPLETED)
        if self.status not in ShellStatusGroups.SPAWNED:
            raise ValueError(_Message.OUTCOME_STATUS)
        return self


class ShellCommandCancelled(Exception):
    """The run was cancelled while a command was live.

    Carries the partial outcome (AC5.2) so the boundary can answer with
    ``status="cancelled"`` and the output captured up to the cancellation rather
    than an empty string. Raised instead of returned because swallowing an
    ``asyncio.CancelledError`` and returning normally would absorb a cancel the
    caller may still need to propagate — that decision belongs to the boundary,
    not to the executor.
    """

    def __init__(self, outcome: ShellExecutionOutcome) -> None:
        super().__init__("command cancelled")
        self._outcome = outcome

    @property
    def outcome(self) -> ShellExecutionOutcome:
        """The partial execution, already bounded and decoded."""

        return self._outcome
