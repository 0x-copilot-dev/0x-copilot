"""Pydantic mirror + validator for the SurfaceSpec contract (generative-UI PRD-01).

The JSON Schema in ``copilot_service_contracts.surface_spec`` is the single
source of truth. These models mirror it; a cross-language parity test pins the
two together so they cannot drift. :func:`validate_surface_spec` is the domain
entry point: it gates an untrusted dict against the schema's own required /
const / enum declarations, then runs full field-level validation through the
pydantic model.

The dot-path accessor itself — its grammar AND how it reads a value — is
:class:`SurfaceDotPath`, public because it is not private to :class:`SurfaceSpec`:
the shaping answer (:mod:`.shaping_answer`) constrains and resolves the same
accessor, and a security-relevant grammar with two implementations is a grammar
with two behaviours.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import Field, ValidationError, ValidationInfo, field_validator

from copilot_service_contracts.implemented_archetypes import (
    IMPLEMENTED_SURFACE_ARCHETYPES,
)
from copilot_service_contracts.surface_spec import load_surface_spec_schema

from agent_runtime.execution.contracts import RuntimeContract


class SurfaceArchetype(StrEnum):
    """Render families a SurfaceSpec may bind to (v1).

    A frontend may implement a subset; an unknown archetype falls back to the
    tier-3 generic renderer and is never an error. Order mirrors
    ``surface_spec.schema.json`` ``$defs.archetype``.

    That subset is not guesswork: :meth:`implemented` names it. Validate against
    the whole enum, but license generation with :meth:`implemented` only.
    """

    RECORD = "record"
    TABLE = "table"
    MESSAGE = "message"
    DOC = "doc"
    BOARD = "board"
    EVENT = "event"
    TIMELINE = "timeline"
    DASHBOARD = "dashboard"
    FILE = "file"
    FORM = "form"

    @classmethod
    def implemented(cls) -> tuple[SurfaceArchetype, ...]:
        """The archetypes a shipped renderer can actually draw, in registry order.

        Deliberately narrower than ``tuple(cls)``, and the two must not be
        conflated. The enum is the *contract's* vocabulary: it stays frozen at
        ten so a spec replayed from an older run still validates, and an
        archetype the client has no renderer for degrades to the generic view
        rather than erroring. What the client can draw is a different, smaller,
        moving fact — owned by ``packages/surface-renderers`` and published
        through ``copilot_service_contracts.implemented_archetypes`` so both
        languages read one value rather than two hand-synced lists.

        This is the set a spec GENERATOR must be licensed by. Licensing it by
        the enum is the defect this exists to close: five of the ten have no
        renderer, so a generated ``dashboard`` collapsed to a generic view and
        the model was never told. Validation is the opposite case and stays on
        the enum — accepting is not licensing.

        Raises ``ValueError`` if the shared constant names something outside the
        vocabulary. Two files disagreeing must fail loudly here rather than
        license a value the schema itself would later reject.
        """

        return tuple(cls(name) for name in IMPLEMENTED_SURFACE_ARCHETYPES)


class SurfaceFieldFormat(StrEnum):
    """Purely visual presentation hint the renderer applies to a value."""

    TEXT = "text"
    NUMBER = "number"
    CURRENCY = "currency"
    DATETIME = "datetime"
    BADGE = "badge"
    USER = "user"


class ColumnAlign(StrEnum):
    """Horizontal alignment for a table/board column."""

    START = "start"
    END = "end"


class SurfaceSpecRung(StrEnum):
    """Which rung of the spec-acquisition ladder produced a surface's spec.

    Not part of the SurfaceSpec contract — it describes how a spec was *found*,
    not what it renders, which is why it hangs off :class:`SurfaceEnvelope` and
    not off :class:`SurfaceSpec` or :class:`SurfaceState`. It exists because the
    Work Ledger's ``view.derived`` event must state the basis a surface was
    shaped on, and only the projector that climbed the ladder knows which rung
    answered. Re-deriving it downstream would give one fact two producers, which
    is a defect ``SurfaceState.source`` has already had to be fixed for.

    Values are byte-identical to ``agent_runtime.surfaces_v2.emitter.SpecRung``,
    the wire-side spelling of the same vocabulary, and a parity test pins the
    two together. The direction of the duplication is deliberate: this module is
    pure domain and must not import the ledger emitter.

    A projector answers ``builtin``, ``store``, ``shape_match`` or ``inferred``
    from the deterministic ladder, and ``selected`` / ``generated`` when it asked
    the model the *shaping* question for a payload none of those could bind
    (``SurfaceProjector.project``). ``generated`` also still arrives out of band,
    as a ``surface_spec_generated`` event refining an already-rendered surface.

    ``shape_match`` exists so provenance cannot lie. A shape match reuses a spec
    that IS curated, for a connector nobody curated — Linear's ``save_issue``
    drawn by the spec written for ``create_issue``. Folding it into ``builtin``
    would record human sign-off on a pairing no human ever saw; folding it into
    ``inferred`` would understate a spec a person did write. It is its own rung
    because it is its own fact.

    ``selected`` and ``generated`` are two rungs for the same reason. Both are a
    model's answer, and they differ in whose values the user is looking at: a
    ``select`` binding returns paths INTO the connector's payload, a ``generate``
    binding returns rows the model wrote. The split is
    ``ShapingBindingMode.view_basis``'s, carried here so the emitter's
    ``(tier, basis)`` map states it without re-deriving it.
    """

    BUILTIN = "builtin"
    STORE = "store"
    SHAPE_MATCH = "shape_match"
    INFERRED = "inferred"
    GENERATED = "generated"
    SELECTED = "selected"


class _Limits:
    """Bounds applied to untrusted SurfaceSpec inputs."""

    LABEL_MIN = 1
    LABEL_MAX = 40


class _Patterns:
    """Pre-compiled regexes for SurfaceSpec input validation."""

    # Dotted accessor: identifier segments and array indices only (``a.b.0.c``).
    # No expressions, functions, brackets, or code — mirrors ``$defs.dotPath``.
    DOT_PATH: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+)(?:\.(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+))*$"
    )


class _Messages:
    """Safe, actionable validation messages surfaced through ``SurfaceSpecError``."""

    NOT_OBJECT = "surface spec must be a JSON object"

    @staticmethod
    def missing_required(field_name: str) -> str:
        return f"surface spec is missing required field: {field_name}"

    @staticmethod
    def bad_spec_version(expected: object, value: object) -> str:
        return f"spec_version must equal {expected!r}; got {value!r}"

    @staticmethod
    def unknown_archetype(allowed: list[str], value: object) -> str:
        return f"archetype must be one of {', '.join(allowed)}; got {value!r}"

    @staticmethod
    def path_not_string(field_name: str) -> str:
        return f"{field_name} must be a dot-path string"

    @staticmethod
    def bad_path(field_name: str, value: object) -> str:
        return (
            f"{field_name} is not a valid dot-path (dots + array indices only, "
            f"e.g. 'a.b.0.c'); got {value!r}"
        )

    @staticmethod
    def from_validation_error(exc: ValidationError) -> str:
        first = exc.errors()[0] if exc.errors() else None
        if first is None:
            return "surface spec failed validation"
        loc = ".".join(str(part) for part in first.get("loc", ())) or "surface spec"
        return f"{loc}: {first.get('msg', 'invalid value')}"


class SurfaceSpecError(ValueError):
    """Raised when an untrusted dict is not a valid SurfaceSpec.

    Carries only a safe, actionable message — never internal traceback content.
    """


class SurfaceDotPath:
    """The dot-path accessor: its grammar, and how it reads a value.

    Both halves live here because they are one contract. ``validate`` gates the
    string against ``$defs.dotPath`` (identifier segments and array indices —
    no expressions, no code); ``resolve`` walks a validated path against data
    with the same segment semantics the frontend resolver uses.

    Public because the dot-path is not private to :class:`SurfaceSpec`. The
    shaping answer (:mod:`.shaping_answer`) constrains and resolves the same
    accessor, and a second implementation of a security-relevant grammar is how
    the two drift apart.
    """

    _MISSING = object()

    @classmethod
    def validate(cls, value: object, field_name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(_Messages.path_not_string(field_name))
        if not _Patterns.DOT_PATH.fullmatch(value):
            raise ValueError(_Messages.bad_path(field_name, value))
        return value

    @classmethod
    def resolve(cls, data: object, path: str) -> tuple[bool, object]:
        """Read ``path`` out of ``data``.

        Returns ``(found, value)`` rather than a bare value so a key that is
        present and legitimately ``None`` is distinguishable from a key that is
        absent — the distinction the generated-rows check turns on.
        """

        current: object = data
        for segment in path.split("."):
            current = cls._step(current, segment)
            if current is cls._MISSING:
                return (False, None)
        return (True, current)

    @classmethod
    def _step(cls, current: object, segment: str) -> object:
        if isinstance(current, Mapping):
            return current.get(segment, cls._MISSING)
        if (
            segment.isdigit()
            and isinstance(current, Sequence)
            and not isinstance(current, (str, bytes))
        ):
            index = int(segment)
            if 0 <= index < len(current):
                return current[index]
        return cls._MISSING


class SurfaceSource(RuntimeContract):
    """The connector server + tool whose output shape a spec maps."""

    server: str = Field(min_length=1)
    tool: str = Field(min_length=1)


class SurfaceField(RuntimeContract):
    """A label/value pair for record | message | doc archetypes."""

    label: str = Field(min_length=_Limits.LABEL_MIN, max_length=_Limits.LABEL_MAX)
    path: str
    format: SurfaceFieldFormat | None = None

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str, info: ValidationInfo) -> str:
        return SurfaceDotPath.validate(value, info.field_name or "path")


class SurfaceColumn(RuntimeContract):
    """A column definition for table | board archetypes."""

    label: str = Field(min_length=_Limits.LABEL_MIN, max_length=_Limits.LABEL_MAX)
    path: str
    format: SurfaceFieldFormat | None = None
    align: ColumnAlign | None = None

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str, info: ValidationInfo) -> str:
        return SurfaceDotPath.validate(value, info.field_name or "path")


class SurfaceLink(RuntimeContract):
    """A single outbound link. ``url_path`` resolves into payload data and is
    host-sanitised at render — there are no free-form URLs (plan D9)."""

    label: str = Field(min_length=1)
    url_path: str

    @field_validator("url_path")
    @classmethod
    def _check_url_path(cls, value: str, info: ValidationInfo) -> str:
        return SurfaceDotPath.validate(value, info.field_name or "url_path")


class SurfaceSpec(RuntimeContract):
    """Declarative binding of a tool's output shape onto an archetype's slots.

    Zero side-effectful members: no handlers, no free-form URLs, no templates,
    no code. Validation is the entire security gate.
    """

    spec_version: Literal[1]
    archetype: SurfaceArchetype
    source: SurfaceSource
    title_path: str
    subtitle_path: str | None = None
    fields: list[SurfaceField] | None = None
    columns: list[SurfaceColumn] | None = None
    items_path: str | None = None
    group_by_path: str | None = None
    link: SurfaceLink | None = None

    @field_validator("title_path", "subtitle_path", "items_path", "group_by_path")
    @classmethod
    def _check_paths(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return value
        return SurfaceDotPath.validate(value, info.field_name or "path")


class SurfaceFieldChange(RuntimeContract):
    """One proposed field change carried inside a surface diff. Structurally
    compatible with the frontend ``GenericFieldChange``."""

    field: str = Field(min_length=1)
    old: object | None = None
    new: object | None = None


class SurfaceState(RuntimeContract):
    """The rendered state of a surface: an optional spec plus the raw data.

    ``spec`` absent ⇒ the frontend renders the tier-3 generic view; a spec may
    arrive later via ``surface_spec_generated`` and be merged by URI (PRD-04).
    ``data`` is untrusted tool output — the schema keeps it inert.

    ``source`` names the connector tool whose output ``data`` is. It belongs to
    the ENVELOPE layer, which the runtime writes: it is never read back out of
    ``data``, because a payload that could name its own provenance could claim
    to be any tool. It exists so the spec-less (tier-3) view can say *which*
    tool it could not match rather than "this tool result" (PRD-02 req 1).

    Optional **forever**: every surface emitted before this field existed
    carries none, and replaying those events must keep rendering. ``None``
    means "unknown tool" — never an error.
    """

    spec: SurfaceSpec | None = None
    source: SurfaceSource | None = None
    data: object


class SurfaceDiff(RuntimeContract):
    """A proposed change to a surface, ridden by approval flows (PRD-09)."""

    spec: SurfaceSpec | None = None
    changes: list[SurfaceFieldChange] = Field(default_factory=list)


class SurfaceEnvelope(RuntimeContract):
    """What rides inside event payloads under the ``surface`` key.

    ``surface_uri`` grammar: ``<archetype>://<server-slug>/<tool-or-resource>/<id>``.

    ``spec_rung`` names which rung of the acquisition ladder produced
    ``state.spec``. It is envelope-level rather than state-level because it is
    *provenance of the resolution*, not something the renderer draws — the state
    is the render contract mirrored in ``packages/surface-renderers`` and gains
    nothing from it. Its consumer is the Work Ledger emitter, which maps it onto
    the pinned ``view.derived`` ``(tier, basis)`` pair.

    Optional **forever**: every envelope built before this field existed carries
    none, and ``None`` means "the producer did not say", never an error.
    """

    surface_uri: str = Field(min_length=1)
    archetype: SurfaceArchetype
    state: SurfaceState
    diff: SurfaceDiff | None = None
    spec_rung: SurfaceSpecRung | None = None


class SurfaceSpecValidator:
    """Gates an untrusted dict against the SurfaceSpec schema, then the model."""

    @classmethod
    def validate(cls, raw: object) -> SurfaceSpec:
        cls._raw_schema_gate(raw)
        try:
            return SurfaceSpec.model_validate(raw)
        except ValidationError as exc:
            raise SurfaceSpecError(_Messages.from_validation_error(exc)) from exc

    @classmethod
    def _raw_schema_gate(cls, raw: object) -> None:
        """Structural pre-checks driven by the service-contracts JSON Schema.

        We deliberately do not pull in a full jsonschema validator (it is not a
        declared dependency; the effort's guardrail prefers hand-rolled checks).
        Instead we read the schema's own ``required`` / ``const`` / ``enum``
        declarations so the schema file genuinely gates — editing it changes
        what this validator accepts. Field-level validation is delegated to the
        pydantic model.
        """

        if not isinstance(raw, dict):
            raise SurfaceSpecError(_Messages.NOT_OBJECT)
        schema = load_surface_spec_schema()
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        defs = schema.get("$defs")
        defs = defs if isinstance(defs, dict) else {}

        required = schema.get("required")
        if isinstance(required, list):
            for name in required:
                if name not in raw:
                    raise SurfaceSpecError(_Messages.missing_required(str(name)))

        spec_version_schema = properties.get("spec_version")
        if isinstance(spec_version_schema, dict) and "const" in spec_version_schema:
            expected = spec_version_schema["const"]
            if raw.get("spec_version") != expected:
                raise SurfaceSpecError(
                    _Messages.bad_spec_version(expected, raw.get("spec_version"))
                )

        archetype_schema = defs.get("archetype")
        allowed = (
            archetype_schema.get("enum") if isinstance(archetype_schema, dict) else None
        )
        if isinstance(allowed, list) and raw.get("archetype") not in allowed:
            raise SurfaceSpecError(
                _Messages.unknown_archetype(
                    [str(item) for item in allowed], raw.get("archetype")
                )
            )


def validate_surface_spec(raw: object) -> SurfaceSpec:
    """Validate an untrusted dict as a SurfaceSpec.

    Checks the raw dict against the service-contracts JSON Schema (required /
    const / enum) and the full pydantic field contract. Raises
    :class:`SurfaceSpecError` with an actionable message on any violation.
    """

    return SurfaceSpecValidator.validate(raw)


__all__ = [
    "ColumnAlign",
    "SurfaceArchetype",
    "SurfaceColumn",
    "SurfaceDiff",
    "SurfaceDotPath",
    "SurfaceEnvelope",
    "SurfaceField",
    "SurfaceFieldChange",
    "SurfaceFieldFormat",
    "SurfaceLink",
    "SurfaceSource",
    "SurfaceSpec",
    "SurfaceSpecError",
    "SurfaceSpecRung",
    "SurfaceSpecValidator",
    "SurfaceState",
    "validate_surface_spec",
]
