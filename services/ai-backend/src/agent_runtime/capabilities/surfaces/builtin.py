"""Builtin curated SurfaceSpec library (generative-UI PRD-02).

Ships one hand-authored ``SurfaceSpec`` per ``(server, tool)`` for the catalog
connectors whose output shapes are stable enough to bind at dev time. Every
JSON file under ``builtin_specs/`` is loaded **and validated** exactly once at
import via :func:`validate_surface_spec`; a malformed or schema-violating file
raises :class:`BuiltinSpecError` (naming the file) so a bad builtin fails the
test suite rather than degrading a live run.

This is the first rung of the spec-acquisition ladder (plan D4):
``builtin → store → generate-async``. Only the builtin rung lives here; the
store port + generator arrive in PRD-07/08.

The floor PRD §3.4 gives the same twelve files a **second** job. Exact
``(server, tool)`` lookup is brittle in a way the audit measured: Linear's real
create tool is ``save_issue`` and not the catalogued ``create_issue``,
``list_my_issues`` misses ``list_issues``, and a server the user added by URL is
named after its host and misses every entry. So each curated spec is also read
as a **shape template** — the set of paths it binds — and
:func:`match_by_shape` reuses one for a payload that supplies those paths under
any name. See :class:`ShapeTemplate` for why that is thresholded hard.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable

from agent_runtime.capabilities.surfaces.shape_hash import (
    SHAPE_MATCH_MIN_COVERAGE,
    SHAPE_MATCH_MIN_TEMPLATE_PATHS,
    ShapeSkeleton,
)
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceArchetype,
    SurfaceSpec,
    SurfaceSpecError,
    validate_surface_spec,
)

_SPECS_DIR_NAME = "builtin_specs"
_SPEC_SUFFIX = ".json"

# Archetypes whose column/group paths are relative to a row inside ``items_path``
# rather than to the payload root. Everything else binds absolute paths.
_COLLECTION_ARCHETYPES: frozenset[SurfaceArchetype] = frozenset(
    {SurfaceArchetype.TABLE, SurfaceArchetype.BOARD}
)


class BuiltinSpecError(RuntimeError):
    """Raised at import when a builtin spec file is malformed or invalid.

    The message always names the offending file so a failing test points
    straight at the fixture to fix.
    """


def server_slug(raw: str) -> str:
    """Reduce a server name/id to the stable connector slug used for lookup.

    Strips a catalog prefix (``seed:linear`` → ``linear``) and collapses any
    non-``[a-z0-9]`` run to a single dash. Both the builtin index keys and the
    runtime ``server_name`` queries pass through here, so ``"seed:linear"`` in a
    spec's ``source.server`` and a live ``"linear"`` call resolve to the same
    entry, and the surface URI's server segment is stable regardless of naming.
    """

    text = raw.strip().lower()
    if ":" in text:
        text = text.rsplit(":", 1)[1]
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def tool_slug(raw: str) -> str:
    """Normalise a tool name for lookup (case-insensitive; no prefix stripping)."""

    return raw.strip().lower()


def display_name(raw: object) -> str:
    """The connector/tool name as the source declared it — the DISPLAY register.

    Sibling of, and deliberately not, :func:`server_slug` / :func:`tool_slug`.
    Those two answer "which entry does this key resolve to" and are lossy on
    purpose (``Get_Issue`` and ``get_issue`` MUST be one registry entry). This
    one answers "what is this thing called", and losing case there is pure
    damage: ``getIssue`` slugs to ``getissue``, which names no tool the user has
    ever seen. A name that reaches a person goes through here; a name that
    reaches a lookup goes through the slugs.

    Note what this can and cannot fix. It guarantees the surface layer never
    re-spells a name it was given; it cannot restore a name that was already
    folded upstream. Today's MCP path folds it early — ``McpToolCallRequest``
    and ``McpToolDescriptor`` both run every server/tool name through
    ``normalize_slug``, so a connector's ``getJiraIssue`` is ``getjiraissue``
    before any surface code sees it, and this function faithfully serves that.
    Recovering the connector's own spelling means carrying it from the MCP
    descriptor (its ``annotations.title``, currently dropped as untrusted), not
    changing anything here.

    Total over ``object`` because its callers sit on the tool-call path where a
    raise would fail the call: a non-string is no name at all, and the empty
    string it returns is what the provenance sites already treat as "unknown
    tool".
    """

    return raw.strip() if isinstance(raw, str) else ""


def _spec_key(server: str, tool: str) -> tuple[str, str]:
    return (server_slug(server), tool_slug(tool))


def load_builtin_specs(
    directory: Traversable,
) -> dict[tuple[str, str], SurfaceSpec]:
    """Load + validate every ``*.json`` under ``directory`` into a lookup index.

    Pure and re-runnable so a test can point it at a temp dir carrying a
    deliberately corrupt fixture and assert the raised message names the file.
    Raises :class:`BuiltinSpecError` on invalid JSON, a schema/model violation,
    or a duplicate ``(server, tool)`` key.
    """

    registry: dict[tuple[str, str], SurfaceSpec] = {}
    entries = sorted(
        (entry for entry in directory.iterdir() if entry.name.endswith(_SPEC_SUFFIX)),
        key=lambda entry: entry.name,
    )
    for entry in entries:
        name = entry.name
        try:
            raw = json.loads(entry.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BuiltinSpecError(f"{name}: invalid JSON — {exc}") from exc
        try:
            spec = validate_surface_spec(raw)
        except SurfaceSpecError as exc:
            raise BuiltinSpecError(f"{name}: {exc}") from exc
        key = _spec_key(spec.source.server, spec.source.tool)
        if key in registry:
            raise BuiltinSpecError(
                f"{name}: duplicate builtin spec for server={key[0]!r} tool={key[1]!r}"
            )
        registry[key] = spec
    return registry


@dataclass(frozen=True)
class ShapeTemplate:
    """A curated spec read as a set of structural requirements (floor PRD §3.4).

    ``paths`` is every payload path the spec binds, expanded into the payload's
    absolute path space: a table's column paths are row-relative, so
    ``items_path="issues"`` + ``path="state.name"`` becomes
    ``issues[].state.name`` — the notation :class:`ShapeSkeleton` speaks.

    ``anchor`` is the collection marker (``issues[]``) for a table or board, and
    it is a **precondition rather than evidence**: if the payload has no list
    there, the spec draws an empty table no matter how many column paths
    coincidentally line up elsewhere. Scoring it would let a payload buy back
    the very miss that makes the template unusable, so it is checked separately
    and kept out of the denominator.

    ``link.url_path`` is deliberately excluded. It is decoration — a missing
    link costs one button, not the render — and its base differs by archetype
    (row-relative for ``github.list_issues``, absolute for ``github.get_issue``),
    so admitting it would put a guess inside the evidence.
    """

    spec: SurfaceSpec
    anchor: str | None
    paths: frozenset[str]

    @classmethod
    def from_spec(cls, spec: SurfaceSpec) -> "ShapeTemplate":
        """Derive the template a curated spec implies."""

        collection = (
            spec.archetype in _COLLECTION_ARCHETYPES and spec.items_path is not None
        )
        marker = (
            f"{spec.items_path}{ShapeSkeleton.COLLECTION_MARKER}"
            if collection
            else None
        )
        row_prefix = f"{marker}{ShapeSkeleton.SEPARATOR}" if marker else ""
        paths = {spec.title_path}
        if spec.subtitle_path:
            paths.add(spec.subtitle_path)
        for field in spec.fields or ():
            paths.add(field.path)
        for column in spec.columns or ():
            paths.add(f"{row_prefix}{column.path}")
        if spec.group_by_path:
            paths.add(f"{row_prefix}{spec.group_by_path}")
        return cls(spec=spec, anchor=marker, paths=frozenset(paths))

    @property
    def is_usable(self) -> bool:
        """Whether this template binds enough paths to be evidence of anything."""

        return len(self.paths) >= SHAPE_MATCH_MIN_TEMPLATE_PATHS

    def score(self, skeleton: ShapeSkeleton) -> float:
        """Coverage of this template's paths by ``skeleton``; 0.0 if the anchor misses."""

        if self.anchor is not None and not skeleton.has(self.anchor):
            return 0.0
        return skeleton.coverage_of(self.paths)


