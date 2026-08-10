"""Rung 0 of the surface ladder: deterministic SurfaceSpec inference.

The generative-UI floor (PRD ``generative-ui-floor`` §3.3). Every other rung of
the ladder can fail — the model can be uncredentialed, the store can be cold, the
curated library can simply not know a tool. This one cannot, and that is its
entire reason to exist: with an unconditional floor there is no failure state
left for a user to read, which is why the PRD can *delete* the "No spec matched"
copy rather than reword it (G1/G2).

Two guarantees, and they are the contract:

* **Totality.** :meth:`SurfaceSpecInferrer.infer` never raises and returns
  ``None`` only when its input is not a ``Mapping`` — the one case that has no
  surface at all. Empty mappings, nulls, scalars, heterogeneous arrays, non-string
  keys, megabyte payloads and pathological nesting all yield a valid
  :class:`SurfaceSpec`. A raise here would re-create the exact hole the effort is
  closing, so the build is additionally wrapped in a last-resort fallback.
* **Purity.** No model, no network, no environment, no clock — same posture as
  :mod:`~agent_runtime.capabilities.surfaces.projector`. The only non-CPU work is
  the schema read inside
  :func:`~agent_runtime.capabilities.surfaces.spec_models.validate_surface_spec`,
  taken deliberately so an inferred spec passes the *same* gate a builtin spec
  does (:mod:`~agent_runtime.capabilities.surfaces.builtin` uses it too) — a spec
  we generate must not be able to bypass the validation curated specs face.

:class:`EnvelopeUnwrapper` is public and separate on purpose. The caller must
apply it to the payload it ships as ``SurfaceState.data`` too, because the paths
this module emits bind against the **unwrapped** value. It also subsumes the MCP
``structured_content`` defect (FINDINGS §3.4): ``langchain-mcp-adapters`` hands
the projector ``{"structured_content": {...}}``, so ``items_path: "issues"``
resolved against the wrapper and missed on every server that returns
``structuredContent``.
"""

from __future__ import annotations

__operation_boundary__ = "presentation"

import re
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, TypeGuard

from agent_runtime.capabilities.surfaces.spec_models import (
    ColumnAlign,
    SurfaceArchetype,
    SurfaceFieldFormat,
    SurfaceSource,
    SurfaceSpec,
    validate_surface_spec,
)


class _Limits:
    """Every bound that keeps inference cheap and total on hostile input.

    Sizing is driven by AC7 (p99 under 5 ms for a 200-row payload) and by the
    1 MB-payload case in AC6: a payload can be pathological in three independent
    directions — wide (many keys), long (many rows), deep (much nesting) — so
    each gets its own ceiling rather than one shared "max size" that only guards
    the direction its author happened to picture.
    """

    # Envelope peels, and single-key record descents. Real connector payloads
    # wrap 1–2 deep; this only stops a self-similar or hostile structure.
    UNWRAP_DEPTH = 8
    # Mapping levels the subject search descends, and nodes it may visit.
    SUBJECT_DEPTH = 4
    SUBJECT_NODES = 512
    # Rows read to compute density / infer types. The rest of a long array adds
    # cost without adding signal — connector arrays are homogeneous.
    ROWS_SCANNED = 200
    # Distinct values retained per key for type inference.
    TYPE_SAMPLE = 20
    # Keys considered from a single (possibly enormous) row.
    CANDIDATE_KEYS = 256
    # PRD §3.3: columns/fields are capped at 6.
    MAX_SLOTS = 6
    # Mirrors the schema's ``label`` maxLength; a longer label fails validation.
    LABEL_MAX = 40
    # A string is badge-shaped when it reads as a token, not as prose.
    BADGE_VALUE_CHARS = 24
    BADGE_VALUE_WORDS = 2
    BADGE_DISTINCT = 8
    # Fraction of an array's sampled elements that must be mappings before it
    # can be the subject. Below this it is a list of scalars or a grab-bag, and
    # a table over it would render worse than the record fallback.
    SUBJECT_MAPPING_FRACTION = 0.5


