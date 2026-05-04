"""CI guard: ``@reader`` methods must not contain write SQL keywords.

Walks the AST of every method decorated with ``@reader`` (or
``@_reader.reader``) under ``services/ai-backend/src/`` and refuses to
exit 0 if the method body contains a string constant matching
``INSERT|UPDATE|DELETE|TRUNCATE|MERGE``.

False-positive resistance: matching is on a whole-word regex, so a SQL
column name like ``last_update_at`` won't trip the check. The intent is
to catch accidental writes on the read-replica path, not to be a
linter.

Exit code: 0 on success, 1 on violation, 2 on usage error.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS: tuple[Path, ...] = (REPO_ROOT / "services" / "ai-backend" / "src",)

_WRITE_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|TRUNCATE|MERGE)\b", re.IGNORECASE
)


def _is_reader_decorator(node: ast.expr) -> bool:
    """Match ``@reader`` and ``@_reader.reader``."""

    if isinstance(node, ast.Name):
        return node.id == "reader"
    if isinstance(node, ast.Attribute):
        return node.attr == "reader"
    return False


def _string_literals(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Return every string constant inside the function body."""

    out: list[str] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out


def _find_violations(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(_is_reader_decorator(dec) for dec in node.decorator_list):
            continue
        for literal in _string_literals(node):
            if _WRITE_KEYWORDS.search(literal):
                try:
                    rel = path.relative_to(REPO_ROOT)
                except ValueError:
                    rel = path
                violations.append(
                    f"{rel}:{node.lineno}: "
                    f"@reader method '{node.name}' contains write SQL keyword"
                )
                break
    return violations


def main(argv: list[str] | None = None) -> int:
    del argv
    failures: list[str] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            failures.extend(_find_violations(path))
    if failures:
        sys.stderr.write("@reader violations:\n")
        for line in failures:
            sys.stderr.write(f"  {line}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
