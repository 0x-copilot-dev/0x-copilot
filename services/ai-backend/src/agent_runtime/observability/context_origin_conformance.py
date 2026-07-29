"""Read-only AST proof that everything occupying the model's context declares it.

§4.2 of the Context Occupancy Ledger design is blunt about why this module
exists: *declaration without a gate is a comment*.
:mod:`agent_runtime.observability.context_origin` gives every contributor a way
to say what it puts in front of the model, and
:class:`~agent_runtime.execution.tool_surface.ModelToolDeclaration` puts that
declaration at the one place model tools are composed — but neither can make a
future author use it. This sweep is what transfers the responsibility: after it
lands, a contributor who adds something to the model's context **without
declaring an origin cannot merge**, and the failure names the thing they added.

It is modelled directly on
:mod:`agent_runtime.observability.llm_seam_conformance` and shares its two
properties. It parses the source tree and imports nothing from it, so it
measures the code as written rather than as some import order happened to
resolve it; and every string it emits uses *lexical scope names* rather than
line numbers, so reformatting the factory does not churn the pinned inventory
while adding a contributor still does.

**What is swept.** Exactly the two places first-party text reaches the provider
request from this repository:

1. The tool block — every expression composed into ``model_tools`` inside
   ``factory._model_visible_tools``. That the surface has only one composition
   path is not assumed here: ``canonical_agent_topology_present`` already proves
   ``DeepAgentBuildRequest`` is constructed exactly once, in this same file, and
   ``llm_seam_violations`` proves ``build_deep_agent`` is called from nowhere
   else.
2. The system block — every :class:`PromptSourceMaterial` handed to
   ``PromptAssemblyInputs`` in that same file. A source's declaration is the
   join of two halves that already exist (§4.1): the ``source_owner`` literal at
   the composition site, and the ``fragment_id`` its registered provider in
   ``prompts/sources.py`` allocates. The gate proves both halves are present and
   statically readable, which is what makes the runtime label
   ``owner:fragment_id`` a projection rather than a new registry. Assembly
   *elsewhere* is refused rather than ignored, so the one-file scope cannot be
   escaped by moving the call — a gate that goes quiet when its subject moves
   is worse than no gate, because silence reads as success.

**What the runtime does instead.** Nothing here runs on the model-call path and
nothing here is mirrored by a runtime raise. Open question §10.1 was decided the
way §6.4 demands: the AST gate hard-fails CI, and an undeclared contributor at
runtime is recorded as ``UNDECLARED`` and counted into ``undeclared_tokens``. A
run is never taken down over an observability concern.

**Known static limits, named rather than allowlisted.** Two things this sweep
deliberately cannot prove, both of which degrade to the runtime ``UNDECLARED``
path rather than to a silent exemption:

- *A tool's declared ``name`` is its own ``.name`` attribute*, resolved when the
  tool is built. The inventory therefore records the **declaration site's
  symbol** as the label's ``name`` half — ``publish_artifact_tool``,
  ``LoadMcpServerTool`` — not the string the model will call. That is the symbol
  a reviewer sees in the diff anyway, and two sites are forbidden from
  collapsing onto one label (see :meth:`ContextOriginGate.duplicates`), so the
  substitution can never cost the gate its resolution.
- *``PromptSource.OPERATION_TOOL_PROTOCOL`` and ``PromptSource.APPROVAL_RUN_STATE``
  have a registered provider but no producer anywhere in the tree*, and
  ``prompts.sources.task_policy_progress_material`` builds a material that no
  call site passes. They contribute zero bytes today, so they carry no
  ``owner:name`` inventory entry — there is no owner to read. The moment
  somebody wires one up, its keyword lands in ``PromptAssemblyInputs`` and this
  sweep demands a literal ``source_owner`` before it will pass, and the pinned
  inventory demands a review before it will merge.

The ``deepagents`` constants (§4.3) are intentionally out of scope: they are
declared on the library's behalf by
:mod:`agent_runtime.observability.context_third_party` and pinned by its own
golden fixture, which is where a dependency bump is supposed to fail.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final


CONTEXT_COMPOSITION_SITE: Final = Path("agent_runtime/execution/factory.py")
"""The one file that composes the model tool surface and the system prompt."""

PROMPT_SOURCE_REGISTRY: Final = Path("agent_runtime/prompts/sources.py")
"""Where ``PromptSource`` members are allocated their ``fragment_id``."""

MODEL_TOOL_OWNER_REGISTRY: Final = Path("agent_runtime/execution/tool_surface.py")
"""Where ``ModelToolOwner`` members are bound to their dotted owner strings."""


class Keys:
    """Every source identifier this gate matches on, spelled exactly once.

    The sweep is a string-matching exercise against a live codebase, which is
    the situation where an inline literal repeated in three predicates silently
    stops matching after a rename. Naming them here means a rename breaks
    compilation of one constant rather than quietly disarming the gate.
    """

    class Callable:
        """Call targets the sweep recognizes, by their simple name."""

        DECLARE_CONTEXT_ORIGIN = "declare_context_origin"
        DECLARED = "declared"
        DECLARED_ALL = "declared_all"
        CONTEXT_ORIGIN = "ContextOrigin"
        PROMPT_ASSEMBLY_INPUTS = "PromptAssemblyInputs"
        PROMPT_SOURCE_MATERIAL = "PromptSourceMaterial"
        FACTORY_SOURCE_MATERIAL = "_prompt_source_material"
        REGISTERED_PROVIDER = "RegisteredPromptFragmentProvider"
        STRUCTURED_TOOL = "_structured_tool"
        SHADOW_WRAPPER = "wrap_model_tool_for_shadow"
        LIST = "list"
        TUPLE = "tuple"

    class Keyword:
        """Keyword arguments the sweep reads a declaration out of."""

        OWNER = "owner"
        NAME = "name"
        SOURCE_OWNER = "source_owner"
        SOURCE = "source"
        FRAGMENT_ID = "fragment_id"
        CONTEXT = "context"

    class Symbol:
        """Names of the declarations, containers, and enums being proved."""

        MODEL_TOOLS = "model_tools"
        MODEL_TOOL_SURFACE = "_model_visible_tools"
        MODEL_TOOL_OWNER = "ModelToolOwner"
        PROMPT_SOURCE = "PromptSource"
        APPEND = "append"
        EXTEND = "extend"


class AstNode:
    """The handful of AST reads all three sweeps below perform identically.

    Deliberately the same shape as ``llm_seam_conformance``'s private helpers —
    a callable's simple name, a parent map, and a lexical scope path — so the
    two gates stay comparable to a reader who has just read the other one.
    """

    @staticmethod
    def callable_name(node: ast.expr) -> str | None:
        """Return the simple name a call targets, ignoring its module path."""

        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    @staticmethod
    def parents(tree: ast.AST) -> Mapping[ast.AST, ast.AST]:
        """Index every node to its parent so scopes can be walked upwards."""

        return {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }

    @staticmethod
    def lexical_scope(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> str:
        """Name the dotted class/function path ``node`` sits in.

        Scope names rather than line numbers are what let a violation survive
        reformatting: moving a declaration down ten lines must not change the
        gate's output, while moving it out of the declared composition site
        must.
        """

        names: list[str] = []
        current = node
        while current in parents:
            current = parents[current]
            if isinstance(
                current, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                names.append(current.name)
        return ".".join(reversed(names)) or "<module>"

    @staticmethod
    def keyword_value(call: ast.Call, name: str) -> ast.expr | None:
        """Return one keyword argument's expression, or ``None`` when absent."""

        for keyword in call.keywords:
            if keyword.arg == name:
                return keyword.value
        return None

    @staticmethod
    def literal_text(node: ast.expr | None) -> str | None:
        """Return a string literal's value, or ``None`` for anything computed.

        A computed value is never accepted as a stand-in for a literal. A label
        assembled at runtime cannot be pinned in the golden inventory, so
        accepting one would let a contributor be added without the review the
        pin exists to force.
        """

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    @staticmethod
    def is_none(node: ast.expr | None) -> bool:
        """Return whether ``node`` is a literal ``None``."""

        return isinstance(node, ast.Constant) and node.value is None