class ShapeTemplateIndex:
    """Nearest-neighbour lookup over the curated specs, keyed by shape.

    Built once at import from the same registry exact lookup reads, so the two
    rungs can never disagree about what "curated" means. Matching is total and
    deterministic: no payload can raise, and ties resolve by a fixed order
    rather than by whatever order the spec directory happened to iterate in.
    """

    def __init__(self, specs: tuple[SurfaceSpec, ...]) -> None:
        self._templates: tuple[ShapeTemplate, ...] = tuple(
            template
            for template in (ShapeTemplate.from_spec(spec) for spec in specs)
            if template.is_usable
        )

    @property
    def templates(self) -> tuple[ShapeTemplate, ...]:
        """The usable templates, for tests and introspection."""

        return self._templates

    def match(self, output: object) -> SurfaceSpec | None:
        """Return the best curated spec for ``output``'s shape, or ``None``.

        ``None`` whenever nothing clears :data:`SHAPE_MATCH_MIN_COVERAGE` — the
        caller then falls through to rung 0, which is the *correct* outcome for
        an unrecognised payload, not a degraded one.
        """

        skeleton = ShapeSkeleton.of(output)
        ranked = [
            (
                # Descending on score, then on how much evidence the template
                # asked for (a six-path template clearing the bar says more than
                # a three-path one); ascending on name purely for determinism.
                (-score, -len(template.paths), template.spec.source.server),
                template.spec,
            )
            for template in self._templates
            if (score := template.score(skeleton)) >= SHAPE_MATCH_MIN_COVERAGE
        ]
        if not ranked:
            return None
        ranked.sort(key=lambda entry: entry[0])
        return ranked[0][1]


