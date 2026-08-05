"""CI guard (P5): no capability ships off-by-default without an e2e/test path.

This is the standing gate for the failure mode that shipped both the AC2b
worker-gate bug and the file-store citation data-loss bug: a capability built
correct in isolation, selected by an env flag that defaults OFF, that *no
automated test ever turns ON*. Every unit test, the typecheck, and adversarial
review passed because they all ran the default (OFF) path; the ON path was dark
until a human flipped it in production. (See
``docs/plan/verification/02-no-dark-capabilities-gate.md``.)

The check is deliberately a *floor*, not a proof. It flags any runtime
capability-selecting env flag declared in the ai-backend source whose **name is
never referenced by any test or e2e harness** — the unambiguous signature of a
dark capability (if no test even mentions the flag, its non-default path is
certainly unexercised). It cannot prove the ON path is *asserted* (a test could
reference the flag only to assert it stays off); that deeper obligation lives in
the reviewer checklist in the policy doc.

Two independent detectors decide what is in scope. Both matter, and the second
exists because the first was structurally blind to an entire subsystem
(``docs/audit/generative-ui/FINDINGS.md`` §4.6b):

**A. Name shape.** ``*_BACKEND`` selects one of several implementations; every
non-default value ships dark unless a test drives it. ``*_ENABLE_*`` is the
established naming convention for an opt-in capability that is OFF by default
(``RUNTIME_ENABLE_LOCAL_MODELS``, ``RUNTIME_ENABLE_REMOTE_SANDBOX``, …).

*The ``RUNTIME_`` prefix requirement was removed.* It was a scoping convenience
that became a hole: ``SURFACE_SPEC_STORE_BACKEND`` is a textbook backend
selector for the generative-UI spec store, and the gate could not see it purely
because of how it is spelled. The blast radius stays bounded by
:data:`DEFAULT_SRC_ROOTS` (one service's ``src``), which is where the scoping
belongs — a flag's *ownership* is a directory, not a prefix.

**B. Read through a ``Flag.enabled()``-shaped classmethod.** A class that
declares an env-var name as a class constant and exposes an ``enabled()``
classmethod/staticmethod over it *is* a capability gate, whatever the flag is
called. This is the shape ``SurfacesV2Flag``, ``Tier2GenerationFlag``,
``RepairExecutionEnv``, ``ArtifactCleanupExecutionEnv``,
``RevisionControlPlaneEnvironment`` and ``QueueTracePropagator`` all use.

Detector B is the fix for a specific escape. The generative-UI audit drove the
real packaged app and found four independent breaks that ~9,000 green unit tests
had shipped over. Every flag in that subsystem was invisible here:
``RUNTIME_TIER2_GENERATION`` matched the old regex but failed the old name
predicate; ``SURFACES_V2`` failed the regex outright. The gate whose whole job is
catching dark capabilities could not see a single flag of the feature that went
dark. A convention ("new capabilities should be named ``RUNTIME_ENABLE_*``")
is not a mechanism — so scope is now decided by how a flag is *read*, which the
code cannot get wrong, as well as by how it is spelled.

An instance ``@property`` named ``enabled`` is deliberately NOT detector B: those
report resolved object state (``QuotaGuard.enabled`` = "a ceiling is
configured"), not an environment gate, and folding them in only adds noise.

Deliberately NOT matched by either detector: ``<subsystem>_ENABLED`` suffix
booleans read ad hoc (e.g. ``RUNTIME_DEFAULT_REASONING_ENABLED``). Those tune an
always-present subsystem rather than gate a separable capability behind an
off-default implementation path, and the default path is exercised.

A flag "has a path" if it is named under a reference root:
- ``services/ai-backend/tests`` (unit/integration, incl. the hermetic run→stream
  Tier A tests),
- ``tools/desktop-runtime`` (the Tier B supervised-boot harness ``run-local.mjs``),
- ``tools/cli-testing`` (the live-smoke Electron driver).

Naming it means either the literal env name (``"SURFACES_V2"``) **or** the
symbol the flag is declared as (``ArtifactCleanupExecutionEnv.ENABLED``). The
symbolic form is not a courtesy: a test that flips a flag correctly writes
``monkeypatch.setenv(RepairExecutionEnv.ENABLED, "true")`` — referencing the
constant, never the string — and a literal-only scan reads that genuinely
exercised flag as dark. Two of the six flags newly in scope,
``ARTIFACT_CLEANUP_EXECUTION_ENABLED`` and ``REPAIR_EXECUTION_ENABLED``, are
named that way and no other, so widening the declaration side without also
widening the reference side would have reported two well-tested capabilities
dark forever — which is how a gate ends up deleted.

Waiver: put ``# dark-capability-waiver: <reason>`` on the flag's declaration line
to exempt it (the reason is reviewed in the PR diff). Keep waivers rare — each is
a capability whose alternate path is, by admission, unverified.

Usage::

    python tools/check_dark_capabilities.py            # default: ai-backend
    python tools/check_dark_capabilities.py <src_root>  # explicit root(s)

Exits non-zero listing every dark capability flag.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# An env-var-shaped token: SCREAMING_SNAKE, at least three characters. No
# subsystem prefix is required — see the module docstring on why dropping
# ``RUNTIME_`` closed a hole rather than opening one. Whether a matched token is
# a *capability* flag is decided by ``_is_capability_flag`` (detector A) or by
# how it is read (``_FlagReaderScanner``, detector B).
_ENV_TOKEN = re.compile(r"[A-Z][A-Z0-9_]{2,}")
# The same token when it appears as a quoted string literal (a declaration).
_ENV_LITERAL = re.compile(r"""["'](?P<name>[A-Z][A-Z0-9_]{2,})["']""")
# ``SomeClass.CONSTANT`` — how a test names a flag it flips through the class
# that owns it. The leading underscore is allowed because a flag class may keep
# its env key private (``QueueTracePropagator._FLAG_ENV_VAR``).
_SYMBOL_REFERENCE = re.compile(r"\b(?P<symbol>[A-Z][A-Za-z0-9_]*\._?[A-Z][A-Z0-9_]*)\b")


def _is_capability_flag(name: str) -> bool:
    """Detector A: a backend/implementation selector or an opt-in toggle.

    * ``*_BACKEND`` — selects one of several implementations
      (``RUNTIME_STORE_BACKEND``, ``RUNTIME_KMS_BACKEND``,
      ``SURFACE_SPEC_STORE_BACKEND``); every non-default value ships dark unless
      a test drives it.
    * ``*_ENABLE_*`` — the established naming convention for an *opt-in
      capability* that is OFF by default (``RUNTIME_ENABLE_LOCAL_MODELS``,
      ``RUNTIME_ENABLE_REMOTE_SANDBOX``, …). A whole feature hangs off it.

    No prefix is required. ``<subsystem>_ENABLED`` suffix booleans stay out of
    scope here — a genuine capability gate spelled that way is caught by
    detector B instead, because it is read through an ``enabled()`` classmethod.
    """

    return name.endswith("_BACKEND") or "_ENABLE_" in name


WAIVER_MARKER = "# dark-capability-waiver:"

DEFAULT_SRC_ROOTS: tuple[Path, ...] = (REPO_ROOT / "services" / "ai-backend" / "src",)

# Where a flag's ON/alternate path may be exercised. Not limited to .py — the
# Tier B harness is .mjs — so these roots are scanned as plain text.
REFERENCE_ROOTS: tuple[Path, ...] = (
    REPO_ROOT / "services" / "ai-backend" / "tests",
    REPO_ROOT / "tools" / "desktop-runtime",
    REPO_ROOT / "tools" / "cli-testing",
)

_SKIP_DIRS = {".venv", "venv", "__pycache__", "node_modules", "dist", ".vite", ".git"}


class FlagDeclaration:
    """One capability flag: its env name, where it is declared, how it is named.

    ``aliases`` are the ``Owner.CONSTANT`` spellings a test may use instead of
    the literal env name. Empty for a flag found only by its name shape, because
    nothing ties such a flag to an owning class.
    """

    __slots__ = ("name", "file", "lineno", "aliases")

    def __init__(
        self,
        *,
        name: str,
        file: Path,
        lineno: int,
        aliases: frozenset[str] = frozenset(),
    ) -> None:
        self.name = name
        self.file = file
        self.lineno = lineno
        self.aliases = aliases


class DarkCapability:
    """A capability flag declared in source with no referencing test/e2e path."""

    __slots__ = ("declaration",)

    def __init__(self, *, declaration: FlagDeclaration) -> None:
        self.declaration = declaration

    @property
    def name(self) -> str:
        return self.declaration.name

    def render(self) -> str:
        try:
            rel = self.declaration.file.relative_to(REPO_ROOT)
        except ValueError:
            rel = self.declaration.file
        alias_hint = (
            f" (or {sorted(self.declaration.aliases)[0]})"
            if self.declaration.aliases
            else ""
        )
        return (
            f"{rel}:{self.declaration.lineno}: capability flag "
            f"{self.declaration.name!r} is never referenced by any test or e2e "
            "harness — its non-default path ships DARK. Add a test that drives "
            "it ON (e.g. a hermetic run→stream over the file/postgres store, or "
            "the Tier B supervised-boot harness) naming it as "
            f"{self.declaration.name!r}{alias_hint}, or add a "
            f"`{WAIVER_MARKER} <reason>` comment to the declaration line."
        )


def _iter_text_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    results: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        results.append(path)
    return results


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _collect_referenced_names(
    roots: tuple[Path, ...],
    *,
    declarations: dict[str, FlagDeclaration] | None = None,
) -> set[str]:
    """Every capability flag named under the ref roots, **however it is spelled**.

    Notably NOT filtered by :func:`_is_capability_flag`. It used to be, and that
    coupling was itself a hole: detector B puts flags in scope that detector A's
    name predicate rejects (``SURFACES_V2``), so filtering the *reference* side
    by A's predicate reported them dark no matter how many tests drove them. The
    two sides answer different questions — what is in scope, and what is named —
    and only the first one is A's to decide.

    **Why the symbolic channel is resolved here rather than by the caller.** It
    was not, briefly, and that shipped a bug worth writing down. A test that
    flips a flag correctly writes ``monkeypatch.setenv(RepairExecutionEnv.ENABLED,
    …)`` — naming the constant, never the string — so "is it referenced" has two
    valid spellings and needs both channels. Handing the caller two sets to
    intersect made the answer *assemblable*, and a second consumer promptly
    assembled a different one: ``agent_runtime/release/e2_final_conformance.py``
    diffs ``_first_declarations`` against this function alone, so the moment
    symbol-only flags entered scope it reported two well-tested capabilities
    (``ARTIFACT_CLEANUP_EXECUTION_ENABLED``, ``REPAIR_EXECUTION_ENABLED``) as
    dark and turned the E2 release gate red.

    That is the same defect this whole audit is about — one object taken apart
    and rebuilt by several layers, each free to rebuild it wrong — so the fix is
    the same: return the finished answer. A caller cannot now disagree with the
    gate without calling something that does not exist.

    ``declarations`` supplies the alias→env-name mapping. It defaults to the
    standard source roots so that a caller who passes only reference roots still
    gets a complete answer; :func:`main` passes the set it already scanned so the
    source tree is not walked twice.
    """

    referenced: set[str] = set()
    for root in roots:
        for path in _iter_text_files(root):
            text = _read(path)
            if text is None:
                continue
            # Any mention (quoted literal OR a bare env-key reference — a harness
            # may build the key by name without quotes) counts as a path.
            referenced.update(_ENV_TOKEN.findall(text))

    known = (
        _first_declarations(DEFAULT_SRC_ROOTS) if declarations is None else declarations
    )
    symbols = _collect_referenced_symbols(roots)
    referenced.update(
        declaration.name
        for declaration in known.values()
        if declaration.aliases & symbols
    )
    return referenced


def _collect_referenced_symbols(roots: tuple[Path, ...]) -> set[str]:
    """Every ``Owner.CONSTANT`` spelling that appears under the ref roots.

    Separate from :func:`_collect_referenced_names` because the two answer
    different questions: that one asks "is this env *string* mentioned", this one
    asks "is the constant that holds it mentioned". A test flipping a flag
    correctly does the latter and never the former.
    """

    referenced: set[str] = set()
    for root in roots:
        for path in _iter_text_files(root):
            text = _read(path)
            if text is None:
                continue
            referenced.update(_SYMBOL_REFERENCE.findall(text))
    return referenced


class _FlagReaderScanner:
    """Detector B: env flags read through a ``Flag.enabled()``-shaped classmethod.

    A class that keeps an env-var name in a class constant and exposes an
    ``enabled()`` classmethod (or staticmethod) over it is a capability gate by
    construction — that is the shape, and the name it happens to be spelled with
    is irrelevant. Scoping by shape rather than by spelling is what makes this
    detector unable to miss the next ``SURFACES_V2``.

    Deliberately excludes an instance ``@property`` named ``enabled``: those
    report resolved object state (a configured ceiling, a constructed runner),
    not an environment gate.
    """

    #: The method whose presence marks a class as a flag reader.
    GATE_METHOD = "enabled"
    #: Decorators that make ``enabled`` a class-level reader rather than a
    #: property over instance state.
    _BINDINGS = frozenset({"classmethod", "staticmethod"})

    def __init__(self, *, file: Path, source: str) -> None:
        self._file = file
        self._source = source
        self._lines = source.splitlines()

    def declarations(self) -> list[FlagDeclaration]:
        """Return one declaration per env flag read by an ``enabled()`` gate."""

        try:
            tree = ast.parse(self._source)
        except SyntaxError:
            # A file this gate cannot parse is a problem for the type checker and
            # the test suite, not for a static scan that must stay total.
            return []
        found: list[FlagDeclaration] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                found.extend(self._declarations_of(node))
        return found

    def _declarations_of(self, node: ast.ClassDef) -> list[FlagDeclaration]:
        gate = self._gate_method(node)
        if gate is None:
            return []
        constants = self._string_constants(node)
        found: list[FlagDeclaration] = []
        for name, lineno, attribute in self._env_names_read(gate, constants):
            if self._is_waived(lineno):
                continue
            aliases = (
                frozenset({f"{node.name}.{attribute}"})
                if attribute is not None
                else frozenset()
            )
            found.append(
                FlagDeclaration(
                    name=name, file=self._file, lineno=lineno, aliases=aliases
                )
            )
        return found

    @classmethod
    def _gate_method(cls, node: ast.ClassDef) -> ast.FunctionDef | None:
        """The class-bound ``enabled()`` reader, or ``None`` if there is none."""

        for statement in node.body:
            if not isinstance(statement, ast.FunctionDef):
                continue
            if statement.name != cls.GATE_METHOD:
                continue
            decorators = {
                decorator.id
                for decorator in statement.decorator_list
                if isinstance(decorator, ast.Name)
            }
            if decorators & cls._BINDINGS:
                return statement
        return None

    @staticmethod
    def _string_constants(node: ast.ClassDef) -> dict[str, tuple[str, int]]:
        """Class-level ``ATTR = "ENV_NAME"`` bindings, annotated or not.

        Only env-shaped values are kept, so a class constant holding a default
        (``_DEFAULT_WHEN_UNSET = "true"``) is never mistaken for a flag name.
        """

        constants: dict[str, tuple[str, int]] = {}
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign):
                targets: list[ast.expr] = [statement.target]
                value = statement.value
            elif isinstance(statement, ast.Assign):
                targets = list(statement.targets)
                value = statement.value
            else:
                continue
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            if not _ENV_TOKEN.fullmatch(value.value):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = (value.value, statement.lineno)
        return constants

    @staticmethod
    def _env_names_read(
        gate: ast.FunctionDef, constants: dict[str, tuple[str, int]]
    ) -> list[tuple[str, int, str | None]]:
        """``(env_name, declaration_lineno, owning_attribute)`` per flag read.

        Two forms, both seen in this codebase: the gate dereferences a class
        constant (``cls.ENV_VAR`` / ``Owner.ENABLED``), or it inlines the env
        name as a literal. The reported line is the *declaration* — where a
        waiver would go and where a reviewer looks — not the read site.
        """

        found: dict[str, tuple[int, str | None]] = {}
        for node in ast.walk(gate):
            if isinstance(node, ast.Attribute) and node.attr in constants:
                name, lineno = constants[node.attr]
                found.setdefault(name, (lineno, node.attr))
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _ENV_TOKEN.fullmatch(node.value)
            ):
                found.setdefault(node.value, (node.lineno, None))
        return [
            (name, lineno, attribute) for name, (lineno, attribute) in found.items()
        ]

    def _is_waived(self, lineno: int) -> bool:
        index = lineno - 1
        if index < 0 or index >= len(self._lines):
            return False
        return WAIVER_MARKER in self._lines[index]