@dataclass(frozen=True, slots=True)
class CompositionSite:
    """Where in the tree something was written, named the way violations are.

    One spelling of ``<relative path>:<lexical scope>``, so the inventory, the
    duplicate report, and every violation message identify a place identically
    — and so a reader who greps for a string out of a red build finds it.
    """

    relative: str
    scope: str

    @property
    def site(self) -> str:
        return f"{self.relative}:{self.scope}"


@dataclass(frozen=True, slots=True)
class DeclaredOrigin:
    """One statically discoverable declaration and the site that made it.

    ``site`` carries the ``<relative path>:<lexical scope>`` of the declaration
    so a duplicate label can name both offenders. It is deliberately not part of
    the inventory: the pinned tuple is labels only, because a label moving
    between two functions in the same file is a refactor and should not read as
    a new context contributor.
    """

    label: str
    site: str


@dataclass(frozen=True, slots=True)
class SourceTreeSweep:
    """The single whole-tree pass, and the two tree-wide facts it collects.

    Both facts are properties of the *tree*, not of one file, which is why they
    share a walk rather than living in the two site-scoped sweeps below. This
    class only observes; the judgement of what the facts mean belongs to the
    sweep that owns the rule.

    **Literal declarations.** Every ``ContextOrigin(...)`` written out with a
    literal owner and name. That is the declaration form used by the
    message-side origins (§4.6) and by the runtime's own ``response_format`` and
    assembly-joiner declarations — contributors that are neither tools nor
    prompt sources, and that therefore have no composition keyword for the other
    two sweeps to key on. ``MessageContextOrigins.ALL`` documents itself as
    being pinned by this gate; this is the pass that does it.

    ``by_symbol`` maps the module- or class-level constant name a declaration
    was assigned to onto its label, so a composition site written as
    ``declare_context_origin(tool, SOME_ORIGIN)`` can be resolved back to a real
    declaration instead of being taken on trust.

    Origins built from *computed* owner/name — ``_fragment_origin`` projecting a
    ``PromptFragment``, ``ThirdPartyPromptConstant.to_origin`` projecting a
    pinned library constant — are skipped rather than flagged. Those are
    projections of declarations that live elsewhere and are pinned elsewhere;
    demanding literals of them would only push authors to inline strings the
    runtime would then have to keep in sync.

    **Assembly sites.** Every ``PromptAssemblyInputs(...)`` construction,
    wherever it is. Collecting these tree-wide is what stops the system half of
    the gate from being evadable by moving one call into another module: a sweep
    that only reads the factory would go quiet, and go quiet in the one way that
    looks like success.
    """

    declarations: tuple[DeclaredOrigin, ...]
    by_symbol: Mapping[str, str]
    assembly_sites: tuple[CompositionSite, ...]

    @classmethod
    def sweep(cls, source_root: Path) -> SourceTreeSweep:
        """Walk every module once, collecting both tree-wide facts."""

        declarations: list[DeclaredOrigin] = []
        by_symbol: dict[str, str] = {}
        assembly_sites: list[CompositionSite] = []
        for path in sorted(source_root.rglob("*.py")):
            relative = path.relative_to(source_root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            parents = AstNode.parents(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                callable_name = AstNode.callable_name(node.func)
                site = CompositionSite(
                    relative=relative,
                    scope=AstNode.lexical_scope(node, parents),
                )
                if callable_name == Keys.Callable.PROMPT_ASSEMBLY_INPUTS:
                    assembly_sites.append(site)
                    continue
                if callable_name != Keys.Callable.CONTEXT_ORIGIN:
                    continue
                owner = AstNode.literal_text(
                    AstNode.keyword_value(node, Keys.Keyword.OWNER)
                )
                name = AstNode.literal_text(
                    AstNode.keyword_value(node, Keys.Keyword.NAME)
                )
                if owner is None or name is None:
                    continue
                label = f"{owner}:{name}"
                declarations.append(DeclaredOrigin(label=label, site=site.site))
                for symbol in cls._assigned_symbols(node, parents):
                    by_symbol.setdefault(symbol, label)
        return cls(
            declarations=tuple(declarations),
            by_symbol=MappingProxyType(by_symbol),
            assembly_sites=tuple(assembly_sites),
        )

    @staticmethod
    def _assigned_symbols(
        node: ast.Call,
        parents: Mapping[ast.AST, ast.AST],
    ) -> tuple[str, ...]:
        """Return the constant names ``node`` was assigned to, if any."""

        parent = parents.get(node)
        if isinstance(parent, ast.AnnAssign) and isinstance(parent.target, ast.Name):
            return (parent.target.id,)
        if isinstance(parent, ast.Assign):
            return tuple(
                target.id for target in parent.targets if isinstance(target, ast.Name)
            )
        return ()


@dataclass(frozen=True, slots=True)
class ModelToolOwnerRegistry:
    """The ``ModelToolOwner`` enum, read as source rather than imported.

    Owners are read from the tree for the same reason the rest of the gate is:
    a conformance proof that imports the thing it is proving would pass on a
    stale ``.pyc`` and would be unable to run against a planted fixture tree.
    """

    owners: Mapping[str, str]

    @classmethod
    def load(cls, source_root: Path) -> ModelToolOwnerRegistry:
        """Read every ``ModelToolOwner`` member's dotted owner string."""

        path = source_root / MODEL_TOOL_OWNER_REGISTRY
        if not path.is_file():
            return cls(owners=MappingProxyType({}))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        owners: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name != Keys.Symbol.MODEL_TOOL_OWNER:
                continue
            for statement in node.body:
                if not isinstance(statement, ast.Assign):
                    continue
                value = AstNode.literal_text(statement.value)
                if value is None:
                    continue
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        owners[target.id] = value
        return cls(owners=MappingProxyType(owners))

    def resolve(self, node: ast.expr | None) -> str | None:
        """Resolve an ``owner=`` argument to its dotted owner string.

        Accepts a ``ModelToolOwner`` member or a plain string literal; anything
        computed resolves to ``None`` and is reported, because an owner nobody
        can read is an owner nobody can be asked to justify at review.
        """

        literal = AstNode.literal_text(node)
        if literal is not None:
            return literal
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == Keys.Symbol.MODEL_TOOL_OWNER
        ):
            return self.owners.get(node.attr)
        return None


@dataclass(frozen=True, slots=True)
class PromptSourceRegistry:
    """The ``PromptSource`` members and the ``fragment_id`` each is allocated.

    The pair is the system half of a declaration: a source with no registered
    provider can never become a fragment, so its bytes could never carry a
    label even if the composition site named an owner perfectly.
    """

    present: bool
    source_values: frozenset[str]
    fragment_ids: Mapping[str, str]

    @classmethod
    def load(cls, source_root: Path) -> PromptSourceRegistry:
        """Read the source enum and its provider registrations from source."""

        path = source_root / PROMPT_SOURCE_REGISTRY
        if not path.is_file():
            return cls(
                present=False,
                source_values=frozenset(),
                fragment_ids=MappingProxyType({}),
            )
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        members = cls._enum_members(tree)
        return cls(
            present=True,
            source_values=frozenset(members.values()),
            fragment_ids=cls._registered_fragment_ids(tree, members),
        )

    def fragment_id_for(self, source_value: str) -> str | None:
        """Return the fragment id registered for a source value, if any."""

        return self.fragment_ids.get(source_value)

    def is_complete(self) -> bool:
        """Return whether every ``PromptSource`` member maps to a declaration.

        Completeness here is the ``name`` half of the label: one registered
        provider per member, so every source that could ever render has a
        fragment id to be attributed under. The ``owner`` half is proved per
        composition site by :meth:`PromptSourceCompositionSweep.sweep`, because
        that is where it is written and where a reviewer can act on it.
        """

        return (
            self.present
            and bool(self.source_values)
            and all(value in self.fragment_ids for value in self.source_values)
        )

    @staticmethod
    def _enum_members(tree: ast.AST) -> Mapping[str, str]:
        """Map ``PromptSource`` member names onto their wire values."""

        members: dict[str, str] = {}
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.ClassDef)
                or node.name != Keys.Symbol.PROMPT_SOURCE
            ):
                continue
            for statement in node.body:
                if not isinstance(statement, ast.Assign):
                    continue
                value = AstNode.literal_text(statement.value)
                if value is None:
                    continue
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        members[target.id] = value
        return MappingProxyType(members)

    @staticmethod
    def _registered_fragment_ids(
        tree: ast.AST,
        members: Mapping[str, str],
    ) -> Mapping[str, str]:
        """Map each registered source's wire value onto its fragment id."""

        fragment_ids: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if AstNode.callable_name(node.func) != Keys.Callable.REGISTERED_PROVIDER:
                continue
            source = AstNode.keyword_value(node, Keys.Keyword.SOURCE)
            fragment_id = AstNode.literal_text(
                AstNode.keyword_value(node, Keys.Keyword.FRAGMENT_ID)
            )
            if fragment_id is None:
                continue
            if not isinstance(source, ast.Attribute):
                continue
            if not isinstance(source.value, ast.Name):
                continue
            if source.value.id != Keys.Symbol.PROMPT_SOURCE:
                continue
            value = members.get(source.attr)
            if value is not None:
                fragment_ids[value] = fragment_id
        return MappingProxyType(fragment_ids)


