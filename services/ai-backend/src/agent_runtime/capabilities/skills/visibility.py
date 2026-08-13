"""Conditional Skill visibility and the bounded index the prompt actually carries.

Before this module the model-visible Skill set was ``card.enabled`` and nothing
else, so every enabled Skill cost system-prompt tokens on *every* run and the
library could not grow past a handful without a linear token tax. Two mechanisms
remove that tax, and they are deliberately different from each other:

**Conditional visibility** — a Skill declares, in its own frontmatter, the run
shape it is for. ``requires_connectors: linear`` means "there is no point
offering me to a run with no Linear connection"; ``fallback_for_tools: web_search``
means "offer me only when the better tool is absent". The declaration travels as
ordinary frontmatter metadata (``SkillManifestParser`` folds unknown top-level
scalars into ``metadata``), so an author needs no new file and no new API. A
Skill that declares nothing is unconditional and always visible — the whole
mechanism is opt-in and the no-declaration path is byte-identical to before.

**A bound** — even after filtering, the index is a *tier*, not the library. It
is capped at :data:`Limits.SKILL_INDEX_MAX_ENTRIES` rows and
:data:`Limits.SKILL_INDEX_MAX_CHARS` characters, and everything past the cap is
reported as a count with the tool that reaches it. This is the one number that
makes a 70-Skill library cost the same prompt as a 7-Skill one.

Why this is not the MCP filesystem catalog
------------------------------------------
``capabilities/mcp/catalog.py`` solves a visibly similar problem — a 70 KB
descriptor became ``/mcp/<server>/`` with a bounded ``SERVER.md`` index and one
file per tool — and the obvious move was to mirror it as ``/skills/``. We took
the *shape* and not the *substrate*, for two reasons:

1. The catalog had to invent a fetch primitive because MCP descriptors arrive as
   one newline-free blob with no addressable parts; ``ls`` / ``grep`` /
   ``read_file`` were the only navigation the model had. A Skill body is already
   an addressable document behind a stable name, and the on-demand fetch already
   exists and is already wired: ``load_skill`` (``skills/middleware.py``).
   Materializing a second retrieval path for a document that has one is how a
   codebase ends up with two.
2. ``/skills/`` is not free real estate — deepagents already mounts the
   configured skill roots (``RuntimeHarness.skill_directories``), so a virtual
   route of the same name would shadow real files.

What we *did* take from the catalog is the part that generalises: an
always-loaded index tier that is bounded in bytes, one clipped summary line per
entry, and an explicit "here is how to get the rest" footer instead of silent
truncation. :class:`SkillIndexPlanner` is that renderer.

Tool and connector names are treated as **opaque strings** compared by exact
membership. Nothing here parses a name's shape, so a naming-scheme change
elsewhere in the runtime cannot silently change which Skills are visible.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from agent_runtime.capabilities.skills.constants import Keys, Limits, Messages
from agent_runtime.execution.contracts import RuntimeContract


def text_attribute(source: object, attribute: str) -> str:
    """Return a stripped string attribute of ``source``, or ``""``.

    Cards reach this module either as :class:`VirtualSkillCard` instances or as
    whatever a custom provider returned, so every read is by attribute name and
    every non-string is treated as absent.
    """

    value = getattr(source, attribute, None)
    return value.strip() if isinstance(value, str) else ""


def skill_name_of(card: object) -> str:
    """Return a card's stable name, falling back to its repr for diagnostics."""

    return text_attribute(card, Keys.Fields.NAME) or str(card)


