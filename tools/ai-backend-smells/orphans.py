"""Import-graph orphan detection over services/ai-backend/src.

A module is an ORPHAN when nothing in src imports it and it is not an entry
point. `tool_result_admission_gate.py` — a full admission gate with a ledger and
an offload writer, unit-tested, never imported — is the shape we are hunting.

Distinguishes:
  ORPHAN_TESTED    nothing in src imports it, tests do    -> built + verified + never runs
  ORPHAN_UNTESTED  nothing imports it anywhere            -> dead weight
Entry points (app/__main__/graph exports/conftest) are excluded by name.

Two things this scan refuses to accept as evidence of use, because each one hid
real debt for months:

**A package ``__init__`` re-exporting its own submodule.** ``__init__`` is in
``ENTRY_HINTS``, so it is never an orphan candidate — yet its imports used to
count as reachability, which let a facade vouch for its own submodules forever
while nothing ever vouched for the facade. ``agent_runtime.persistence.schema``
survived exactly that way: its last outside importer left with the Postgres
backend, it became import-time BROKEN (it read a deleted migrations file at
module scope), and this scan stayed green. PENDING-WIRINGS.md had to record
``delegation.subagents``'s two unwired modules **by hand** for the same reason,
noting the blind spot and asking for this follow-up.

So a re-export is resolved, not trusted: ``from pkg.mod import Thing`` inside
``pkg/__init__.py`` records that ``pkg.Thing`` MEANS ``pkg.mod``, and only an
import from OUTSIDE the package spends that credit. The resolution is transitive,
because facades nest — ``from agent_runtime import X`` reaches
``agent_runtime.execution`` reaches ``agent_runtime.execution.factory``. An
``__init__`` re-exporting some OTHER package's symbol is an ordinary use and
still counts.

**A dotted string that merely looks like a module.** Resolving providers through
``importlib`` is real, late wiring and must count. A logger channel is not:
``_LOGGER_NAME = "runtime_worker.jobs.routine_scheduler"`` sitting in a
*different* module silently cleared a known-unwired 907-line job out of this
report — worse than missing an orphan, because it removed recorded debt from
view. Only a literal bound to a module-ish name, or handed to ``import_module``,
counts now. That keeps the two ``provider_module=`` registry entries and drops
eight logger names, four ``source_owner=``/``owner=`` provenance labels and an
argparse ``prog=``.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys

ROOT = pathlib.Path(sys.argv[1])
SRC = ROOT / "src"
TESTS = ROOT / "tests"

ENTRY_HINTS = {"__init__", "__main__", "app", "graph", "conftest", "settings"}

# A dotted literal counts as late wiring only when its binding promises a module.
# ``provider_module=`` qualifies; ``_LOGGER_NAME`` and ``source_owner=`` do not.
LAZY_IMPORT_HINT = "module"
IMPORT_CALLS = {"import_module", "load_module", "find_spec"}

INIT_SUFFIX = ".__init__"


def modules(base: pathlib.Path) -> dict[str, pathlib.Path]:
    out: dict[str, pathlib.Path] = {}
    for p in base.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(base).with_suffix("")
        out[".".join(rel.parts)] = p
    return out


def parsed(path: pathlib.Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None


def package_of(module: str) -> str | None:
    """The package a ``pkg.__init__`` module is the facade for, else None."""

    return module[: -len(INIT_SUFFIX)] if module.endswith(INIT_SUFFIX) else None


def is_inside(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def imported_names(path: pathlib.Path) -> set[str]:
    tree = parsed(path)
    if tree is None:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.add(node.module)
                for a in node.names:
                    found.add(f"{node.module}.{a.name}")
    return found


def reexports(path: pathlib.Path, package: str) -> dict[tuple[str, str], str]:
    """Map ``(package, exported_name) -> defining module`` for one facade.

    Only the package's OWN submodules are facade re-exports. A ``pkg/__init__.py``
    that pulls a symbol out of a different package is consuming that package for
    real, and is left alone so it still counts as a use.

    The alias is what outside code will write, so ``as`` bindings map from the
    alias — ``from pkg.mod import Thing as Other`` means ``pkg.Other``.
    """

    tree = parsed(path)
    if tree is None:
        return {}
    out: dict[tuple[str, str], str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not is_inside(node.module, package):
            continue
        for a in node.names:
            out[(package, a.asname or a.name)] = node.module
    return out


def through_facades(name: str, facades: dict[tuple[str, str], str]) -> set[str]:
    """Every module *name* really reaches, following re-exports transitively.

    ``from agent_runtime import RuntimeContract`` arrives here as
    ``agent_runtime.RuntimeContract`` and must credit
    ``agent_runtime.execution.contracts`` at the end of the chain, not just the
    top-level package. Iterative and cycle-safe: two packages re-exporting each
    other's names would otherwise recurse forever.
    """

    reached: set[str] = set()
    pending = [name]
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        head, _, leaf = current.rpartition(".")
        target = facades.get((head, leaf))
        if target is None:
            continue
        # The defining module is reached, and the SYMBOL keeps travelling: the
        # next facade re-exports it under its own package, so the chain is
        # ``outer.Thing`` -> ``outer.inner`` -> ``outer.inner.Thing`` ->
        # ``outer.inner.leaf``. Following only the module would stop one hop
        # short and report the leaf that actually defines Thing as an orphan.
        pending.append(target)
        pending.append(f"{target}.{leaf}")
    return reached


def lazy_import_literals(path: pathlib.Path) -> set[str]:
    """Dotted literals a registry really imports — not logger names or labels.

    A registry that resolves plugins by dotted path (``importlib.import_module``
    over a table of provider modules) imports its targets for real, but with a
    string the AST import walk cannot see. Treating those as orphans would be
    wrong: they are wired, just late.

    Matching every dotted literal was too loose in the one direction that costs
    debt visibility — it let a logger name in an unrelated module clear a
    genuinely unwired job. So the binding has to promise a module: a
    ``*module*`` keyword argument or assignment target, or an ``import_module``
    call argument. The caller still intersects with the real module set, so a
    literal that names nothing is ignored either way.
    """

    tree = parsed(path)
    if tree is None:
        return set()
    found: set[str] = set()

    def literal(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def promises_a_module(name: str | None) -> bool:
        return bool(name) and LAZY_IMPORT_HINT in name.lower()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee = node.func
            called = callee.attr if isinstance(callee, ast.Attribute) else None
            if called is None and isinstance(callee, ast.Name):
                called = callee.id
            if called in IMPORT_CALLS:
                for arg in node.args:
                    value = literal(arg)
                    if value:
                        found.add(value)
            for kw in node.keywords:
                if promises_a_module(kw.arg):
                    value = literal(kw.value)
                    if value:
                        found.add(value)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [
                t.id if isinstance(t, ast.Name) else getattr(t, "attr", None)
                for t in targets
            ]
            if any(promises_a_module(n) for n in names):
                value = literal(node.value) if node.value is not None else None
                if value:
                    found.add(value)
    return found


def has_main_guard(path: pathlib.Path) -> bool:
    """True when the module has a top-level ``if __name__ == "__main__":`` guard.

    Such a module is a ``python -m`` entry point — a CLI, or a boot-time job the
    desktop supervisor spawns — not dead code, even when nothing in ``src``
    imports it. ``ENTRY_HINTS`` only catches entry points by leaf name; this
    catches the ones named anything else (``migrate``, ``*_cli``), which would
    otherwise scan as orphans. AST, not a text match, so the guard string in a
    docstring or comment does not count.
    """

    tree = parsed(path)
    if tree is None:
        return False
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "__main__"
        ):
            return True
    return False


def reachable_modules(src_mods: dict[str, pathlib.Path]) -> set[str]:
    """Every module name reached by a real use, facades resolved not trusted."""

    facades: dict[tuple[str, str], str] = {}
    for mod, path in src_mods.items():
        package = package_of(mod)
        if package is not None:
            facades.update(reexports(path, package))

    reached: set[str] = set()
    lazy: set[str] = set()
    for mod, path in src_mods.items():
        package = package_of(mod)
        for name in imported_names(path):
            # A facade re-exporting its own submodule is not a consumer of it.
            if package is not None and is_inside(name, package):
                continue
            reached |= through_facades(name, facades)
        lazy |= lazy_import_literals(path)
    return reached | (lazy & set(src_mods))


def main() -> None:
    src_mods = modules(SRC)
    imported_by_src = reachable_modules(src_mods)

    test_text = ""
    if TESTS.exists():
        test_text = "\n".join(
            p.read_text(encoding="utf-8", errors="ignore")
            for p in TESTS.rglob("*.py")
            if "__pycache__" not in p.parts
        )

    orphan_tested: list[dict] = []
    orphan_untested: list[dict] = []

    for mod, path in sorted(src_mods.items()):
        leaf = mod.rsplit(".", 1)[-1]
        if leaf in ENTRY_HINTS:
            continue
        if mod in imported_by_src:
            continue
        # any src file importing a submodule of it, or naming it lazily?
        if any(other.startswith(mod + ".") for other in imported_by_src):
            continue
        # a ``python -m`` entry point is reachable off the import graph — a CLI
        # or a boot-time job — so it is not an orphan even when nothing imports it.
        if has_main_guard(path):
            continue
        # count real (non-docstring-ish) mentions of the leaf module name in src
        # excluding its own file
        others = [p for m, p in src_mods.items() if m != mod]
        mentions = sum(
            p.read_text(encoding="utf-8", errors="ignore").count(leaf) for p in others
        )
        entry = {
            "module": mod,
            "file": str(path.relative_to(ROOT)),
            "lines": len(
                path.read_text(encoding="utf-8", errors="ignore").splitlines()
            ),
            "src_mentions_of_leaf": mentions,
            "test_mentions": test_text.count(leaf),
        }
        if entry["test_mentions"] > 0:
            orphan_tested.append(entry)
        else:
            orphan_untested.append(entry)

    report = {
        "orphan_tested": sorted(orphan_tested, key=lambda e: -e["lines"]),
        "orphan_untested": sorted(orphan_untested, key=lambda e: -e["lines"]),
        "totals": {
            "orphan_tested": len(orphan_tested),
            "orphan_untested": len(orphan_untested),
            "src_modules": len(src_mods),
        },
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