class ContextOriginViolation:
    """The messages this gate fails with, written to be actionable in a diff.

    Each one names the *thing that was added* rather than the rule it broke,
    because the reader of a red CI job is usually the person who just added it
    and the useful sentence is the one that says which symbol to declare.
    """

    @staticmethod
    def missing_composition_site(relative: str) -> str:
        return (
            f"{relative}: the context-origin composition site is absent; "
            "the gate cannot see what occupies the model's context"
        )

    @staticmethod
    def missing_tool_surface(relative: str) -> str:
        return (
            f"{relative}: {Keys.Symbol.MODEL_TOOL_SURFACE} is absent; "
            "the gate cannot see the model tool surface"
        )

    @staticmethod
    def missing_prompt_registry(site: str) -> str:
        return (
            f"{site} -> {PROMPT_SOURCE_REGISTRY.as_posix()} is absent; "
            "prompt sources cannot be checked for a declared origin"
        )

    @staticmethod
    def undeclared_tool(site: str, symbol: str) -> str:
        return (
            f"{site} -> {symbol} is composed into "
            f"{Keys.Symbol.MODEL_TOOLS} without a context-origin declaration"
        )

    @staticmethod
    def unreadable_tool_owner(site: str, symbol: str) -> str:
        return (
            f"{site} -> {symbol} declares a context origin whose owner is not a "
            f"{Keys.Symbol.MODEL_TOOL_OWNER} member"
        )

    @staticmethod
    def unnameable_tool(site: str) -> str:
        return (
            f"{site} -> a tool composed into {Keys.Symbol.MODEL_TOOLS} declares "
            "a context origin that cannot be named statically"
        )

    @staticmethod
    def unresolvable_origin(site: str, symbol: str) -> str:
        return (
            f"{site} -> {symbol} is declared with a context origin that is not a "
            "literal declaration this gate can inventory"
        )

    @staticmethod
    def assembly_off_site(site: str) -> str:
        return (
            f"{site} -> {Keys.Callable.PROMPT_ASSEMBLY_INPUTS} is composed "
            f"outside {CONTEXT_COMPOSITION_SITE.as_posix()}; its sources are "
            "not covered by the context-origin gate"
        )

    @staticmethod
    def keyword_expansion(site: str) -> str:
        return (
            f"{site} -> {Keys.Callable.PROMPT_ASSEMBLY_INPUTS} is composed by "
            "keyword expansion; its sources cannot be declared statically"
        )

    @staticmethod
    def unknown_prompt_source(site: str, keyword: str) -> str:
        return (
            f"{site} -> {Keys.Callable.PROMPT_ASSEMBLY_INPUTS}({keyword}=...) "
            f"is not a declared {Keys.Symbol.PROMPT_SOURCE} member"
        )

    @staticmethod
    def unregistered_prompt_source(site: str, keyword: str) -> str:
        return (
            f"{site} -> {Keys.Callable.PROMPT_ASSEMBLY_INPUTS}({keyword}=...) "
            "has no registered prompt fragment provider"
        )

    @staticmethod
    def undeclared_prompt_owner(site: str, keyword: str) -> str:
        return (
            f"{site} -> {Keys.Callable.PROMPT_ASSEMBLY_INPUTS}({keyword}=...) "
            "has no statically declared source owner"
        )

    @staticmethod
    def duplicate_label(label: str, sites: Sequence[str]) -> str:
        return (
            f"{label} is declared at more than one composition site: {', '.join(sites)}"
        )