class _DeclScanner:
    """Detector A: collect name-shaped capability flags from one source file."""

    def __init__(self, *, file: Path, source: str) -> None:
        self._file = file
        self._lines = source.splitlines()

    def declarations(self) -> list[tuple[str, int]]:
        """Return ``(flag_name, lineno)`` for each non-waived declaration.

        A flag counts as "declared" where its quoted literal appears in source
        (canonically its ``settings.py`` constant). A line carrying the waiver
        marker is skipped so an explicit exception never becomes a violation.
        """

        found: list[tuple[str, int]] = []
        for lineno, line in enumerate(self._lines, start=1):
            if WAIVER_MARKER in line:
                continue
            for match in _ENV_LITERAL.finditer(line):
                name = match.group("name")
                if _is_capability_flag(name):
                    found.append((name, lineno))
        return found


def _first_declarations(roots: tuple[Path, ...]) -> dict[str, FlagDeclaration]:
    """Map each non-waived capability flag to its first declaration site.

    Detector B runs first per file so a flag found both ways keeps the richer
    record — the one that knows the ``Owner.CONSTANT`` alias a test may name it
    by. Both detectors are needed: B cannot see a selector read through plain
    settings loading, A cannot see a gate whose name follows no convention.
    """

    declarations: dict[str, FlagDeclaration] = {}
    for root in roots:
        files = _iter_text_files(root) if root.is_dir() else [root]
        for path in sorted(files):
            if path.suffix != ".py":
                continue
            source = _read(path)
            if source is None:
                continue
            for declared in _FlagReaderScanner(file=path, source=source).declarations():
                declarations.setdefault(declared.name, declared)
            for name, lineno in _DeclScanner(file=path, source=source).declarations():
                declarations.setdefault(
                    name, FlagDeclaration(name=name, file=path, lineno=lineno)
                )
    return declarations