class _Keys:
    """The key vocabularies the heuristics recognise."""

    # Single-key envelopes peeled by :class:`EnvelopeUnwrapper`. The first six
    # are the PRD's set; ``structuredcontent`` is the MCP artifact wrapper —
    # FINDINGS §3.4.
    #
    # Entries are in NORMALISED form (:meth:`_KeyNamer.normalized`: lower-case,
    # alphanumerics only), because that is what they are compared against. One
    # entry therefore covers every spelling a connector might use —
    # ``structuredContent`` on the wire, ``structured_content`` in the adapter's
    # TypedDict, ``StructuredContent`` from something else. Adding a
    # punctuated variant here does nothing; it can never match.
    WRAPPERS: Final[frozenset[str]] = frozenset(
        {
            "data",
            "result",
            "items",
            "output",
            "response",
            "payload",
            "structuredcontent",
        }
    )

    # Probed in order for ``title_path`` (PRD §3.3 step 4).
    TITLE_PROBE: Final[tuple[str, ...]] = (
        "title",
        "name",
        "summary",
        "identifier",
        "key",
    )

    # When a column's value is a nested mapping, the sub-key we bind through —
    # first match wins. This is what lets a spec say ``assignee.displayName``
    # and ``state.name`` instead of dumping a JSON blob into a cell.
    DISPLAY_SUBKEYS: Final[tuple[str, ...]] = (
        "displayname",
        "name",
        "title",
        "label",
        "fullname",
        "login",
        "email",
        "key",
        "identifier",
        "id",
        "avatar",
    )

    # Presence of one of these inside a nested mapping means "this is a person".
    # NOTE the PRD phrases the detector as ``displayName|name|avatar``; taken
    # literally, bare ``name`` types Linear's ``state: {"name": "In Progress"}``
    # as a user. So ``name`` is used to *choose the bound sub-key* (above) while
    # only the unambiguously person-ish markers — plus a person-ish key name,
    # see :data:`_NameGroup.PERSON` — decide the ``user`` format.
    USER_MARKERS: Final[frozenset[str]] = frozenset(
        {"displayname", "avatar", "avatarurl", "email", "login", "username"}
    )

    # Tokens rendered upper-case in a label. Small and deliberate: "Id" and
    # "Url" read as typos in a column header.
    ACRONYMS: Final[frozenset[str]] = frozenset(
        {
            "api",
            "css",
            "csv",
            "html",
            "http",
            "https",
            "id",
            "ip",
            "json",
            "os",
            "pdf",
            "sku",
            "sla",
            "sql",
            "ui",
            "uri",
            "url",
            "uuid",
            "xml",
        }
    )

    # Emitted when nothing title-shaped exists. It is a *valid, unresolved*
    # path: the schema makes ``title_path`` required, so "leave it unset" is not
    # available; an unresolved path renders as an empty headline, which is the
    # honest equivalent and never an error.
    FALLBACK_TITLE = "title"


class _Patterns:
    """Pre-compiled recognisers. Mirrors the schema where it must."""

    # One segment of the schema's ``$defs.dotPath``. Deliberately re-stated here
    # rather than imported from ``spec_models._Patterns`` (private): a key that
    # fails this is skipped BEFORE it can reach validation, so a mismatch would
    # cost a dropped column, never a raised spec.
    SEGMENT: Final[re.Pattern[str]] = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+)$")

    # ISO-8601 date, optionally with a time and offset. Tight on purpose: a
    # false ``datetime`` renders a value as a formatted date and loses it.
    ISO_8601: Final[re.Pattern[str]] = re.compile(
        r"^\d{4}-\d{2}-\d{2}"
        r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?"
        r"(?:[Zz]|[+-]\d{2}:?\d{2})?)?$"
    )

    # Word boundaries for label casing: an acronym run, a capitalised or lower
    # word, or a digit run. ``displayName`` and ``display_name`` both split to
    # ``["display", "name"]``, which is why they Title-Case identically.
    WORD: Final[re.Pattern[str]] = re.compile(
        r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+"
    )

    # Non-alphanumerics stripped when comparing a payload key to a vocabulary
    # entry, so ``displayName`` / ``display_name`` / ``DisplayName`` all match.
    NON_ALNUM: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")


class _NameGroup(StrEnum):
    """What a key's *name* says about it, independent of its value.

    The PRD's ``name_priority`` axis. It carries two things at once: how much a
    column earns its place among six (:class:`_NameGroups`), and — because a
    name is often a better type signal than a sample — what format to bind.
    ``state`` is a badge whether the sample happens to repeat or not.
    """

    IDENTITY = "identity"
    TITLE = "title"
    STATE = "state"
    PERSON = "person"
    TIME = "time"
    OTHER = "other"