class ModelToolSurfaceSweep:
    """Prove every tool composed onto the model surface declares an origin.

    The rule is coverage, not proximity: a contribution passes when the
    expression appended to ``model_tools`` *is* a declaration call, or when the
    local name being appended was passed through one somewhere in the same
    function. Ordering is not required, because
    :meth:`ContextOriginBinding.declare` stamps the object in place — declaring
    after appending declares the same object — and a gate that insisted on
    source order would be asserting something that is not true of the runtime.

    What the rule does forbid is the alternative that would quietly defeat it: a
    lookup table keyed by tool name, maintained away from the composition site.
    Under that design a new tool composes fine and measures as ``UNDECLARED``
    forever, which is exactly the failure mode §3.2 rejects a central registry
    for.

    The sweep stops at this one function on purpose. Everything the surface
    passes through afterwards — display-schema decoration, tool-policy
    enforcement — takes the already-declared tuple as its input and returns
    wrappers around the same objects, and
    :meth:`ContextOriginBinding.of` walks that wrapper chain to read the
    declaration back. Those are transforms of declared tools, not new
    contributors, so sweeping them would only manufacture violations that can be
    silenced but not fixed.
    """

    DECLARATION_CALLS: Final = frozenset(
        {
            Keys.Callable.DECLARE_CONTEXT_ORIGIN,
            Keys.Callable.DECLARED,
            Keys.Callable.DECLARED_ALL,
        }
    )

    SEQUENCE_BUILDERS: Final = frozenset({Keys.Callable.LIST, Keys.Callable.TUPLE})
    """Materializers unwrapped before coverage is judged.

    ``model_tools = list(declared_all(...))`` contributes the declared tools,
    not a list; treating the ``list`` call as the contributor would report a
    violation against a builtin.
    """

    UNNAMED_SUBJECT: Final = "<expression>"
    """Stand-in used in a message when a contributor cannot be named at all.

    Deliberately not a valid identifier: it appears only in violations, never in
    a label, so it can never reach the golden inventory and be mistaken for a
    contributor somebody reviewed.
    """

    TOOL_WRAPPERS: Final = frozenset(
        {Keys.Callable.STRUCTURED_TOOL, Keys.Callable.SHADOW_WRAPPER}
    )
    """Adapters descended through when naming a declaration site.

    ``_structured_tool`` wraps ten different adapters at this one site, so
    naming the *wrapper* would collapse ten contributors onto one inventory
    entry and the eleventh could then be added without moving the pin. Naming
    what it wraps keeps one entry per contributor. A future wrapper that is not
    listed here does not silently merge entries either — it collides, and
    :meth:`ContextOriginGate.duplicates` fails the build.
    """

    @classmethod
    def sweep(
        cls,
        source_root: Path,
        *,
        owners: ModelToolOwnerRegistry,
        origins: SourceTreeSweep,
    ) -> tuple[tuple[str, ...], tuple[DeclaredOrigin, ...]]:
        """Return tool-surface violations and the declarations it proved."""

        relative = CONTEXT_COMPOSITION_SITE.as_posix()
        path = source_root / CONTEXT_COMPOSITION_SITE
        if not path.is_file():
            return ((ContextOriginViolation.missing_composition_site(relative),), ())
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = AstNode.parents(tree)
        function = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == Keys.Symbol.MODEL_TOOL_SURFACE
            ),
            None,
        )
        if function is None:
            return ((ContextOriginViolation.missing_tool_surface(relative),), ())

        local_declarations = cls._local_declarations(function)
        violations: list[str] = []
        declarations: list[DeclaredOrigin] = []
        for node, contributed in cls._contributions(function):
            site = CompositionSite(
                relative=relative,
                scope=AstNode.lexical_scope(node, parents),
            ).site
            declaration = cls._declaration_for(contributed, local_declarations)
            # Name the *declared* expression rather than the declaration
            # wrapping it: a failure that reads "declared declares a context
            # origin whose owner is unreadable" tells the author nothing about
            # which of their thirteen appends is at fault. The same answer feeds
            # the inventory label, so a contributor is named identically whether
            # it passed or failed.
            subject = cls._site_symbol(
                cls._declared_expression(declaration, contributed)
            )
            symbol = subject or cls.UNNAMED_SUBJECT
            if declaration is None:
                violations.append(ContextOriginViolation.undeclared_tool(site, symbol))
                continue
            declared = cls._declared_origin(
                declaration,
                site=site,
                symbol=symbol,
                subject=subject,
                owners=owners,
                origins=origins,
            )
            if isinstance(declared, str):
                violations.append(declared)
            elif declared is not None:
                declarations.append(declared)
        return tuple(violations), tuple(declarations)

    @classmethod
    def _contributions(
        cls,
        function: ast.AST,
    ) -> tuple[tuple[ast.AST, ast.expr], ...]:
        """Return every ``(site, expression)`` that adds tools to the surface.

        Appends, extends, and assignments to the list all count. An assignment
        whose value *reads* the list is a transform of already-declared tools —
        policy enforcement, display decoration — and contributes nothing new, so
        it is not a contribution site. That exemption is structural rather than
        a name allowlist: it holds for any future transform precisely because
        the transform has to take the declared list as input.
        """

        found: list[tuple[ast.AST, ast.expr]] = []
        for node in ast.walk(function):
            if isinstance(node, ast.Call):
                target = node.func
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == Keys.Symbol.MODEL_TOOLS
                    and target.attr in {Keys.Symbol.APPEND, Keys.Symbol.EXTEND}
                    and node.args
                ):
                    found.extend(
                        (node, expression) for expression in cls._flatten(node.args[0])
                    )
                continue
            if isinstance(node, ast.Assign):
                assigned = any(
                    isinstance(target, ast.Name)
                    and target.id == Keys.Symbol.MODEL_TOOLS
                    for target in node.targets
                )
            elif isinstance(node, ast.AugAssign):
                assigned = (
                    isinstance(node.target, ast.Name)
                    and node.target.id == Keys.Symbol.MODEL_TOOLS
                )
            else:
                continue
            if assigned and not cls._reads_surface(node.value):
                found.extend(
                    (node, expression) for expression in cls._flatten(node.value)
                )
        return tuple(found)

    @classmethod
    def _flatten(cls, node: ast.expr) -> tuple[ast.expr, ...]:
        """Unwrap materializers and literal sequences into their contributors."""

        if (
            isinstance(node, ast.Call)
            and AstNode.callable_name(node.func) in cls.SEQUENCE_BUILDERS
            and len(node.args) == 1
        ):
            return cls._flatten(node.args[0])
        if isinstance(node, (ast.List, ast.Tuple)):
            return tuple(
                part for element in node.elts for part in cls._flatten(element)
            )
        return (node,)

    @staticmethod
    def _reads_surface(node: ast.expr) -> bool:
        """Return whether an expression reads the tool list it is assigned to."""

        return any(
            isinstance(child, ast.Name) and child.id == Keys.Symbol.MODEL_TOOLS
            for child in ast.walk(node)
        )

    @classmethod
    def _local_declarations(cls, function: ast.AST) -> Mapping[str, ast.Call]:
        """Map local names that were passed through a declaration call.

        Covers the two shapes a declaration can take when it is not written
        inline at the append: ``tool = declare_context_origin(build(), origin)``
        and a bare ``declare_context_origin(tool, origin)`` statement before or
        after the append.
        """

        declared: dict[str, ast.Call] = {}
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and cls._is_declaration(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        declared.setdefault(target.id, node.value)  # type: ignore[arg-type]
                continue
            if not cls._is_declaration(node):
                continue
            if node.args and isinstance(node.args[0], ast.Name):  # type: ignore[union-attr]
                declared.setdefault(node.args[0].id, node)  # type: ignore[union-attr,arg-type]
        return MappingProxyType(declared)

    @classmethod
    def _is_declaration(cls, node: ast.AST) -> bool:
        """Return whether ``node`` is a call to the declaration seam."""

        return (
            isinstance(node, ast.Call)
            and AstNode.callable_name(node.func) in cls.DECLARATION_CALLS
        )

    @classmethod
    def _declaration_for(
        cls,
        contributed: ast.expr,
        local_declarations: Mapping[str, ast.Call],
    ) -> ast.Call | None:
        """Return the declaration covering ``contributed``, or ``None``."""

        if cls._is_declaration(contributed):
            return contributed  # type: ignore[return-value]
        if isinstance(contributed, ast.Name):
            return local_declarations.get(contributed.id)
        return None

    @staticmethod
    def _declared_expression(
        declaration: ast.Call | None,
        contributed: ast.expr,
    ) -> ast.expr:
        """Return the expression a declaration is about.

        For a declared contribution that is the declaration's first argument —
        the tool, not the wrapper around it — and for an undeclared one it is
        the contributed expression itself. Both the inventory label and the
        violation message are derived from this single answer so a contributor
        is named the same way whether it passed or failed.
        """

        if declaration is not None and declaration.args:
            return declaration.args[0]
        return contributed

    @classmethod
    def _declared_origin(
        cls,
        declaration: ast.Call,
        *,
        site: str,
        symbol: str,
        subject: str | None,
        owners: ModelToolOwnerRegistry,
        origins: SourceTreeSweep,
    ) -> DeclaredOrigin | str | None:
        """Read a declaration's label, a violation string, or ``None``.

        ``None`` means the declaration is real and already inventoried
        elsewhere: a ``declare_context_origin(tool, ORIGIN)`` names an origin
        object that :class:`SourceTreeSweep` has already recorded at the
        point it was written, and recording it twice would trip the duplicate
        check with a false positive.

        ``subject`` is the caller's already-computed name for the thing being
        declared, and is ``None`` when the expression cannot be named at all;
        ``symbol`` is the same answer made presentable for a message. Deriving
        it once upstream is what keeps the label in the golden inventory and the
        name in a violation from ever disagreeing.
        """

        if (
            AstNode.callable_name(declaration.func)
            == Keys.Callable.DECLARE_CONTEXT_ORIGIN
        ):
            if cls._origin_argument_label(declaration, origins) is None:
                return ContextOriginViolation.unresolvable_origin(site, symbol)
            return None
        owner = owners.resolve(AstNode.keyword_value(declaration, Keys.Keyword.OWNER))
        if owner is None:
            return ContextOriginViolation.unreadable_tool_owner(site, symbol)
        name = (
            AstNode.literal_text(AstNode.keyword_value(declaration, Keys.Keyword.NAME))
            or subject
        )
        if name is None:
            return ContextOriginViolation.unnameable_tool(site)
        return DeclaredOrigin(label=f"{owner}:{name}", site=site)

    @staticmethod
    def _origin_argument_label(
        declaration: ast.Call,
        origins: SourceTreeSweep,
    ) -> str | None:
        """Resolve the origin handed to ``declare_context_origin``, if it can be.

        An inline ``ContextOrigin(...)`` with literal fields is resolved by the
        literal sweep that already walked this file; a constant reference is
        resolved by the name it was assigned to. Anything else — an origin built
        from a variable, or handed in as a parameter — is unpinnable and is
        reported, which is the same standard the owner keyword is held to.
        """

        if len(declaration.args) < 2:
            return None
        origin = declaration.args[1]
        if isinstance(origin, ast.Call):
            if AstNode.callable_name(origin.func) != Keys.Callable.CONTEXT_ORIGIN:
                return None
            owner = AstNode.literal_text(
                AstNode.keyword_value(origin, Keys.Keyword.OWNER)
            )
            name = AstNode.literal_text(
                AstNode.keyword_value(origin, Keys.Keyword.NAME)
            )
            return None if owner is None or name is None else f"{owner}:{name}"
        symbol = AstNode.callable_name(origin)
        return None if symbol is None else origins.by_symbol.get(symbol)

    @classmethod
    def _site_symbol(cls, node: ast.expr | None) -> str | None:
        """Name the contributor an expression composes, for the inventory.

        Descends through the composition wrappers so the name is the adapter
        being composed rather than the helper composing it, and through a
        comprehension to the sequence it draws from — which is how the tools
        listed by the injected registry are named for what they are rather than
        for the shadow wrapper each one passes through.
        """

        if node is None:
            return None
        if isinstance(node, ast.Call):
            callable_name = AstNode.callable_name(node.func)
            if callable_name in cls.TOOL_WRAPPERS and node.args:
                return cls._site_symbol(node.args[0])
            return callable_name
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Starred):
            return cls._site_symbol(node.value)
        if isinstance(node, ast.IfExp):
            return cls._site_symbol(node.body)
        if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
            if not node.generators:
                return None
            return cls._site_symbol(node.generators[0].iter)
        return None