class SkillVisibilityConditions(RuntimeContract):
    """When a Skill is worth spending prompt tokens on, as its author declared it.

    Empty on every axis means unconditional. ``requires_*`` hides the Skill when
    the named capability is absent; ``fallback_for_*`` hides it when the named
    capability is *present* (the Skill exists to cover its absence).
    """

    requires_tools: tuple[str, ...] = ()
    requires_connectors: tuple[str, ...] = ()
    fallback_for_tools: tuple[str, ...] = ()
    fallback_for_connectors: tuple[str, ...] = ()

    @property
    def is_unconditional(self) -> bool:
        """Return ``True`` when the Skill declared no conditions at all."""
        return not (
            self.requires_tools
            or self.requires_connectors
            or self.fallback_for_tools
            or self.fallback_for_connectors
        )

    @classmethod
    def from_metadata(
        cls, metadata: Mapping[str, object] | None
    ) -> "SkillVisibilityConditions":
        """Read conditions out of a Skill's frontmatter metadata mapping.

        Frontmatter metadata is JSON-scalar-only by contract
        (:class:`SkillManifest`), so the wire form of a list is a
        comma-or-whitespace separated string. A real sequence is accepted too so
        a provider that carries richer metadata needs no special case. Anything
        unparseable contributes nothing rather than raising: a malformed
        condition must not take a run down, and an empty condition set is the
        safe direction (the Skill stays visible).
        """

        if not metadata:
            return cls()
        return cls(
            requires_tools=cls._names(metadata.get(Keys.Conditions.REQUIRES_TOOLS)),
            requires_connectors=cls._names(
                metadata.get(Keys.Conditions.REQUIRES_CONNECTORS)
            ),
            fallback_for_tools=cls._names(
                metadata.get(Keys.Conditions.FALLBACK_FOR_TOOLS)
            ),
            fallback_for_connectors=cls._names(
                metadata.get(Keys.Conditions.FALLBACK_FOR_CONNECTORS)
            ),
        )

    def is_satisfied_by(self, context: "SkillVisibilityContext") -> bool:
        """Return ``True`` when this run should offer the Skill."""

        if self.is_unconditional or not context.resolved:
            return True
        for tool in self.fallback_for_tools:
            if tool in context.tool_names:
                return False
        for connector in self.fallback_for_connectors:
            if connector in context.connector_slugs:
                return False
        for tool in self.requires_tools:
            if tool not in context.tool_names:
                return False
        for connector in self.requires_connectors:
            if connector not in context.connector_slugs:
                return False
        return True

    @classmethod
    def _names(cls, raw: object) -> tuple[str, ...]:
        """Coerce a scalar or sequence declaration to a de-duplicated name tuple."""

        if raw is None or isinstance(raw, bool):
            return ()
        items: Iterable[object]
        if isinstance(raw, str):
            items = raw.replace(",", " ").split()
        elif isinstance(raw, Sequence):
            items = raw
        else:
            return ()
        seen: dict[str, None] = {}
        for item in items:
            if not isinstance(item, str):
                continue
            name = item.strip()
            if name:
                seen.setdefault(name, None)
        return tuple(seen)


class SkillVisibilityContext(RuntimeContract):
    """The run-shape facts a Skill's conditions are evaluated against.

    ``resolved=False`` is the Hermes back-compat position: a caller that could
    not determine the run's tools or connectors filters nothing rather than
    guessing, so an unwired call site degrades to today's behaviour instead of
    silently hiding the user's Skills.
    """

    tool_names: frozenset[str] = frozenset()
    connector_slugs: frozenset[str] = frozenset()
    resolved: bool = True

    @classmethod
    def unresolved(cls) -> "SkillVisibilityContext":
        """Return the context that disables conditional filtering."""
        return cls(resolved=False)

    @classmethod
    def of(
        cls,
        *,
        tools: Iterable[object] = (),
        connectors: Iterable[object] = (),
    ) -> "SkillVisibilityContext":
        """Project model-visible tools and authorized connector cards to a context.

        Both inputs are read through ``getattr`` because the caller holds
        framework objects (LangChain tools, MCP cards), and both names are kept
        verbatim — this module never inspects a name's shape.
        """

        tool_names = {
            name
            for name in (text_attribute(tool, Keys.Fields.NAME) for tool in tools)
            if name
        }
        connector_slugs: set[str] = set()
        for connector in connectors:
            for attribute in (Keys.Fields.NAME, Keys.Fields.CONNECTOR_SLUG):
                slug = text_attribute(connector, attribute)
                if slug:
                    connector_slugs.add(slug)
        return cls(
            tool_names=frozenset(tool_names),
            connector_slugs=frozenset(connector_slugs),
            resolved=True,
        )