class _NameGroups:
    """The vocabularies and weights behind :class:`_NameGroup`.

    Held outside the enum because an annotated assignment inside an ``Enum``
    body becomes a *member*, not a class constant — a ``StrEnum`` would then try
    to build a member out of a mapping and fail at import.

    Each group carries two vocabularies. **Primary** is verbatim the PRD's
    ``name_priority`` ladder (``id|key|identifier`` · ``title|name|summary`` ·
    ``state|status`` · ``assignee|owner|user`` · ``updated|created``) and earns
    the group's full weight. **Secondary** is the near-synonyms real connectors
    use, which get the group's *typing* — ``level`` is as much a badge as
    ``status`` — but a discounted weight. That split is deliberate: without it a
    token we invented (``priority``, at ``state``'s 2.5) silently outranks a
    tier the contract actually names (``updated``, at 2.0) and pushes the
    timestamp column off a six-column table.
    """

    # Checked in this order, so an overlapping key resolves once and
    # predictably: ``priorityLabel`` is a TITLE before it is a STATE.
    VOCABULARY: Final[tuple[tuple[_NameGroup, frozenset[str], frozenset[str]], ...]] = (
        (
            _NameGroup.IDENTITY,
            frozenset({"id", "key", "identifier"}),
            frozenset({"slug", "number", "uuid", "gid"}),
        ),
        (
            _NameGroup.TITLE,
            frozenset({"title", "name", "summary"}),
            frozenset({"subject", "label", "headline"}),
        ),
        (
            _NameGroup.STATE,
            frozenset({"state", "status"}),
            frozenset({"stage", "phase", "level", "priority", "severity"}),
        ),
        (
            _NameGroup.PERSON,
            frozenset({"assignee", "owner", "user"}),
            frozenset({"author", "creator", "reporter", "member", "assigned"}),
        ),
        (
            _NameGroup.TIME,
            frozenset({"updated", "created"}),
            frozenset(
                {"modified", "closed", "due", "published", "seen", "date", "timestamp"}
            ),
        ),
    )

    WEIGHT: Final[Mapping[_NameGroup, float]] = {
        _NameGroup.IDENTITY: 3.0,
        _NameGroup.TITLE: 2.9,
        _NameGroup.STATE: 2.5,
        _NameGroup.PERSON: 2.2,
        _NameGroup.TIME: 2.0,
        _NameGroup.OTHER: 1.0,
    }

    # What a secondary match keeps of its group's weight. Chosen so the lowest
    # discounted tier (``identity`` at 1.8) still outranks an unrecognised key
    # (1.0) while the highest (also 1.8) stays below the lowest primary (2.0).
    SECONDARY_DISCOUNT: Final[float] = 0.6

    @classmethod
    def classify(cls, words: set[str]) -> tuple[_NameGroup, float]:
        """Return the group a key's words fall in, and the weight it earns.

        Every primary vocabulary is consulted before any secondary one, so a
        token the contract names always beats one we added: ``slug_name`` is a
        TITLE (``name``, primary) rather than an IDENTITY (``slug``, secondary).
        """

        if words:
            for group, primary, _ in cls.VOCABULARY:
                if words & primary:
                    return group, cls.WEIGHT[group]
            for group, _, secondary in cls.VOCABULARY:
                if words & secondary:
                    return group, cls.WEIGHT[group] * cls.SECONDARY_DISCOUNT
        return _NameGroup.OTHER, cls.WEIGHT[_NameGroup.OTHER]


# How much a confidently-typed slot outranks an untyped one. Small multipliers:
# the name axis should dominate, with type acting as the tie-breaker it is.
_FORMAT_WEIGHT: Final[Mapping[SurfaceFieldFormat, float]] = {
    SurfaceFieldFormat.USER: 1.3,
    SurfaceFieldFormat.DATETIME: 1.2,
    SurfaceFieldFormat.BADGE: 1.2,
    SurfaceFieldFormat.NUMBER: 1.1,
    SurfaceFieldFormat.CURRENCY: 1.1,
    SurfaceFieldFormat.TEXT: 1.0,
}


# Provenance stamped on a spec nobody named a source for. The renderer prefers
# the envelope's own ``state.source`` (``toolNameFromState`` in
# ``surface-renderers``), so this is a last-resort label rather than something a
# user normally reads — callers that know the connector pass ``source=``.
INFERRED_SOURCE: Final[SurfaceSource] = SurfaceSource(
    server="inferred", tool="inferred"
)


class _Shape:
    """Structural predicates shared by the subject search and the binder."""

    @staticmethod
    def is_array(value: object) -> bool:
        """True for a list-like value. ``str``/``bytes`` are scalars, not arrays."""

        return isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        )

    @staticmethod
    def is_scalar(value: object) -> bool:
        """True for a non-null value that can be rendered in one cell."""

        if value is None:
            return False
        if isinstance(value, (str, bytes, bytearray)):
            return True
        return not isinstance(value, (Mapping, Sequence, set, frozenset))

    @staticmethod
    def is_number(value: object) -> bool:
        """True for a real number. ``bool`` is excluded — it is a subclass of ``int``."""

        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @classmethod
    def is_segment(cls, key: object) -> TypeGuard[str]:
        """True when ``key`` can be one segment of a schema-valid dot-path.

        A ``TypeGuard`` so every call site narrows: a payload may key itself by
        integers, tuples or anything else hashable, and none of those can be
        addressed by a dot-path.
        """

        return isinstance(key, str) and _Patterns.SEGMENT.fullmatch(key) is not None

    @classmethod
    def meaningful_keys(cls, value: Mapping[object, object]) -> list[str]:
        """Dot-path-safe keys carrying a non-null value, in insertion order.

        "Meaningful" excludes ``None`` so a wrapper that pads itself with empty
        siblings (``{"data": {...}, "error": null}``) still reads as a wrapper.
        """

        return [key for key in value if cls.is_segment(key) and value[key] is not None]