def _default_specs_dir() -> Traversable:
    return files(__package__).joinpath(_SPECS_DIR_NAME)


# Loaded once at import. A bad builtin file raises here → collected as a test
# failure (the package fails to import), never a silent runtime degradation.
_REGISTRY: dict[tuple[str, str], SurfaceSpec] = load_builtin_specs(_default_specs_dir())

# The same twelve specs, indexed by shape rather than by name.
_TEMPLATES = ShapeTemplateIndex(tuple(_REGISTRY.values()))


def lookup(server: str, tool: str) -> SurfaceSpec | None:
    """Return the builtin spec for ``(server, tool)``, or ``None`` if uncurated."""

    return _REGISTRY.get(_spec_key(server, tool))


def match_by_shape(output: object) -> SurfaceSpec | None:
    """Return the curated spec whose bound paths ``output`` supplies, or ``None``.

    The shape-matched rung. A hit is **not** an exact registry hit and must not
    be reported as one — the projector stamps
    :data:`~agent_runtime.capabilities.surfaces.spec_models.SurfaceSpecRung.SHAPE_MATCH`
    so the ledger records that a human curated the *spec* but never vouched for
    this connector.
    """

    return _TEMPLATES.match(output)


def all_specs() -> tuple[SurfaceSpec, ...]:
    """Return every loaded builtin spec (test/introspection helper)."""

    return tuple(_REGISTRY.values())


__all__ = [
    "BuiltinSpecError",
    "ShapeTemplate",
    "ShapeTemplateIndex",
    "all_specs",
    "display_name",
    "load_builtin_specs",
    "lookup",
    "match_by_shape",
    "server_slug",
    "tool_slug",
]