def _is_referenced(declaration: FlagDeclaration, *, referenced: set[str]) -> bool:
    """Whether any reference root names this flag.

    Both spellings are already folded into ``referenced`` by
    :func:`_collect_referenced_names`; this is deliberately a plain membership
    test so there is exactly one place the two channels can be combined.
    """

    return declaration.name in referenced


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_dark_capabilities")
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Source root(s) to scan for capability flags (default: ai-backend src).",
    )
    args = parser.parse_args(argv)

    src_roots: tuple[Path, ...] = tuple(args.paths) if args.paths else DEFAULT_SRC_ROOTS

    declarations = _first_declarations(src_roots)
    referenced = _collect_referenced_names(REFERENCE_ROOTS, declarations=declarations)

    dark: list[DarkCapability] = [
        DarkCapability(declaration=declaration)
        for _name, declaration in sorted(declarations.items())
        if not _is_referenced(declaration, referenced=referenced)
    ]

    if not dark:
        sys.stdout.write(
            "OK: no dark capabilities "
            f"({len(declarations)} capability flag(s) scanned, all referenced by "
            "a test/e2e path or waived)\n"
        )
        return 0

    sys.stderr.write("FAIL: dark capability flags (off-by-default, no e2e path)\n")
    for cap in dark:
        sys.stderr.write(f"  {cap.render()}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