class EnvelopeUnwrapper:
    """Peel single-key wrapper envelopes off a payload.

    A connector rarely hands back the thing you asked for; it hands back the
    thing you asked for inside ``{"data": …}`` or ``{"result": …}`` — and, for
    every MCP server that returns ``structuredContent``, inside
    ``{"structured_content": …}``. Every dot-path in a spec is then one segment
    short and resolves to nothing, which is FINDINGS §3.4.

    Two rules keep this from eating a real payload:

    * the wrapper must have exactly **one** meaningful key, and that key must be
      a known envelope name — ``{"issues": [...], "team": {...}}`` is a payload,
      not a wrapper; and
    * the peel must yield a ``Mapping``. Peeling ``{"result": "ok"}`` down to a
      bare string would leave a payload with no addressable paths, and would
      break :meth:`SurfaceSpecInferrer.infer`'s promise that a ``Mapping`` in
      means a spec out. ``{"data": [row, row]}`` is likewise left alone, which
      is what lets ``items_path: "data"`` bind the array.

    Public because the caller must peel the payload it ships as
    ``SurfaceState.data`` with the *same* function that shaped the paths.
    """

    @classmethod
    def unwrap(cls, output: object) -> object:
        """Return ``output`` with its wrapper envelopes removed.

        Depth-capped (:data:`_Limits.UNWRAP_DEPTH`) in the style of
        :mod:`~agent_runtime.capabilities.surfaces.shape_hash`, so a
        self-similar payload cannot drive an unbounded walk. Iterative rather
        than recursive: the cap is then the only bound that matters, and there
        is no interpreter recursion limit to trip.
        """

        current = output
        for _ in range(_Limits.UNWRAP_DEPTH):
            peeled = cls._peel_once(current)
            if peeled is None:
                return current
            current = peeled
        return current

    @classmethod
    def _peel_once(cls, value: object) -> Mapping[object, object] | None:
        """One peel, or ``None`` when ``value`` is not a wrapper."""

        if not isinstance(value, Mapping):
            return None
        keys = _Shape.meaningful_keys(value)
        if len(keys) != 1:
            return None
        key = keys[0]
        if _KeyNamer.normalized(key) not in _Keys.WRAPPERS:
            return None
        inner = value[key]
        return inner if isinstance(inner, Mapping) else None


class _KeyNamer:
    """Everything that reads a key's *name*: normalising, grouping, labelling."""

    @classmethod
    def normalized(cls, key: str) -> str:
        """Lower-case, alphanumerics only — the form vocabularies are keyed by."""

        return _Patterns.NON_ALNUM.sub("", key.lower())

    @classmethod
    def tokens(cls, key: str) -> list[str]:
        """Split a key into lower-case words across camel, snake and kebab case."""

        return [match.lower() for match in _Patterns.WORD.findall(key)]

    @classmethod
    def classify(cls, key: str) -> tuple[_NameGroup, float]:
        """Classify ``key`` by whole-word match against the group vocabularies.

        Whole words, never substrings: ``estate`` must not read as ``state``,
        and ``candidate`` must not read as ``id``.
        """

        return _NameGroups.classify(set(cls.tokens(key)))

    @classmethod
    def label(cls, key: str) -> str | None:
        """Title-Case ``key`` for display, or ``None`` when it yields no words.

        ``display_name`` and ``displayName`` both become ``"Display Name"`` —
        the collision is real and handled by :class:`_LabelAllocator`, not by
        pretending the two spellings are different fields.
        """

        words = cls.tokens(key)
        if not words:
            return None
        rendered = " ".join(
            word.upper() if word in _Keys.ACRONYMS else word.capitalize()
            for word in words
        )
        return rendered[: _Limits.LABEL_MAX].strip() or None

    @classmethod
    def find(cls, source: Mapping[object, object], wanted: str) -> str | None:
        """Return the actual key in ``source`` matching ``wanted`` (normalised)."""

        for key in source:
            if _Shape.is_segment(key) and cls.normalized(key) == wanted:
                return key
        return None


class _LabelAllocator:
    """Hands out unique display labels, so two keys never collide silently.

    ``display_name`` and ``displayName`` Title-Case to the same string. The
    schema permits duplicate labels, so this is a legibility fix, not a
    validation one: two identically-headed columns are a bug report waiting to
    happen. Suffixing is bounded — at most :data:`_Limits.MAX_SLOTS` labels
    exist — and re-truncated so the schema's 40-char ceiling still holds.
    """

    def __init__(self) -> None:
        self._taken: set[str] = set()

    def allocate(self, base: str) -> str:
        if base not in self._taken:
            self._taken.add(base)
            return base
        for ordinal in range(2, _Limits.MAX_SLOTS + 2):
            suffix = f" {ordinal}"
            trimmed = base[: _Limits.LABEL_MAX - len(suffix)].strip()
            candidate = f"{trimmed}{suffix}"
            if candidate not in self._taken:
                self._taken.add(candidate)
                return candidate
        return base