class PromptSourceCompositionSweep:
    """Prove every system source handed to the assembler declares an origin.

    The system half needs no new bookkeeping (§4.1) — a ``PromptSourceMaterial``
    already carries ``source_owner`` and its registered provider already
    allocates ``fragment_id``, which together *are* the ``owner:name`` label the
    runtime records. What was missing is a proof that both halves exist and can
    be read without running the program, and that is all this adds.

    Only two constructors are recognized: the contract itself and the factory's
    own ``_prompt_source_material`` helper. That is not a stylistic preference.
    A third indirection would hide the owner literal behind a call this sweep
    cannot read, so requiring it to be added here is the review step that keeps
    "who owns these bytes" answerable from the composition site.
    """

    MATERIAL_BUILDERS: Final[Mapping[str, str]] = MappingProxyType(
        {
            Keys.Callable.PROMPT_SOURCE_MATERIAL: Keys.Keyword.SOURCE_OWNER,
            Keys.Callable.FACTORY_SOURCE_MATERIAL: Keys.Keyword.OWNER,
        }
    )

    @classmethod
    def sweep(
        cls,
        source_root: Path,
        *,
        registry: PromptSourceRegistry,
        assembly_sites: Sequence[CompositionSite] = (),
    ) -> tuple[tuple[str, ...], tuple[DeclaredOrigin, ...]]:
        """Return system-source violations and the declarations it proved.

        ``assembly_sites`` is every ``PromptAssemblyInputs`` construction in the
        whole tree, and any that is not in the composition site is refused
        before the file itself is read. Without that, moving one call into
        another module would silently take its sources out of the gate's view —
        the failure mode a conformance proof must not have, because it looks
        exactly like passing.

        A missing composition site is reported once, by
        :meth:`ModelToolSurfaceSweep.sweep`; repeating it here would double
        every failure of a tree that has no factory at all.
        """

        relative = CONTEXT_COMPOSITION_SITE.as_posix()
        off_site = tuple(
            ContextOriginViolation.assembly_off_site(site.site)
            for site in assembly_sites
            if site.relative != relative
        )
        path = source_root / CONTEXT_COMPOSITION_SITE
        if not path.is_file():
            return (off_site, ())
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = AstNode.parents(tree)
        violations: list[str] = list(off_site)
        declarations: list[DeclaredOrigin] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if AstNode.callable_name(node.func) != Keys.Callable.PROMPT_ASSEMBLY_INPUTS:
                continue
            site = CompositionSite(
                relative=relative,
                scope=AstNode.lexical_scope(node, parents),
            ).site
            if not registry.present:
                violations.append(ContextOriginViolation.missing_prompt_registry(site))
                continue
            for keyword in node.keywords:
                outcome = cls._keyword_outcome(keyword, registry=registry, site=site)
                if isinstance(outcome, str):
                    violations.append(outcome)
                elif outcome is not None:
                    declarations.append(outcome)
        return tuple(violations), tuple(declarations)

    @classmethod
    def _keyword_outcome(
        cls,
        keyword: ast.keyword,
        *,
        registry: PromptSourceRegistry,
        site: str,
    ) -> DeclaredOrigin | str | None:
        """Judge one ``PromptAssemblyInputs`` keyword.

        Returns the declaration it proved, a violation string, or ``None`` for a
        keyword that contributes nothing to the window — the assembly context,
        and a source explicitly passed as ``None``.
        """

        if keyword.arg is None:
            return ContextOriginViolation.keyword_expansion(site)
        if keyword.arg == Keys.Keyword.CONTEXT:
            return None
        if AstNode.is_none(keyword.value):
            return None
        if keyword.arg not in registry.source_values:
            return ContextOriginViolation.unknown_prompt_source(site, keyword.arg)
        fragment_id = registry.fragment_id_for(keyword.arg)
        if fragment_id is None:
            return ContextOriginViolation.unregistered_prompt_source(site, keyword.arg)
        owner = cls._material_owner(keyword.value)
        if owner is None:
            return ContextOriginViolation.undeclared_prompt_owner(site, keyword.arg)
        return DeclaredOrigin(label=f"{owner}:{fragment_id}", site=site)

    @classmethod
    def _material_owner(cls, node: ast.expr) -> str | None:
        """Read the literal ``source_owner`` a material was built with.

        A conditional source — ``material if block else None``, the shape every
        optional block in the factory uses — is read through to the branch that
        actually builds a material, because the declaration is the same whether
        or not this run renders the block.
        """

        if isinstance(node, ast.IfExp):
            for branch in (node.body, node.orelse):
                if AstNode.is_none(branch):
                    continue
                owner = cls._material_owner(branch)
                if owner is not None:
                    return owner
            return None
        if not isinstance(node, ast.Call):
            return None
        keyword_name = cls.MATERIAL_BUILDERS.get(AstNode.callable_name(node.func) or "")
        if keyword_name is None:
            return None
        return AstNode.literal_text(AstNode.keyword_value(node, keyword_name))