class SkillIndexPlan(RuntimeContract):
    """What the assembled prompt will carry, and what it deliberately will not.

    Every Skill the run knows about lands in exactly one of the three name
    tuples, which is what makes the usage sidecar able to distinguish
    "offered and ignored" from "never offered".
    """

    rows: tuple[str, ...] = ()
    surfaced: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()
    hidden: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Return ``True`` when no Skill row will be rendered."""
        return not self.rows


class SkillIndexPlanner:
    """Applies the conditional filter, then the bound, then renders the rows."""

    #: Rendered once per row; mirrors the pre-existing card line so a run with
    #: few, unconditional Skills produces the same prompt text it always did.
    ROW: str = "- {name} ({display_name}, path={virtual_path}{allowed}): {summary}"
    ALLOWED: str = ", allowed_tools={tools}"

    @classmethod
    def plan(
        cls,
        *,
        cards: Sequence[object],
        visibility: SkillVisibilityContext | None = None,
        max_entries: int = Limits.SKILL_INDEX_MAX_ENTRIES,
        max_chars: int = Limits.SKILL_INDEX_MAX_CHARS,
    ) -> SkillIndexPlan:
        """Return the bounded, condition-filtered index for ``cards``."""

        context = visibility or SkillVisibilityContext.unresolved()
        eligible: list[object] = []
        hidden: list[str] = []
        for card in cards:
            if cls.conditions_of(card).is_satisfied_by(context):
                eligible.append(card)
            else:
                hidden.append(skill_name_of(card))

        rows: list[str] = []
        surfaced: list[str] = []
        deferred: list[str] = []
        budget = max_chars
        for card in eligible:
            name = skill_name_of(card)
            if len(rows) >= max_entries:
                deferred.append(name)
                continue
            row = cls.row(card)
            if len(row) + 1 > budget:
                deferred.append(name)
                continue
            budget -= len(row) + 1
            rows.append(row)
            surfaced.append(name)
        if deferred:
            rows.append(Messages.Index.deferred_footer(len(deferred)))
        return SkillIndexPlan(
            rows=tuple(rows),
            surfaced=tuple(surfaced),
            deferred=tuple(deferred),
            hidden=tuple(hidden),
        )

    @classmethod
    def conditions_of(cls, card: object) -> SkillVisibilityConditions:
        """Read a card's declared conditions from its frontmatter metadata."""

        metadata = getattr(card, "metadata", None)
        if not isinstance(metadata, Mapping):
            return SkillVisibilityConditions()
        return SkillVisibilityConditions.from_metadata(metadata)

    @classmethod
    def row(cls, card: object) -> str:
        """Render one bounded index row for ``card``."""

        name = skill_name_of(card)
        allowed_tools = tuple(getattr(card, Keys.Fields.ALLOWED_TOOLS, ()) or ())
        return cls.ROW.format(
            name=name,
            display_name=text_attribute(card, Keys.Fields.DISPLAY_NAME) or name,
            virtual_path=text_attribute(card, Keys.Fields.VIRTUAL_PATH),
            allowed=(
                cls.ALLOWED.format(tools=Keys.Characters.COMMA.join(allowed_tools))
                if allowed_tools
                else ""
            ),
            summary=cls.clip(text_attribute(card, Keys.Fields.DESCRIPTION)),
        )

    @classmethod
    def clip(
        cls, summary: str, *, max_chars: int = Limits.SKILL_INDEX_SUMMARY_MAX_CHARS
    ) -> str:
        """Clip one description to the per-row summary budget.

        A Skill description is already capped at
        :data:`Limits.SKILL_DESCRIPTION_MAX_LENGTH` by the manifest, but the
        card arrives over HTTP from another service — the index bound cannot
        depend on a remote validator having run.
        """

        if len(summary) <= max_chars:
            return summary
        return summary[: max_chars - 1].rstrip() + Messages.Index.ELLIPSIS