class _ValueTyper:
    """Choose a :class:`SurfaceFieldFormat` from a key's group and its samples.

    Name beats sample wherever the name is decisive. A single-row payload gives
    a sample of one, from which no cardinality argument can be made — but
    ``state`` is a badge and ``updatedAt`` is a datetime on the first row as
    surely as on the two-hundredth. The sample only decides where the name says
    nothing.
    """

    @classmethod
    def resolve(
        cls,
        *,
        group: _NameGroup,
        values: Sequence[object],
        user_shaped: bool,
    ) -> SurfaceFieldFormat:
        if group is _NameGroup.PERSON or user_shaped:
            return SurfaceFieldFormat.USER
        if group is _NameGroup.IDENTITY:
            # An identifier is never a badge: its whole job is to be distinct,
            # and badging it would imply a category it does not have.
            return cls._numeric_or(values, SurfaceFieldFormat.TEXT)
        if group is _NameGroup.TITLE:
            return SurfaceFieldFormat.TEXT
        if group is _NameGroup.STATE:
            return (
                SurfaceFieldFormat.BADGE
                if cls._all_strings(values)
                else cls._numeric_or(values, SurfaceFieldFormat.TEXT)
            )
        if group is _NameGroup.TIME:
            if cls._all_iso_8601(values):
                return SurfaceFieldFormat.DATETIME
            return cls._numeric_or(values, SurfaceFieldFormat.TEXT)
        return cls._from_values(values)

    @classmethod
    def _from_values(cls, values: Sequence[object]) -> SurfaceFieldFormat:
        if not values:
            return SurfaceFieldFormat.TEXT
        if all(_Shape.is_number(value) for value in values):
            return SurfaceFieldFormat.NUMBER
        if cls._all_iso_8601(values):
            return SurfaceFieldFormat.DATETIME
        if cls._is_low_cardinality(values):
            return SurfaceFieldFormat.BADGE
        return SurfaceFieldFormat.TEXT

    @classmethod
    def _numeric_or(
        cls, values: Sequence[object], otherwise: SurfaceFieldFormat
    ) -> SurfaceFieldFormat:
        if values and all(_Shape.is_number(value) for value in values):
            return SurfaceFieldFormat.NUMBER
        return otherwise

    @staticmethod
    def _all_strings(values: Sequence[object]) -> bool:
        return bool(values) and all(isinstance(value, str) for value in values)

    @classmethod
    def _all_iso_8601(cls, values: Sequence[object]) -> bool:
        return cls._all_strings(values) and all(
            _Patterns.ISO_8601.fullmatch(value) is not None
            for value in values
            if isinstance(value, str)
        )

    @classmethod
    def _is_low_cardinality(cls, values: Sequence[object]) -> bool:
        """True when the values read as a small vocabulary of short tokens.

        Shape, not repetition: with one row there is nothing to repeat, yet
        ``"In Progress"`` is still a badge and ``"Fix the login redirect loop"``
        still is not. Prose is excluded by word count and length rather than by
        counting distinct values, which only works on long samples.
        """

        if not cls._all_strings(values):
            return False
        texts = [value for value in values if isinstance(value, str)]
        if len(set(texts)) > _Limits.BADGE_DISTINCT:
            return False
        return all(
            0 < len(text) <= _Limits.BADGE_VALUE_CHARS
            and len(text.split()) <= _Limits.BADGE_VALUE_WORDS
            for text in texts
        )


@dataclass(frozen=True)
class _Binding:
    """A key resolved to the dot-path a slot will actually read.

    ``path`` may descend into a nested mapping (``assignee.displayName``), which
    is why it is carried separately from the key the label and the name-group
    come from — a column headed by its container reads better than one headed
    ``Display Name``.
    """

    key: str
    path: str
    values: tuple[object, ...]
    present: int
    user_shaped: bool


@dataclass(frozen=True)
class _Slot:
    """A scored candidate column/field, before labels are allocated."""

    key: str
    path: str
    format: SurfaceFieldFormat
    score: float
    order: int


@dataclass(frozen=True)
class _Subject:
    """The array a table renders, and how strongly it claimed that role."""

    path: str
    rows: tuple[object, ...]
    score: float