@dataclass(frozen=True, slots=True)
class ContextOriginGateResult:
    """One evaluation of the gate: what failed, and what was proved declared."""

    violations: tuple[str, ...]
    declarations: tuple[DeclaredOrigin, ...]

    @property
    def inventory(self) -> tuple[str, ...]:
        """Sorted, unique ``owner:name`` labels discoverable in the tree."""

        return tuple(sorted({declaration.label for declaration in self.declarations}))


class ContextOriginGate:
    """Run the three sweeps together and reconcile what they found.

    Kept as one evaluation rather than three independent entry points because
    the sweeps genuinely depend on each other: a tool declared with
    ``declare_context_origin`` is inventoried by the whole-tree pass, the
    system-source sweep needs that pass's assembly sites to know it saw them
    all, and a label collision is only visible once every sweep has contributed.
    The module's public functions are thin projections of the single result.
    """

    @classmethod
    def evaluate(cls, source_root: Path) -> ContextOriginGateResult:
        """Sweep ``source_root`` once and return every finding."""

        origins = SourceTreeSweep.sweep(source_root)
        owners = ModelToolOwnerRegistry.load(source_root)
        registry = PromptSourceRegistry.load(source_root)
        tool_violations, tool_declarations = ModelToolSurfaceSweep.sweep(
            source_root,
            owners=owners,
            origins=origins,
        )
        prompt_violations, prompt_declarations = PromptSourceCompositionSweep.sweep(
            source_root,
            registry=registry,
            assembly_sites=origins.assembly_sites,
        )
        declarations = origins.declarations + tool_declarations + prompt_declarations
        violations = tool_violations + prompt_violations + cls.duplicates(declarations)
        return ContextOriginGateResult(
            violations=tuple(sorted(violations)),
            declarations=declarations,
        )

    @staticmethod
    def duplicates(declarations: Sequence[DeclaredOrigin]) -> tuple[str, ...]:
        """Report labels claimed by more than one declaration.

        This is what keeps the pinned inventory honest under deduplication. Two
        contributors sharing a label would appear as one entry, and the second
        could then be added — or removed — without the golden tuple moving,
        which is precisely the review the pin exists to force. A collision is
        also a real defect at runtime: the occupancy report would attribute both
        contributors' bytes to whichever one the reader happened to open.

        The trigger is the *count of declarations*, not the count of distinct
        sites, and that distinction is load-bearing rather than pedantic: every
        tool declaration in the real tree is made inside the same function, so
        de-duplicating by site would blind the check to the one collision it
        most needs to catch — a naming rule that quietly folds several appends
        in ``_model_visible_tools`` onto one label. The sites are still
        de-duplicated in the *message*, where repeating one scope name adds
        nothing.
        """

        sites: dict[str, list[str]] = {}
        for declaration in declarations:
            sites.setdefault(declaration.label, []).append(declaration.site)
        return tuple(
            ContextOriginViolation.duplicate_label(label, sorted(set(found)))
            for label, found in sorted(sites.items())
            if len(found) > 1
        )


