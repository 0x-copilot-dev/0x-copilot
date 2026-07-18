"""Typed contracts for AC6 Monty code mode (embedded code interpreter).

These are the product-owned, adapter-agnostic contracts. Nothing here imports
Monty; the only importer of ``pydantic_monty`` is :mod:`.monty_adapter`, behind
:class:`~agent_runtime.capabilities.interpreter.ports.InterpreterPort`.

The shapes mirror ``docs/plan/desktop/agent-capabilities/06-ac6-monty-code-mode.md``
("Typed contracts"). Where the PRD leaves module grouping open, we keep the
semantics: a model-facing request that *cannot* set limits/adapter/identity, a
richer runtime request the service builds, and typed step results.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from agent_runtime.execution.contracts import JsonValue, RuntimeContract


class InterpreterLimitKind(StrEnum):
    """Resource dimensions the interpreter can exhaust.

    ``limit_kind`` on :class:`InterpreterFailed` is one of these when the
    failure is a resource ceiling.
    """

    CODE_BYTES = "code_bytes"
    WALL_TIME = "wall_time"
    HEAP_BYTES = "heap_bytes"
    ALLOCATIONS = "allocations"
    RECURSION_DEPTH = "recursion_depth"
    EXTERNAL_CALLS = "external_calls"
    SNAPSHOT_BYTES = "snapshot_bytes"
    OUTPUT_BYTES = "output_bytes"


class InterpreterErrorCode(StrEnum):
    """Stable, redaction-safe failure classes (PRD "Stable errors").

    Safe messages must never contain source fragments, callback arguments,
    tool output, host paths, or an adapter traceback.
    """

    INTERPRETER_UNAVAILABLE = "interpreter_unavailable"
    INVALID_SOURCE = "invalid_source"
    UNSUPPORTED_LANGUAGE_FEATURE = "unsupported_language_feature"
    EXTERNAL_FUNCTION_UNKNOWN = "external_function_unknown"
    EXTERNAL_FUNCTION_DENIED = "external_function_denied"
    APPROVAL_EXPIRED = "approval_expired"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    SNAPSHOT_INVALID = "snapshot_invalid"
    SNAPSHOT_INCOMPATIBLE = "snapshot_incompatible"
    CANCELLED = "cancelled"
    INTERPRETER_CRASH = "interpreter_crash"
    RESULT_INVALID = "result_invalid"


class InterpreterLimits(RuntimeContract):
    """Product-owned resource ceilings for one interpreter session.

    Every field is a hard cap the model cannot raise: the model-facing
    :class:`RunCodeModeInput` has no limits field, and the service always
    stamps the deployment limit profile. ``max_*`` values are validated as
    positive so a mis-wired profile fails closed instead of unbounding.
    """

    max_code_bytes: int = Field(gt=0)
    segment_timeout_ms: int = Field(gt=0)
    total_timeout_ms: int = Field(gt=0)
    max_heap_bytes: int = Field(gt=0)
    max_allocations: int = Field(gt=0)
    max_recursion_depth: int = Field(gt=0)
    max_external_calls: int = Field(ge=0)
    max_snapshot_bytes: int = Field(gt=0)
    max_result_bytes: int = Field(gt=0)
    max_stdout_bytes: int = Field(gt=0)
    max_stderr_bytes: int = Field(gt=0)


class ExternalFunctionSpec(RuntimeContract):
    """A resolved alias binding an interpreter name to one authorized tool.

    ``alias`` is the name interpreted code calls (e.g. ``search_web``);
    ``tool_name`` is the already-authorized product/MCP tool it maps to. The
    interpreter never learns ``tool_name`` — only the runtime dispatcher does.
    """

    alias: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)
    output_schema: dict[str, JsonValue] | None = None


class InterpreterRequest(RuntimeContract):
    """Full runtime request the service builds from a model call plus context.

    Carries the resolved external-function allowlist and the stamped limit
    profile. ``external_functions`` is frozen for the session; the interpreter
    can only call an alias present here.
    """

    interpreter_session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    code: str
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    external_functions: tuple[ExternalFunctionSpec, ...] = ()
    limits: InterpreterLimits


class ExternalFunctionCall(RuntimeContract):
    """One suspension point: interpreted code requested an external function.

    ``snapshot`` is the content-addressed reference to the RAM-only interpreter
    state serialized at this boundary (see :mod:`.snapshot_store`). ``source_sha256``
    binds the snapshot to the exact program so a resume cannot smuggle in
    different code.
    """

    interpreter_session_id: str = Field(min_length=1)
    invocation_index: int = Field(ge=0)
    alias: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    snapshot: "SnapshotRef"
    source_sha256: str = Field(min_length=64, max_length=64)


class SnapshotRef(RuntimeContract):
    """Content-addressed reference to a persisted interpreter snapshot.

    Deliberately small: only the digest, byte size, and the envelope metadata
    needed to reject an incompatible resume. The bytes live in the object store;
    a checkpoint stores only this ref (PRD: "A checkpoint never points at an
    uncommitted artifact").
    """

    sha256: str = Field(min_length=64, max_length=64)
    size: int = Field(ge=0)
    adapter: str = Field(min_length=1)
    abi_version: str = Field(min_length=1)
    source_sha256: str = Field(min_length=64, max_length=64)
    limit_profile_hash: str = Field(min_length=1)
    invocation_index: int = Field(ge=0)


class InterpreterCompleted(RuntimeContract):
    """Terminal success: the program produced a JSON-compatible result."""

    result: JsonValue = None
    stdout_preview: str = ""
    stderr_preview: str = ""
    external_invocation_ids: tuple[str, ...] = ()
    payload_ref: "SnapshotRef | None" = None


class InterpreterFailed(RuntimeContract):
    """Terminal failure with a stable code and redaction-safe message."""

    code: InterpreterErrorCode
    safe_message: str
    retryable: bool = False
    limit_kind: InterpreterLimitKind | None = None
    stdout_preview: str = ""
    stderr_preview: str = ""


# One step of an interpreter session: it either finished, needs an external
# call routed through policy, or failed terminally.
InterpreterStep = InterpreterCompleted | ExternalFunctionCall | InterpreterFailed


class RunCodeModeInput(RuntimeContract):
    """Model-facing tool input. Intentionally minimal.

    It cannot set limits, adapter, snapshot ref, runtime identity, tool id,
    permission state, or approval state — the service supplies all of those from
    trusted context.
    """

    code: str
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    external_functions: tuple[str, ...] = ()


class InterpreterError(Exception):
    """Internal typed error carrying a stable code and safe message.

    The service converts this into an :class:`InterpreterFailed` step; it never
    escapes to model output as a raw traceback.
    """

    def __init__(
        self,
        code: InterpreterErrorCode,
        safe_message: str,
        *,
        limit_kind: InterpreterLimitKind | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.limit_kind = limit_kind
        self.retryable = retryable

    def as_failed(
        self, *, stdout_preview: str = "", stderr_preview: str = ""
    ) -> InterpreterFailed:
        """Project this error into a terminal :class:`InterpreterFailed`."""

        return InterpreterFailed(
            code=self.code,
            safe_message=self.safe_message,
            retryable=self.retryable,
            limit_kind=self.limit_kind,
            stdout_preview=stdout_preview,
            stderr_preview=stderr_preview,
        )


class InterpreterLimitProfiles:
    """Named, product-owned limit profiles. Deployment policy may lower defaults.

    Defaults follow the PRD "Resource policy" table (``desktop_v1``). Raising a
    hard ceiling is a reviewed change, not a runtime knob.
    """

    #: PRD desktop_v1 defaults (the "Default" column).
    DESKTOP_V1 = InterpreterLimits(
        max_code_bytes=32 * 1024,
        segment_timeout_ms=3_000,
        total_timeout_ms=10_000,
        max_heap_bytes=32 * 1024 * 1024,
        max_allocations=250_000,
        max_recursion_depth=128,
        max_external_calls=32,
        max_snapshot_bytes=2 * 1024 * 1024,
        max_result_bytes=32 * 1024,
        max_stdout_bytes=32 * 1024,
        max_stderr_bytes=8 * 1024,
    )

    #: Absolute ceilings (the "Hard ceiling" column). A profile whose value
    #: exceeds these is rejected by :meth:`resolve`.
    _HARD = InterpreterLimits(
        max_code_bytes=64 * 1024,
        segment_timeout_ms=10_000,
        total_timeout_ms=30_000,
        max_heap_bytes=64 * 1024 * 1024,
        max_allocations=1_000_000,
        max_recursion_depth=256,
        max_external_calls=64,
        max_snapshot_bytes=8 * 1024 * 1024,
        max_result_bytes=256 * 1024,
        max_stdout_bytes=64 * 1024,
        max_stderr_bytes=16 * 1024,
    )

    _BY_NAME = {"desktop_v1": DESKTOP_V1}

    @classmethod
    def resolve(cls, name: str) -> InterpreterLimits:
        """Return the named profile, clamped to hard ceilings.

        Unknown names fall back to ``desktop_v1`` rather than unbounding, so a
        typo can never widen limits.
        """

        profile = cls._BY_NAME.get(name, cls.DESKTOP_V1)
        return cls._clamp(profile)

    @classmethod
    def _clamp(cls, limits: InterpreterLimits) -> InterpreterLimits:
        """Clamp every field down to the hard ceiling; never up."""

        hard = cls._HARD
        return InterpreterLimits(
            max_code_bytes=min(limits.max_code_bytes, hard.max_code_bytes),
            segment_timeout_ms=min(limits.segment_timeout_ms, hard.segment_timeout_ms),
            total_timeout_ms=min(limits.total_timeout_ms, hard.total_timeout_ms),
            max_heap_bytes=min(limits.max_heap_bytes, hard.max_heap_bytes),
            max_allocations=min(limits.max_allocations, hard.max_allocations),
            max_recursion_depth=min(
                limits.max_recursion_depth, hard.max_recursion_depth
            ),
            max_external_calls=min(limits.max_external_calls, hard.max_external_calls),
            max_snapshot_bytes=min(limits.max_snapshot_bytes, hard.max_snapshot_bytes),
            max_result_bytes=min(limits.max_result_bytes, hard.max_result_bytes),
            max_stdout_bytes=min(limits.max_stdout_bytes, hard.max_stdout_bytes),
            max_stderr_bytes=min(limits.max_stderr_bytes, hard.max_stderr_bytes),
        )


__all__ = (
    "ExternalFunctionCall",
    "ExternalFunctionSpec",
    "InterpreterCompleted",
    "InterpreterError",
    "InterpreterErrorCode",
    "InterpreterFailed",
    "InterpreterLimitKind",
    "InterpreterLimitProfiles",
    "InterpreterLimits",
    "InterpreterRequest",
    "InterpreterStep",
    "RunCodeModeInput",
    "SnapshotRef",
)