class _SubjectFinder:
    """Locate the dominant array-of-mappings — the thing the payload is *about*.

    Ranked by ``row count × fraction of elements that are mappings`` (PRD §3.3
    step 2). Row count alone would let a three-element lookup table beat the
    result set; mapping-ness alone would let a one-row array beat a hundred-row
    one. The fraction gate is what keeps an array of scalars, or a mixed
    grab-bag, from becoming a table with no columns.

    Breadth-first and node-capped, so a wide-and-deep payload costs a bounded
    walk and shallower arrays win ties — the array nearest the root is the one a
    connector meant as its answer.
    """

    @classmethod
    def find(cls, root: Mapping[object, object]) -> _Subject | None:
        best: _Subject | None = None
        visited = 0
        queue: deque[tuple[Mapping[object, object], str, int]] = deque(
            [(root, "", 0)],
        )
        while queue and visited < _Limits.SUBJECT_NODES:
            node, prefix, depth = queue.popleft()
            visited += 1
            for key in node:
                if not _Shape.is_segment(key):
                    continue
                value = node[key]
                path = f"{prefix}{key}"
                if _Shape.is_array(value):
                    candidate = cls._score(path, value)
                    if candidate is not None and (
                        best is None or candidate.score > best.score
                    ):
                        best = candidate
                elif isinstance(value, Mapping) and depth + 1 < _Limits.SUBJECT_DEPTH:
                    queue.append((value, f"{path}.", depth + 1))
        return best

    @classmethod
    def _score(cls, path: str, value: Sequence[object]) -> _Subject | None:
        rows = tuple(value[: _Limits.ROWS_SCANNED])
        if not rows:
            return None
        fraction = sum(isinstance(row, Mapping) for row in rows) / len(rows)
        if fraction < _Limits.SUBJECT_MAPPING_FRACTION:
            return None
        return _Subject(path=path, rows=rows, score=len(value) * fraction)


class _SlotBuilder:
    """Rank a mapping's keys into the ≤6 slots a table or record renders.

    One implementation for both archetypes: a record is a table of one row. The
    score is the PRD's ``density × name_priority × type_signal``, where density
    is the fraction of rows carrying the key non-null — a column that is empty
    on four rows in five is worse than useless, it is misleading.
    """

    @classmethod
    def build(
        cls,
        rows: Sequence[object],
        *,
        prefix: str = "",
        exclude_path: str | None = None,
    ) -> tuple[_Slot, ...]:
        mapping_rows = [row for row in rows if isinstance(row, Mapping)]
        if not mapping_rows:
            return ()
        slots: list[_Slot] = []
        for order, key in enumerate(cls._candidate_keys(mapping_rows)):
            binding = cls._bind(key, mapping_rows, prefix=prefix)
            if binding is None or binding.present == 0:
                continue
            if exclude_path is not None and binding.path == exclude_path:
                continue
            group, weight = _KeyNamer.classify(binding.key)
            fmt = _ValueTyper.resolve(
                group=group,
                values=binding.values,
                user_shaped=binding.user_shaped,
            )
            density = binding.present / len(mapping_rows)
            score = density * weight * _FORMAT_WEIGHT[fmt]
            slots.append(
                _Slot(
                    key=binding.key,
                    path=binding.path,
                    format=fmt,
                    score=score,
                    order=order,
                )
            )
        slots.sort(key=lambda slot: (-slot.score, slot.order))
        return tuple(slots[: _Limits.MAX_SLOTS])

    @classmethod
    def _candidate_keys(cls, rows: Sequence[Mapping[object, object]]) -> list[str]:
        """Union of dot-path-safe keys across the rows, first-seen order.

        First-seen order is the deterministic tie-break for equal scores, and
        matching a connector's own field order tends to match what a person
        expects to read first. Non-string and non-identifier keys are dropped
        here rather than later: they cannot be addressed by a dot-path at all.
        """

        seen: dict[str, None] = {}
        for row in rows:
            for key in row:
                if _Shape.is_segment(key):
                    seen.setdefault(key, None)
                if len(seen) >= _Limits.CANDIDATE_KEYS:
                    return list(seen)
        return list(seen)

    @classmethod
    def _bind(
        cls,
        key: str,
        rows: Sequence[Mapping[object, object]],
        *,
        prefix: str,
    ) -> _Binding | None:
        """Resolve ``key`` to the path a slot reads, or ``None`` if unrenderable.

        Three cases. A scalar binds directly. A nested mapping binds through a
        display sub-key — ``assignee`` alone would render as a JSON blob, while
        ``assignee.displayName`` renders as a person. An array-valued key binds
        to nothing: a cell is not a place to put a collection, and the row that
        collection belongs to is a different surface.
        """

        nested = cls._first_mapping(key, rows)
        if nested is not None:
            return cls._bind_nested(key, rows, nested=nested, prefix=prefix)
        values: list[object] = []
        present = 0
        for row in rows:
            value = row.get(key)
            if value is None:
                continue
            if not _Shape.is_scalar(value):
                # An array (or anything else non-renderable) disqualifies the
                # key outright rather than binding a partly-broken column.
                return None
            present += 1
            if len(values) < _Limits.TYPE_SAMPLE:
                values.append(value)
        return _Binding(
            key=key,
            path=f"{prefix}{key}",
            values=tuple(values),
            present=present,
            user_shaped=False,
        )

    @classmethod
    def _bind_nested(
        cls,
        key: str,
        rows: Sequence[Mapping[object, object]],
        *,
        nested: Mapping[object, object],
        prefix: str,
    ) -> _Binding | None:
        subkey = cls._display_subkey(nested)
        if subkey is None:
            return None
        normalized = {
            _KeyNamer.normalized(name) for name in nested if isinstance(name, str)
        }
        values: list[object] = []
        present = 0
        for row in rows:
            container = row.get(key)
            if not isinstance(container, Mapping):
                continue
            value = container.get(subkey)
            if not _Shape.is_scalar(value):
                continue
            present += 1
            if len(values) < _Limits.TYPE_SAMPLE:
                values.append(value)
        return _Binding(
            key=key,
            path=f"{prefix}{key}.{subkey}",
            values=tuple(values),
            present=present,
            user_shaped=bool(normalized & _Keys.USER_MARKERS),
        )

    @staticmethod
    def _first_mapping(
        key: str, rows: Sequence[Mapping[object, object]]
    ) -> Mapping[object, object] | None:
        for row in rows:
            value = row.get(key)
            if isinstance(value, Mapping):
                return value
        return None

    @staticmethod
    def _display_subkey(nested: Mapping[object, object]) -> str | None:
        """The sub-key to bind through: a named display key, else any scalar.

        The named list is the quality path — ``assignee.displayName`` beats
        ``assignee.id``. The scalar fallback is the *floor* path: a connector
        that nests an object under a name none of our vocabularies knows would
        otherwise contribute no column, and a table whose columns all dropped
        out renders "No columns configured." — a fresh apology of exactly the
        kind this effort exists to delete. Showing one honest value from the
        object beats claiming there is nothing to show.
        """

        for wanted in _Keys.DISPLAY_SUBKEYS:
            found = _KeyNamer.find(nested, wanted)
            if found is not None and _Shape.is_scalar(nested[found]):
                return found
        for key in nested:
            if _Shape.is_segment(key) and _Shape.is_scalar(nested[key]):
                return key
        return None


