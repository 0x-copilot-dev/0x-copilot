"""Typed contracts for ``run_tool_program`` — a declarative multi-step tool plan.

The model submits a *plan*, not a program: a list of steps, each naming one
already-authorized tool, plus a final projection. Dependencies between steps are
**structural** — a step argument may be a :class:`StepRef` marker addressing a
prior step's output — never string interpolation of model-authored code. There
is no interpreter here and no expression language: the only thing the executor
ever evaluates is "walk this path into that step's output".

What the model gets back is :class:`ToolProgramResult`: the resolved projection
and a per-step ledger of outcomes. Intermediate step outputs are deliberately
**not** returned — keeping them out of the transcript is the entire point of
batching.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import Field

from agent_runtime.execution.contracts import JsonValue, RuntimeContract


class ToolProgramErrorCode(StrEnum):
    """Stable, redaction-safe failure classes.

    Every one of these is precise enough for the model to act on without a
    retry-the-whole-thing guess: a plan error names the offending step, and a
    runtime error names the step that produced it.
    """

    INVALID_PLAN = "invalid_plan"
    UNKNOWN_TOOL = "unknown_tool"
    UNKNOWN_STEP_REFERENCE = "unknown_step_reference"
    CYCLIC_DEPENDENCY = "cyclic_dependency"
    STEP_LIMIT_EXCEEDED = "step_limit_exceeded"
    REFERENCE_UNRESOLVED = "reference_unresolved"
    STEP_DENIED = "step_denied"
    STEP_FAILED = "step_failed"
    STEP_REQUIRES_APPROVAL = "step_requires_approval"
    WALL_CLOCK_EXCEEDED = "wall_clock_exceeded"
    PAYLOAD_LIMIT_EXCEEDED = "payload_limit_exceeded"


class StepStatus(StrEnum):
    """Terminal disposition of one step, as reported back to the model."""

    COMPLETED = "completed"
    DENIED = "denied"
    FAILED = "failed"
    REQUIRES_APPROVAL = "requires_approval"
    NOT_RUN = "not_run"


class ToolProgramError(Exception):
    """Internal typed error carrying a stable code, a safe message, and a step.

    Converted into a :class:`ToolProgramResult` before it can reach model
    output; it never escapes as a traceback.
    """

    def __init__(
        self,
        code: ToolProgramErrorCode,
        safe_message: str,
        *,
        step_id: str | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.step_id = step_id


class StepRef(RuntimeContract):
    """A typed reference to a prior step's output.

    Wire form, written by the model inside a step argument or the projection::

        {"$from": "issues", "path": ["items", 0, "id"]}

    ``path`` walks the referenced output: a ``str`` indexes a mapping key, an
    ``int`` indexes a sequence position. An empty path means the whole output.

    Recognition is deliberately strict. A mapping carrying ``$from`` is *always*
    treated as a reference; if the rest of its shape is wrong the plan is
    rejected rather than being silently passed through as a literal argument,
    because a "literal" that the model meant as a reference would send the wrong
    payload to a real connector.
    """

    FROM_KEY: ClassVar[str] = "$from"
    PATH_KEY: ClassVar[str] = "path"

    step: str = Field(min_length=1, max_length=64)
    path: tuple[str | int, ...] = ()

    @classmethod
    def marker(cls, value: object) -> bool:
        """Whether ``value`` is shaped like a reference marker at all."""

        return isinstance(value, Mapping) and cls.FROM_KEY in value

    @classmethod
    def parse(cls, value: object) -> "StepRef":
        """Parse a recognised marker, or raise :class:`ToolProgramError`."""

        if not isinstance(value, Mapping):  # pragma: no cover - guarded by marker()
            raise ToolProgramError(
                ToolProgramErrorCode.INVALID_PLAN,
                "a step reference must be an object",
            )
        unknown = set(value) - {cls.FROM_KEY, cls.PATH_KEY}
        if unknown:
            raise ToolProgramError(
                ToolProgramErrorCode.INVALID_PLAN,
                f"a step reference accepts only '{cls.FROM_KEY}' and "
                f"'{cls.PATH_KEY}'; got {sorted(unknown)}",
            )
        target = value[cls.FROM_KEY]
        if not isinstance(target, str) or not target:
            raise ToolProgramError(
                ToolProgramErrorCode.INVALID_PLAN,
                f"'{cls.FROM_KEY}' must name a step id",
            )
        raw_path = value.get(cls.PATH_KEY, ())
        if isinstance(raw_path, (str, bytes)) or not isinstance(raw_path, Sequence):
            raise ToolProgramError(
                ToolProgramErrorCode.INVALID_PLAN,
                f"'{cls.PATH_KEY}' must be a list of keys and indexes",
                step_id=target,
            )
        segments: list[str | int] = []
        for segment in raw_path:
            # bool is an int subclass; a bool path segment is a typo, not an index.
            if isinstance(segment, bool) or not isinstance(segment, (str, int)):
                raise ToolProgramError(
                    ToolProgramErrorCode.INVALID_PLAN,
                    f"'{cls.PATH_KEY}' segments must be strings or integers",
                    step_id=target,
                )
            segments.append(segment)
        return cls(step=target, path=tuple(segments))


# NOTE (not a docstring, deliberately): ``tool`` is an **opaque** identifier. It
# is never parsed, prefixed, or pattern-matched here — it is looked up verbatim
# against the run's authorized tool surface, so a tool naming scheme can change
# without touching this file. That fact belongs to maintainers, and every
# sentence in the docstring below is instead paid for on every model call of
# every run, because pydantic puts it in the tool schema.
class ToolProgramStep(RuntimeContract):
    """One tool call in the plan."""

    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.\-]+$",
        description="Name for this step, referenced by later steps.",
    )
    tool: str = Field(
        min_length=1,
        max_length=256,
        description="A tool you are already allowed to call.",
    )
    arguments: dict[str, JsonValue] = Field(
        default_factory=dict,
        description=(
            'Arguments for the tool. Any value may be {"$from": "<step id>", '
            '"path": [...]} to use that step\'s output.'
        ),
    )
    depends_on: tuple[str, ...] = Field(
        default=(),
        description=(
            "Steps that must finish first when this step does not reference "
            "their output."
        ),
    )


class RunToolProgramInput(RuntimeContract):
    """Model-facing tool input. It cannot set limits, identity, or a tool map.

    ``result`` is the **projection**: an arbitrary JSON structure whose embedded
    :class:`StepRef` markers are resolved once every step has run. Only this
    value comes back — that is what makes a five-step plan cost one tool result
    instead of five.
    """

    steps: tuple[ToolProgramStep, ...]
    result: JsonValue = None


class ToolProgramLimits(RuntimeContract):
    """Hard bounds for one program. The model cannot raise any of them.

    Sourced from the hyperparameter document at wiring time (see
    ``hyperparameters.tool_program``), so tuning is a reviewable diff.
    """

    max_steps: int = Field(gt=0)
    max_concurrency: int = Field(gt=0)
    wall_clock_ms: int = Field(gt=0)
    #: Sum of every step's serialized output. The batch's whole justification is
    #: that intermediate payloads stay out of context; this stops it becoming a
    #: way to pull an unbounded amount of data into one process instead.
    max_total_output_bytes: int = Field(gt=0)
    #: Ceiling on the serialized projection actually handed to the model.
    max_result_bytes: int = Field(gt=0)


class StepOutcome(RuntimeContract):
    """What happened to one step. Carries no output — only the projection does."""

    step_id: str
    tool: str
    status: StepStatus
    error_code: ToolProgramErrorCode | None = None
    safe_message: str | None = None


class ToolProgramResult(RuntimeContract):
    """Terminal result handed back to the model as one JSON tool result."""

    status: Literal["completed", "failed"]
    result: JsonValue = None
    steps: tuple[StepOutcome, ...] = ()
    failed_step: str | None = None
    error_code: ToolProgramErrorCode | None = None
    safe_message: str | None = None

    @classmethod
    def failure(
        cls,
        *,
        error: ToolProgramError,
        steps: Sequence[StepOutcome] = (),
    ) -> "ToolProgramResult":
        """Project a typed error into a failed result that names its step."""

        return cls(
            status="failed",
            steps=tuple(steps),
            failed_step=error.step_id,
            error_code=error.code,
            safe_message=error.safe_message,
        )


__all__ = (
    "RunToolProgramInput",
    "StepOutcome",
    "StepRef",
    "StepStatus",
    "ToolProgramError",
    "ToolProgramErrorCode",
    "ToolProgramLimits",
    "ToolProgramResult",
    "ToolProgramStep",
)
