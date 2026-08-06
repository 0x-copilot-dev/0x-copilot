"""The shaping answer — what the model returns when asked to shape one payload.

A tool result has to become a rendered surface. The deterministic floor
(:mod:`.builtin` curated specs, :mod:`.infer` rung-0) answers instantly and for
free when the payload arrives as a Mapping — and it stays, because it is what
keeps surfaces working for a user with no provider key. But a measured matrix of
eight real MCP shapes produced a spec for only three: an array at the root,
prose, a markdown table, a CSV block and a multi-block result all yield nothing,
because there is no structure to walk. Adding a parser per shape is an infinite
staircase. The decision taken instead is **one model call on the read path**, in
parallel with the main response, and this module is the contract for its answer.

Three legal answers, and no fourth:

* ``{"render": false}`` — nothing here is worth drawing. A **correct** answer,
  not a failure: :class:`ShapingDeclined`. It may say why, and nothing else.
* ``binding.mode == "select"`` — the model chose the shape and returns *paths
  into the payload*. Every value the user sees is still the connector's.
* ``binding.mode == "generate"`` — the model returns *values*, for prose and
  other shapes where no path exists to point at.

That distinction is the reason this module exists at all. This product has a
receipt / audit lane, and "a table whose cells came from the connector" is a
different claim from "a table whose cells the model wrote". The two are recorded
as two different :class:`~agent_runtime.surfaces_v2.ledger_models.ViewBasis`
values, and :attr:`ShapingBindingMode.view_basis` is the single place that
mapping is stated so no producer re-derives it.

**Generated bindings carry a guarantee the select mode cannot.** In ``generate``
mode the model authors the rows AND the paths in one answer, so a path that
misses is not sparse data — it is the model contradicting itself. Validation
therefore rejects a ``generate`` answer unless every ``column.path`` resolves in
every row. A genuinely empty cell is still expressible: write the key with a
``null`` value, which *resolves* (``found=True``) and renders blank.

**What this module deliberately does not do.** It does not convert an answer
into a :class:`~.spec_models.SurfaceSpec`. It cannot, and the reason is
load-bearing rather than incidental: the answer carries a literal ``title``,
whereas a spec is paths-only by construction (``title_path``) — "zero
side-effectful members: no handlers, no free-form URLs, no templates, no code".
Collapsing the two would put a model-authored value inside the contract whose
whole security argument is that it holds none. They stay two contracts; how a
shaping answer reaches a renderer belongs to the caller that made the call.

**An explicit ``null`` is an omission, not a value** — everywhere except inside
a generated row. A provider forced to emit every declared key answers a decline
as ``{"render": false, "archetype": null, "binding": null}``; under
``extra="forbid"`` that read as a *malformed* answer, which cost a retry and was
metered as breakage for an answer that was correct.
:meth:`ShapingAnswerValidator.validate` therefore drops null-valued keys at the
two levels where this contract declares fields — the answer and its ``binding``
— and deliberately not inside ``binding.rows``, the one place a null is data
(see :class:`GenerateBinding`).

Validation is total by design. :meth:`ShapingAnswerValidator.parse` never raises
into a run: a malformed answer degrades to "no surface", which is the same
outcome the user already had before the call was made.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, ClassVar, Literal

from pydantic import (
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceArchetype,
    SurfaceColumn,
    SurfaceDotPath,
)
from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.surfaces_v2.ledger_models import ViewBasis


class _Limits:
    """Bounds applied to an untrusted shaping answer.

    Every number is pinned to a bound that already exists somewhere in the
    surface stack rather than invented here, so an answer this module accepts is
    an answer the rest of the pipeline can already carry.
    """

    # A rendered heading, not prose. Wider than a column label (40) because a
    # title names a whole result ("Open incidents assigned to me").
    TITLE_MIN = 1
    TITLE_MAX = 120

    # == ``generator._SpecBounds.MAX_COLUMNS`` — the count the spec linter
    # already refuses to exceed. An answer with more columns than a spec may
    # carry is an answer nothing downstream could draw.
    COLUMNS_MIN = 1
    COLUMNS_MAX = 12

    # == ``surfaces_v2.rowset._Limits.MAX_ROWS`` — the ceiling the row-set
    # surface already enforces on a materialised table.
    ROWS_MIN = 1
    ROWS_MAX = 200

    # == ``generator._Limits.MAX_MAPPING_KEYS`` — the width the sample redactor
    # already treats as the most of a mapping worth looking at.
    ROW_KEYS_MAX = 60

    # Safe rejection summaries are for logs and the ledger's ``reason``, which
    # is a short lint string, never raw model output. The same bound caps the
    # note a decline may carry: it is the same kind of thing (one operator-facing
    # line), and giving it a second number would let the two drift.
    REASON_MAX = 200


class _Keys:
    """Answer keys this module names as strings rather than as model fields.

    Only the ones something actually reads off a raw dict, a discriminator, or a
    sibling-field lookup — the rest of the shape is declared by the models, and
    restating it here would give every field two spellings.
    """

    RENDER = "render"
    BINDING = "binding"
    MODE = "mode"
    ITEMS_PATH = "items_path"
    ROWS = "rows"


class _Messages:
    """Safe, actionable rejection messages.

    Actionable because a rejection is fed back to the model as a correction, and
    safe because it is also what the ledger records: no payload values, no raw
    model output, no internal detail.
    """

    NOT_OBJECT = "shaping answer must be a JSON object"
    RENDER_NOT_BOOL = "shaping answer must carry a boolean 'render'"
    MISSING_BINDING = "a rendering answer must carry a 'binding' object"
    UNKNOWN_MODE = "binding.mode must be one of select, generate"
    VERDICT_WITHOUT_SURFACE = (
        "a shaping outcome carries a surface if and only if its verdict is render"
    )
    REASON_WITHOUT_REJECTION = "only an invalid shaping outcome carries a reason"

    @staticmethod
    def row_too_wide(index: int, limit: int) -> str:
        return f"binding.rows[{index}] has more than {limit} keys"

    @staticmethod
    def unresolved_generated_path(path: str, index: int) -> str:
        return (
            f"binding.columns path {path!r} does not resolve in "
            f"binding.rows[{index}]; in generate mode the model writes both, so "
            "every column must resolve in every row (use an explicit null for an "
            "empty cell)"
        )

    @classmethod
    def from_validation_error(cls, exc: ValidationError) -> str:
        first = exc.errors()[0] if exc.errors() else None
        if first is None:
            return "shaping answer failed validation"
        loc = ".".join(str(part) for part in first.get("loc", ())) or "shaping answer"
        message = f"{loc}: {first.get('msg', 'invalid value')}"
        return message[: _Limits.REASON_MAX]


class ShapingAnswerError(ValueError):
    """Raised when an untrusted dict is not a legal shaping answer.

    Carries only a safe, actionable message — never internal traceback content
    and never a value read out of the payload.
    """


class ShapingBindingMode(StrEnum):
    """Where the values in a shaped surface came from.

    The two members are not two ways of doing one thing; they are two different
    provenance claims, which is why each maps to its own ledger basis.
    """

    SELECT = "select"
    GENERATE = "generate"

    @property
    def view_basis(self) -> ViewBasis:
        """The Work-Ledger basis a surface built this way must be recorded under.

        Stated once, here, because the alternative is every producer deciding
        for itself what a select binding "counts as" — and a receipt that says
        ``generated`` over connector-derived values (or ``selected`` over
        model-authored ones) is a false claim, not a cosmetic mislabel.
        """

        if self is ShapingBindingMode.SELECT:
            return ViewBasis.SELECTED
        return ViewBasis.GENERATED


class SelectBinding(RuntimeContract):
    """Paths into the payload. The values stay the connector's.

    ``items_path`` is optional because not every archetype has a list: a
    ``record`` binds its columns against the payload root, a ``table`` points at
    the array it draws. Paths are NOT resolved here — this binding names a shape
    for a payload the contract layer never sees, and checking a path against the
    real payload is the existing spec linter's job.
    """

    mode: Literal[ShapingBindingMode.SELECT]
    items_path: str | None = None
    columns: list[SurfaceColumn] = Field(
        min_length=_Limits.COLUMNS_MIN, max_length=_Limits.COLUMNS_MAX
    )

    @field_validator("items_path")
    @classmethod
    def _check_items_path(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return value
        return SurfaceDotPath.validate(value, info.field_name or _Keys.ITEMS_PATH)


class GenerateBinding(RuntimeContract):
    """Values the model wrote, for a payload with no paths to point at.

    Prose is the case this exists for: an answer summarising a paragraph has no
    ``issues[0].identifier`` to select. The rows are model-authored data and are
    recorded as such (:attr:`ShapingBindingMode.view_basis` ⇒ ``generated``).

    The invariant that makes this mode safe to render is checked here: every
    ``column.path`` resolves in every row. The model produced both halves in one
    answer, so a miss is self-contradiction rather than sparse data — and a
    column that finds nothing draws an empty table under a confident heading,
    which is the failure this contract refuses to pass on.
    """

    mode: Literal[ShapingBindingMode.GENERATE]
    rows: list[JsonObject] = Field(
        min_length=_Limits.ROWS_MIN, max_length=_Limits.ROWS_MAX
    )
    columns: list[SurfaceColumn] = Field(
        min_length=_Limits.COLUMNS_MIN, max_length=_Limits.COLUMNS_MAX
    )

    @field_validator("rows")
    @classmethod
    def _check_row_width(cls, value: list[JsonObject]) -> list[JsonObject]:
        for index, row in enumerate(value):
            if len(row) > _Limits.ROW_KEYS_MAX:
                raise ValueError(_Messages.row_too_wide(index, _Limits.ROW_KEYS_MAX))
        return value

    @field_validator("columns")
    @classmethod
    def _check_columns_resolve(
        cls, value: list[SurfaceColumn], info: ValidationInfo
    ) -> list[SurfaceColumn]:
        rows = info.data.get(_Keys.ROWS)
        if not isinstance(rows, list):
            # ``rows`` itself failed; pydantic already reports that error and
            # reporting a second, derived one would only bury it.
            return value
        for column in value:
            for index, row in enumerate(rows):
                found, _ = SurfaceDotPath.resolve(row, column.path)
                if not found:
                    raise ValueError(
                        _Messages.unresolved_generated_path(column.path, index)
                    )
        return value


# Discriminated on ``mode``, not a plain union. A plain union tries both members
# and reports whichever failed "best", so a generate binding with one unresolved
# column path came back as ``binding.SelectBinding.mode: Input should be
# 'select'`` — a true statement about the branch nobody asked for, and a
# correction neither a model nor a reader can act on. The discriminator makes
# pydantic validate exactly the arm the answer named.
ShapingBinding = Annotated[
    SelectBinding | GenerateBinding, Field(discriminator=_Keys.MODE)
]


class ShapingDeclined(RuntimeContract):
    """ "Nothing here is worth showing." A first-class answer, not a failure.

    A connector that returns an acknowledgement, a status string or an empty
    result has nothing to draw, and a model that says so has answered correctly.
    Treating a decline as an error is how a pipeline ends up rendering a
    confident empty card over a payload that never had a surface in it.

    **A decline may say why, and may carry nothing else.** ``reason`` is here
    because a model asked to justify itself will write the justification
    somewhere, and a bare ``render: false`` gave it no legal place to put one —
    so ``{"render": false, "reason": "the payload is an acknowledgement"}`` was
    classified *invalid*, retried, and metered as breakage. Naming the field is
    what keeps that from becoming "a decline may carry any extra key": a
    ``binding`` beside a decline is still refused, because a decline that
    smuggles a surface is an answer that cannot be acted on either way.

    The note is operator-facing and goes nowhere near a user: it is never
    rendered, and it is deliberately NOT copied onto
    :attr:`ShapingOutcome.reason`, whose meaning is "why this answer was
    rejected" — a decline was not rejected, and putting the two in one field
    would make an honest "no" read as a failure in a receipt.
    """

    render: Literal[False]
    reason: str = Field(default="", max_length=_Limits.REASON_MAX)


class ShapingSurface(RuntimeContract):
    """A surface the model is prepared to stand behind.

    ``title`` is a literal string the model wrote, which is exactly why this is
    not a :class:`~.spec_models.SurfaceSpec` (see the module docstring). It is
    rendered as plain text and carries no markup, no path and no URL.
    """

    render: Literal[True]
    archetype: SurfaceArchetype
    title: str = Field(min_length=_Limits.TITLE_MIN, max_length=_Limits.TITLE_MAX)
    binding: ShapingBinding

    @property
    def view_basis(self) -> ViewBasis:
        """Provenance for this surface, delegated to the binding that decided it."""

        return self.binding.mode.view_basis


ShapingAnswer = ShapingDeclined | ShapingSurface


class ShapingVerdict(StrEnum):
    """What came back from one shaping call.

    Three outcomes, because two would lie. ``declined`` and ``invalid`` both end
    with no surface on screen, but they are different facts about the model: one
    looked and said no, the other produced something unusable. Folding them
    together would make a broken prompt indistinguishable from a payload that
    genuinely has nothing in it — and only one of those is worth an alarm.
    """

    RENDER = "render"
    DECLINED = "declined"
    INVALID = "invalid"


class ShapingOutcome(RuntimeContract):
    """The total result of validating one shaping answer.

    Construct through :meth:`rendering` / :meth:`declined` / :meth:`invalid`
    rather than the initialiser, so the relationship between ``verdict``,
    ``surface`` and ``reason`` is established in one place — and enforced, so a
    hand-built outcome cannot claim ``render`` with nothing to draw.
    """

    verdict: ShapingVerdict
    surface: ShapingSurface | None = None
    reason: str = ""

    @model_validator(mode="after")
    def _check_verdict_matches_its_evidence(self) -> ShapingOutcome:
        if (self.surface is not None) is not (self.verdict is ShapingVerdict.RENDER):
            raise ValueError(_Messages.VERDICT_WITHOUT_SURFACE)
        if bool(self.reason) and self.verdict is not ShapingVerdict.INVALID:
            raise ValueError(_Messages.REASON_WITHOUT_REJECTION)
        return self

    @classmethod
    def rendering(cls, surface: ShapingSurface) -> ShapingOutcome:
        return cls(verdict=ShapingVerdict.RENDER, surface=surface)

    @classmethod
    def declined(cls) -> ShapingOutcome:
        return cls(verdict=ShapingVerdict.DECLINED)

    @classmethod
    def invalid(cls, reason: str) -> ShapingOutcome:
        return cls(
            verdict=ShapingVerdict.INVALID,
            reason=(reason or "shaping answer failed validation")[: _Limits.REASON_MAX],
        )

    @property
    def renders(self) -> bool:
        """True when this outcome puts a surface on screen."""

        return self.verdict is ShapingVerdict.RENDER

    @property
    def view_basis(self) -> ViewBasis | None:
        """Provenance to record, or ``None`` when nothing is being rendered."""

        return None if self.surface is None else self.surface.view_basis


class ShapingAnswerValidator:
    """Gates an untrusted model answer, raising or totalising as the caller needs.

    :meth:`validate` raises :class:`ShapingAnswerError`; use it where a rejection
    is the interesting event (a retry-with-correction loop, a test).
    :meth:`parse` never raises; use it on the read path, where the run must not
    learn that shaping was even attempted.
    """

    _MODES: ClassVar[Mapping[str, type[SelectBinding] | type[GenerateBinding]]] = {
        ShapingBindingMode.SELECT.value: SelectBinding,
        ShapingBindingMode.GENERATE.value: GenerateBinding,
    }

    @classmethod
    def validate(cls, raw: object) -> ShapingAnswer:
        """Validate one answer, or raise :class:`ShapingAnswerError`.

        The ``render`` flag is read off the raw dict before a model is chosen —
        a plain union would report both branches' failures for every answer, and
        "expected false, got true" buried among a table's worth of errors is not
        a correction anyone (model included) can act on.
        """

        if not isinstance(raw, Mapping):
            raise ShapingAnswerError(_Messages.NOT_OBJECT)
        answer = cls._omitting_nulls(raw)
        render = answer.get(_Keys.RENDER)
        if not isinstance(render, bool):
            raise ShapingAnswerError(_Messages.RENDER_NOT_BOOL)
        if not render:
            return cls._build(ShapingDeclined, answer)
        cls._check_binding_mode(answer)
        return cls._build(ShapingSurface, answer)

    @classmethod
    def parse(cls, raw: object) -> ShapingOutcome:
        """Validate one answer without ever raising into the run.

        Total by construction: shaping is best-effort presentation layered over
        a result the user can already see, so a bad answer costs the surface and
        nothing else.
        """

        try:
            answer = cls.validate(raw)
        except ShapingAnswerError as exc:
            return ShapingOutcome.invalid(str(exc))
        if isinstance(answer, ShapingDeclined):
            return ShapingOutcome.declined()
        return ShapingOutcome.rendering(answer)

    @classmethod
    def _omitting_nulls(cls, raw: Mapping[str, object]) -> dict[str, object]:
        """Read an explicit ``null`` as an omission, not as a value.

        A provider running structured output emits every key its schema
        declares, using ``null`` for the ones that do not apply — that IS the
        idiom for an optional field. Under ``extra="forbid"`` the answer
        ``{"render": false, "archetype": null}`` was therefore a *rejection*:
        one retry spent, one ``schema_invalid`` recorded, for a model that
        answered correctly.

        Applied at the two levels this contract declares fields — the answer and
        its ``binding`` — and nowhere deeper. It must NOT descend into
        ``binding.rows``: a row is model-authored data, and a key present with a
        ``null`` value there is a cell the model deliberately left blank, which
        the generate-mode invariant reads as *resolved* (:class:`GenerateBinding`).
        Stripping it would turn an honest blank into a self-contradiction.
        """

        answer = {key: value for key, value in raw.items() if value is not None}
        binding = answer.get(_Keys.BINDING)
        if isinstance(binding, Mapping):
            answer[_Keys.BINDING] = {
                key: value for key, value in binding.items() if value is not None
            }
        return answer

    @classmethod
    def _check_binding_mode(cls, raw: Mapping[str, object]) -> None:
        """Reject an unknown ``binding.mode`` before pydantic sees the union.

        Without this, an answer naming a mode we do not implement fails as a
        pair of union errors about ``Literal`` mismatches, and the model is told
        two things that are both true and neither useful.
        """

        binding = raw.get(_Keys.BINDING)
        if not isinstance(binding, Mapping):
            raise ShapingAnswerError(_Messages.MISSING_BINDING)
        if binding.get(_Keys.MODE) not in cls._MODES:
            raise ShapingAnswerError(_Messages.UNKNOWN_MODE)

    @classmethod
    def _build(
        cls,
        model: type[ShapingDeclined] | type[ShapingSurface],
        raw: Mapping[str, object],
    ) -> ShapingAnswer:
        try:
            return model.model_validate(raw)
        except ValidationError as exc:
            raise ShapingAnswerError(_Messages.from_validation_error(exc)) from exc


def validate_shaping_answer(raw: object) -> ShapingAnswer:
    """Validate an untrusted model answer as a shaping answer.

    Raises :class:`ShapingAnswerError` with a safe, actionable message. Callers
    on the read path want :func:`parse_shaping_answer` instead.
    """

    return ShapingAnswerValidator.validate(raw)


def parse_shaping_answer(raw: object) -> ShapingOutcome:
    """Validate an untrusted model answer, degrading to "no surface" on anything bad."""

    return ShapingAnswerValidator.parse(raw)


__all__ = [
    "GenerateBinding",
    "SelectBinding",
    "ShapingAnswer",
    "ShapingAnswerError",
    "ShapingAnswerValidator",
    "ShapingBinding",
    "ShapingBindingMode",
    "ShapingDeclined",
    "ShapingOutcome",
    "ShapingSurface",
    "ShapingVerdict",
    "parse_shaping_answer",
    "validate_shaping_answer",
]