class _RecordBody:
    """Descend past single-object wrappers to the mapping a record is about.

    ``{"issue": {...}}`` is not an envelope :class:`EnvelopeUnwrapper` will peel
    — ``issue`` is not a wrapper name, and peeling it would strip a segment the
    caller's ``data`` still has. But a record built from it has exactly one
    field, ``Issue``, which is no record at all. So the descent happens here and
    keeps the prefix: fields come out as ``issue.state.name``, exactly as the
    hand-authored ``linear.get_issue`` spec writes them.
    """

    @classmethod
    def locate(
        cls, root: Mapping[object, object]
    ) -> tuple[str, Mapping[object, object]]:
        prefix = ""
        body = root
        for _ in range(_Limits.UNWRAP_DEPTH):
            keys = _Shape.meaningful_keys(body)
            if len(keys) != 1:
                return prefix, body
            inner = body[keys[0]]
            if not isinstance(inner, Mapping):
                return prefix, body
            prefix = f"{prefix}{keys[0]}."
            body = inner
        return prefix, body


class _TitleFinder:
    """Pick ``title_path``: the first title-shaped key, root then one level in.

    The nested pass is what recovers the headline a table needs, since a table's
    title lives *outside* its rows — ``{"team": {"name": "Core"}, "issues": [...]}``
    yields ``team.name``, which is what the curated Linear spec says by hand.
    """

    @classmethod
    def find(cls, body: Mapping[object, object], *, prefix: str = "") -> str:
        direct = cls._probe(body)
        if direct is not None:
            return f"{prefix}{direct}"
        for key in body:
            if not _Shape.is_segment(key):
                continue
            value = body[key]
            if not isinstance(value, Mapping):
                continue
            nested = cls._probe(value)
            if nested is not None:
                return f"{prefix}{key}.{nested}"
        return f"{prefix}{_Keys.FALLBACK_TITLE}"

    @staticmethod
    def _probe(source: Mapping[object, object]) -> str | None:
        for wanted in _Keys.TITLE_PROBE:
            found = _KeyNamer.find(source, wanted)
            if found is not None and _Shape.is_scalar(source[found]):
                return found
        return None


