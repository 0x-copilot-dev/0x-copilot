"""Import-graph orphan detection over services/ai-backend/src.

A module is an ORPHAN when nothing in src imports it and it is not an entry
point. `tool_result_admission_gate.py` — a full admission gate with a ledger and
an offload writer, unit-tested, never imported — is the shape we are hunting.

Distinguishes:
  ORPHAN_TESTED    nothing in src imports it, tests do    -> built + verified + never runs
  ORPHAN_UNTESTED  nothing imports it anywhere            -> dead weight
Entry points (app/__main__/graph exports/conftest) are excluded by name.
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


def modules(base: pathlib.Path) -> dict[str, pathlib.Path]:
    out: dict[str, pathlib.Path] = {}
    for p in base.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(base).with_suffix("")
        out[".".join(rel.parts)] = p
    return out


def imported_names(path: pathlib.Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
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


def has_main_guard(path: pathlib.Path) -> bool:
    """True when the module has a top-level ``if __name__ == "__main__":`` guard.

    Such a module is a ``python -m`` entry point — a CLI, or a boot-time job the
    desktop supervisor spawns — not dead code, even when nothing in ``src``
    imports it. ``ENTRY_HINTS`` only catches entry points by leaf name; this
    catches the ones named anything else (``migrate``, ``*_cli``), which would
    otherwise scan as orphans. AST, not a text match, so the guard string in a
    docstring or comment does not count.
    """

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
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


def main() -> None:
    src_mods = modules(SRC)

    # Every module name imported by any src file.
    imported_by_src: set[str] = set()
    for p in src_mods.values():
        imported_by_src |= imported_names(p)

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
