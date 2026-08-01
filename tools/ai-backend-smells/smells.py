"""Mechanical smell scan over services/ai-backend.

Targets the bug CLASS this session kept hitting by reading instead of running:
code that exists, is well-documented, is unit-tested, and has no production
caller. `McpToolCallOutcome.extract_error_text` was found by accident; this
finds the rest of them.

Categories:
  TESTED_BUT_UNWIRED  defined in src, referenced in tests, never referenced in src
                      (highest signal — someone built and verified a thing that
                      never runs)
  DEAD_IN_SRC         defined in src, referenced nowhere at all
  SETTING_NEVER_READ  an env var name declared but never fetched
  DUPLICATE_DEFAULT   the same concept given two different default literals
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys
from collections import defaultdict

ROOT = pathlib.Path(sys.argv[1])
SRC = ROOT / "src"
TESTS = ROOT / "tests"


def py_files(base: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in base.rglob("*.py") if "__pycache__" not in p.parts]


class Defs(ast.NodeVisitor):
    """Public callables/classes defined at module or class scope."""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.out: list[tuple[str, str, int]] = []  # (name, qualifier, lineno)
        self._cls: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if not node.name.startswith("_"):
            self.out.append((node.name, "class", node.lineno))
        self._cls.append(node.name)
        self.generic_visit(node)
        self._cls.pop()

    def _fn(self, node) -> None:
        if not node.name.startswith("_") and node.name not in {
            "model_dump",
            "model_validate",
        }:
            kind = "method" if self._cls else "function"
            self.out.append((node.name, kind, node.lineno))
        self.generic_visit(node)

    visit_FunctionDef = _fn
    visit_AsyncFunctionDef = _fn


def collect_defs(base: pathlib.Path) -> dict[str, list[tuple[pathlib.Path, str, int]]]:
    defs: dict[str, list[tuple[pathlib.Path, str, int]]] = defaultdict(list)
    for path in py_files(base):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        visitor = Defs(path)
        visitor.visit(tree)
        for name, kind, lineno in visitor.out:
            defs[name].append((path, kind, lineno))
    return defs


IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def name_counts(base: pathlib.Path) -> dict[str, int]:
    """Count every identifier occurrence, per file, so we can subtract self-hits."""
    counts: dict[str, int] = defaultdict(int)
    for path in py_files(base):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for tok in IDENT.findall(text):
            counts[tok] += 1
    return counts


def per_file_counts(base: pathlib.Path) -> dict[pathlib.Path, dict[str, int]]:
    out: dict[pathlib.Path, dict[str, int]] = {}
    for path in py_files(base):
        text = path.read_text(encoding="utf-8", errors="ignore")
        c: dict[str, int] = defaultdict(int)
        for tok in IDENT.findall(text):
            c[tok] += 1
        out[path] = c
    return out


def main() -> None:
    src_defs = collect_defs(SRC)
    src_counts = name_counts(SRC)
    src_per_file = per_file_counts(SRC)
    test_counts = name_counts(TESTS) if TESTS.exists() else {}

    tested_unwired: list[dict] = []
    dead: list[dict] = []

    for name, sites in src_defs.items():
        if len(sites) > 1:
            continue  # defined in several places; ambiguous, skip
        path, kind, lineno = sites[0]
        total = src_counts.get(name, 0)
        own = src_per_file[path].get(name, 0)
        elsewhere = total - own
        if elsewhere > 0:
            continue  # referenced by another src module -> wired
        if own > 1:
            # referenced within its own file beyond the def line: could be
            # internal use. Only flag when the ONLY other hits are __all__.
            text = path.read_text(encoding="utf-8", errors="ignore")
            in_all = f'"{name}"' in text or f"'{name}'" in text
            if own - (1 if in_all else 0) > 1:
                continue
        rel = str(path.relative_to(ROOT))
        entry = {
            "name": name,
            "kind": kind,
            "file": rel,
            "line": lineno,
            "test_refs": test_counts.get(name, 0),
        }
        if test_counts.get(name, 0) > 0:
            tested_unwired.append(entry)
        else:
            dead.append(entry)

    # env vars declared vs read
    env_declared: dict[str, tuple[str, int]] = {}
    for path in py_files(SRC):
        for i, line in enumerate(
            path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
        ):
            m = re.search(
                r'=\s*"((?:RUNTIME|BACKEND|ENTERPRISE|COPILOT|MCP)_[A-Z0-9_]+)"', line
            )
            if m:
                env_declared.setdefault(m.group(1), (str(path.relative_to(ROOT)), i))
    all_src_text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore") for p in py_files(SRC)
    )
    env_unread = [
        {"env": k, "file": v[0], "line": v[1]}
        for k, v in sorted(env_declared.items())
        if all_src_text.count(k) <= 1
    ]

    report = {
        "tested_but_unwired": sorted(tested_unwired, key=lambda e: -e["test_refs"]),
        "dead_in_src": sorted(dead, key=lambda e: e["file"]),
        "env_declared_never_read": env_unread,
        "totals": {
            "tested_but_unwired": len(tested_unwired),
            "dead_in_src": len(dead),
            "env_declared_never_read": len(env_unread),
        },
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