class SurfaceSpecInferrer:
    """The floor: any mapping in, a real :class:`SurfaceSpec` out.

    The whole ladder above this — curated specs, shape matches, model
    refinement, the learned cache — is an *upgrade path*. This rung is the one
    that must never be unavailable, so it takes no dependencies it could be
    denied: no credential, no store, no network, no clock.

    ``infer`` returns ``None`` only for a non-``Mapping`` input, which has no
    surface to render in the first place (the same short-circuit
    :class:`~agent_runtime.capabilities.surfaces.projector.SurfaceProjector`
    already makes). For every mapping — empty, hostile, or a megabyte deep — it
    returns a spec that validates.
    """

    @classmethod
    def infer(
        cls, output: object, *, source: SurfaceSource | None = None
    ) -> SurfaceSpec | None:
        """Infer a spec for ``output``, or ``None`` when it is not a mapping.

        ``source`` is the connector provenance stamped onto the spec. It is
        keyword-only with a default so the pinned call form ``infer(output)``
        is exactly what it says; a caller that knows the ``(server, tool)`` pair
        should pass it, because ``SurfaceSpec.source`` is required by the schema
        and the renderer falls back to reading it.
        """

        if not isinstance(output, Mapping):
            return None
        resolved = source if isinstance(source, SurfaceSource) else INFERRED_SOURCE
        try:
            return cls._build(output, resolved)
        except Exception:  # noqa: BLE001 - see below
            # The floor may not have a failure mode. Every branch above is
            # written to be total, but this module's promise is stronger than
            # "we believe it is total": an exotic Mapping implementation, or a
            # future edit, must still yield a surface rather than resurrect the
            # empty-canvas defect this whole effort exists to delete. The
            # minimal spec always validates.
            return cls._minimal(resolved)

    @classmethod
    def _build(
        cls, output: Mapping[object, object], source: SurfaceSource
    ) -> SurfaceSpec:
        unwrapped = EnvelopeUnwrapper.unwrap(output)
        root: Mapping[object, object] = (
            unwrapped if isinstance(unwrapped, Mapping) else output
        )
        subject = _SubjectFinder.find(root)
        if subject is not None:
            return cls._table(root, subject, source)
        return cls._record(root, source)

    @classmethod
    def _table(
        cls,
        root: Mapping[object, object],
        subject: _Subject,
        source: SurfaceSource,
    ) -> SurfaceSpec:
        """A table over ``subject``, or a record when no column could be bound.

        The fallback is not tidiness. ``TableRenderer`` draws a column-less spec
        as "No columns configured." — a brand-new apology in the exact register
        the PRD is deleting, and a *worse* outcome than the record view because
        the table also promises rows it then cannot show. If the subject's rows
        yield nothing addressable, the honest answer is that this payload is not
        table-shaped.
        """

        columns = cls._render_slots(_SlotBuilder.build(subject.rows), aligned=True)
        if not columns:
            return cls._record(root, source)
        raw = cls._envelope(SurfaceArchetype.TABLE, source)
        raw["title_path"] = _TitleFinder.find(root)
        raw["items_path"] = subject.path
        raw["columns"] = columns
        return validate_surface_spec(raw)

    @classmethod
    def _record(
        cls, root: Mapping[object, object], source: SurfaceSource
    ) -> SurfaceSpec:
        prefix, body = _RecordBody.locate(root)
        title_path = _TitleFinder.find(body, prefix=prefix)
        raw = cls._envelope(SurfaceArchetype.RECORD, source)
        raw["title_path"] = title_path
        fields = cls._render_slots(
            _SlotBuilder.build([body], prefix=prefix, exclude_path=title_path),
            aligned=False,
        )
        if fields:
            raw["fields"] = fields
        return validate_surface_spec(raw)

    @classmethod
    def _minimal(cls, source: SurfaceSource) -> SurfaceSpec:
        """The smallest spec the schema accepts: a record with a bare headline."""

        raw = cls._envelope(SurfaceArchetype.RECORD, source)
        raw["title_path"] = _Keys.FALLBACK_TITLE
        return validate_surface_spec(raw)

    @staticmethod
    def _envelope(
        archetype: SurfaceArchetype, source: SurfaceSource
    ) -> dict[str, object]:
        return {
            "spec_version": 1,
            "archetype": archetype.value,
            "source": {"server": source.server, "tool": source.tool},
        }

    @staticmethod
    def _render_slots(
        slots: Sequence[_Slot], *, aligned: bool
    ) -> list[dict[str, object]]:
        """Turn scored slots into schema dicts, allocating unique labels.

        ``aligned`` is the one difference between a column and a field: numbers
        right-align in a table so digits line up, and a record has no column to
        align within. Any slot whose key yields no label at all is dropped —
        a label is required, and inventing one would name a field nothing.
        """

        allocator = _LabelAllocator()
        rendered: list[dict[str, object]] = []
        for slot in slots:
            label = _KeyNamer.label(slot.key)
            if label is None:
                continue
            entry: dict[str, object] = {
                "label": allocator.allocate(label),
                "path": slot.path,
                "format": slot.format.value,
            }
            if aligned and slot.format is SurfaceFieldFormat.NUMBER:
                entry["align"] = ColumnAlign.END.value
            rendered.append(entry)
        return rendered


__all__ = [
    "INFERRED_SOURCE",
    "EnvelopeUnwrapper",
    "SurfaceSpecInferrer",
]