def undeclared_context_contributors(source_root: Path) -> tuple[str, ...]:
    """Return every contributor that occupies context without declaring it.

    **Must be empty.** A non-empty result is the build failing on behalf of a
    reader who does not exist yet: somebody added text to the model's context
    window and did not say what it was, so the occupancy report would have
    charged it to ``undeclared_tokens`` and named nobody.

    The fix is never to add an entry here. It is to declare the origin at the
    composition site — ``ModelToolDeclaration.declared(...)`` for a tool, a
    literal ``source_owner`` for a prompt source — which is the same edit as
    accepting the tokens it costs.
    """

    return ContextOriginGate.evaluate(source_root).violations


def context_origin_inventory(source_root: Path) -> tuple[str, ...]:
    """Inventory every context origin declared statically under ``source_root``.

    Sorted ``owner:name`` labels, pinned to a literal tuple in
    ``tests/unit/test_context_origin_gate.py`` exactly as
    ``test_llm_seam_gate`` pins the canonical chat-model call sites. Adding a
    contributor with a declaration still fails CI until the author adds its line
    to that tuple, and that moment — not the declaration, not the merge — is
    when they consciously accept the context cost.
    """

    return ContextOriginGate.evaluate(source_root).inventory


def declared_origin_registry_complete(source_root: Path) -> bool:
    """Return whether every ``PromptSource`` member maps to a declaration.

    The system half of a declaration is the join of a ``fragment_id`` allocated
    by a registered provider with a ``source_owner`` written at the composition
    site. This proves the first half for the whole closed enum, including the
    members no producer materializes yet: a source that renders one day with no
    registered provider would occupy the window with no possible label, and it
    would do so silently, because the assembler simply skips a source it has no
    provider for.
    """

    return PromptSourceRegistry.load(source_root).is_complete()


__all__ = (
    "CONTEXT_COMPOSITION_SITE",
    "MODEL_TOOL_OWNER_REGISTRY",
    "PROMPT_SOURCE_REGISTRY",
    "ContextOriginGate",
    "ContextOriginGateResult",
    "DeclaredOrigin",
    "context_origin_inventory",
    "declared_origin_registry_complete",
    "undeclared_context_contributors",
)
