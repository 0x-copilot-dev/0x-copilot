"""CI guard: assert every audit-write site sits inside a ``transaction()`` block.

The C3 spec requires that every (primary write + audit append) pair in
``services/backend/src/backend_app/service.py`` is composed inside one
transaction so a partial failure rolls back both rows. This tool walks the
service module's AST and flags any ``self.store.append_*audit*(...)`` call
whose enclosing function does not also enter a ``self.store.transaction()``
context (or otherwise establish a ``conn.transaction()`` block).

Usage:
    python tools/check_audit_in_transaction.py [path ...]

When called without paths, defaults to the production service module. Exits
non-zero on the first violation; prints all violations.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_TARGETS: tuple[Path, ...] = (
    REPO_ROOT / "services" / "backend" / "src" / "backend_app" / "service.py",
)

_AUDIT_METHOD_PREFIXES: tuple[str, ...] = ("append_audit", "append_skill_audit")
_TRANSACTION_ATTR_NAMES: frozenset[str] = frozenset({"transaction"})


class _Violation:
    __slots__ = ("file", "function", "lineno", "message")

    def __init__(self, *, file: Path, function: str, lineno: int, message: str) -> None:
        self.file = file
        self.function = function
        self.lineno = lineno
        self.message = message

    def render(self) -> str:
        try:
            rel = self.file.relative_to(REPO_ROOT)
        except ValueError:
            rel = self.file
        return f"{rel}:{self.lineno}: {self.function}: {self.message}"


def _is_audit_call(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    return any(func.attr.startswith(prefix) for prefix in _AUDIT_METHOD_PREFIXES)


def _passes_conn_through(call: ast.Call) -> bool:
    """``self.store.append_audit(record, conn=conn)`` delegates the txn to its
    caller — the audit row lands on whatever connection the caller picks.
    Such helper-method calls are not violations; the *caller* of the helper
    is responsible for opening the transaction."""

    return any(kw.arg == "conn" for kw in call.keywords)


def _is_transaction_with_item(item: ast.withitem) -> bool:
    """Match ``X.transaction()`` and ``X.transaction(...).__enter__-ish`` forms."""

    expr = item.context_expr
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
        if expr.func.attr in _TRANSACTION_ATTR_NAMES:
            return True
    return False


class _FunctionAuditChecker(ast.NodeVisitor):
    """Within one function, walk descendants tracking whether we're inside a
    transaction block. Append any audit call seen outside such a block as a
    violation."""

    def __init__(self, *, file: Path, function_name: str) -> None:
        self._file = file
        self._function_name = function_name
        self._txn_depth = 0
        self.violations: list[_Violation] = []

    def visit_With(self, node: ast.With) -> None:  # noqa: N802 — ast API
        opens = sum(1 for item in node.items if _is_transaction_with_item(item))
        self._txn_depth += opens
        try:
            for child in node.body:
                self.visit(child)
        finally:
            self._txn_depth -= opens

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:  # noqa: N802
        opens = sum(1 for item in node.items if _is_transaction_with_item(item))
        self._txn_depth += opens
        try:
            for child in node.body:
                self.visit(child)
        finally:
            self._txn_depth -= opens

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if (
            _is_audit_call(node)
            and self._txn_depth == 0
            and not _passes_conn_through(node)
        ):
            self.violations.append(
                _Violation(
                    file=self._file,
                    function=self._function_name,
                    lineno=node.lineno,
                    message=(
                        "audit append must be inside a "
                        "``with store.transaction()`` block; otherwise the "
                        "primary write and audit row are not atomic (C3)."
                    ),
                )
            )
        self.generic_visit(node)


def _check_file(path: Path) -> list[_Violation]:
    if not path.is_file():
        return [
            _Violation(
                file=path,
                function="<file>",
                lineno=0,
                message="target file not found",
            )
        ]
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[_Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            checker = _FunctionAuditChecker(file=path, function_name=node.name)
            for child in node.body:
                checker.visit(child)
            violations.extend(checker.violations)
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_audit_in_transaction")
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Service module(s) to check (default: backend service.py).",
    )
    args = parser.parse_args(argv)

    targets: tuple[Path, ...] = tuple(args.paths) if args.paths else DEFAULT_TARGETS

    failures: list[_Violation] = []
    for target in targets:
        failures.extend(_check_file(target))

    if not failures:
        sys.stdout.write(
            f"OK: audit-in-transaction check passed ({len(targets)} file(s))\n"
        )
        return 0

    sys.stderr.write("FAIL: audit-in-transaction check\n")
    for violation in failures:
        sys.stderr.write(f"  {violation.render()}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
